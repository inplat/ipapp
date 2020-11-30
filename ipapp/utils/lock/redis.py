import asyncio
import time
from typing import Dict, List, Optional

from aioredis import Redis, create_redis
from aioredis.pubsub import Receiver

from ipapp.ctx import app
from ipapp.error import PrepareError

from .main import LockConfig, LockerInterface, masked_url

NO_WAIT = object()


class RedisLock(LockerInterface):
    def __init__(self, cfg: LockConfig) -> None:
        super().__init__(cfg)
        self.cfg = cfg
        self.redis_lock: Optional[Redis] = None
        self.redis_subscr: Optional[Redis] = None
        self.redis_connect_lock: asyncio.Lock = asyncio.Lock()
        self.redis_connect_subscr: asyncio.Lock = asyncio.Lock()

        self.mpsc: Optional[Receiver] = None
        self._reader_fut: Optional[asyncio.Future] = None
        self.waiters: Dict[str, List[asyncio.Future]] = {}
        self._ttl = int(self.cfg.max_lock_time * 1000)
        self._reader_err: Optional[Exception] = None
        self.encoding = 'UTF-8'

    async def start(self) -> None:
        await self._connect()
        if self.redis_lock is None or self.redis_subscr is None:
            raise UserWarning

    async def _connect(self) -> None:
        for i in range(self.cfg.connect_max_attempts):
            app.log_info("Connecting to %s", masked_url(self.cfg.url))
            try:
                await self._connect_lock()
                await self._connect_subscr()
                app.log_info("Connected to %s", masked_url(self.cfg.url))
                return
            except Exception as e:
                app.log_err(str(e))
                await asyncio.sleep(self.cfg.connect_retry_delay)
        raise PrepareError(
            "Could not connect to %s" % masked_url(self.cfg.url)
        )

    async def _connect_lock(self) -> None:
        async with self.redis_connect_lock:
            self.redis_lock = await create_redis(
                self.cfg.url,
                encoding=self.encoding,
            )

    async def _connect_subscr(self) -> None:
        async with self.redis_connect_subscr:
            self.redis_subscr = await create_redis(
                self.cfg.url,
                encoding=self.encoding,
            )
            self.mpsc = Receiver(loop=app.loop)
            await self.redis_subscr.subscribe(
                self.mpsc.channel(self.cfg.channel)
            )
            self._reader_fut = asyncio.ensure_future(self._reader(self.mpsc))

    async def _reader(self, mpsp: Receiver) -> None:
        reader_err: Optional[Exception] = None
        try:
            while True:
                _, msg = await mpsp.get()
                msg_str = msg.decode(self.encoding)
                if msg_str in self.waiters:
                    for fut in self.waiters[msg_str]:
                        fut.set_result(None)
        except Exception as err:
            reader_err = err
            app.log_err(err)

            while True:
                # сбрасываем все Future, т.к. они скорее всего не дождутся
                # поступления из канала, т.о. они будут
                # бороться за захват в реальном времени
                for wl in self.waiters.values():
                    for fut in wl:
                        if not fut.done():
                            fut.set_result(NO_WAIT)

                try:
                    await self._connect_subscr()
                    reader_err = None
                    return
                except Exception:
                    await asyncio.sleep(0.1)
        finally:
            # если не удалось переподключиться, то сохраняем ошибку
            # из-за которой все случилось
            self._reader_err = reader_err

    async def health(self) -> None:
        if self.redis_lock is None or self.redis_subscr is None:
            raise RuntimeError
        if self._reader_err:
            raise self._reader_err
        await self.redis_lock.get('none')

    async def acquire(self, key: str, timeout: Optional[float] = None) -> None:
        if self.redis_lock is None:  # pragma: no-cover
            raise UserWarning

        _timeout = timeout or self.cfg.default_timeout
        est = _timeout
        start_time = time.time()

        no_wait = False
        while True:

            if self.redis_lock.closed:
                await self._connect_lock()

            fut: asyncio.Future = asyncio.Future()
            if key not in self.waiters:
                self.waiters[key] = []
            self.waiters[key].append(fut)
            try:

                res = await self.redis_lock.execute(
                    'SET', key, 1, 'PX', self._ttl, 'NX'
                )
                if res is None:  # no acquired
                    if not no_wait:
                        res = await asyncio.wait_for(fut, timeout=est)
                        if res is NO_WAIT:
                            no_wait = True
                    else:
                        await asyncio.sleep(0.001)

                    est = _timeout - (time.time() - start_time)
                    if est <= 0:
                        raise asyncio.TimeoutError()
                else:
                    return
            except Exception:
                raise
            finally:
                self.waiters[key].remove(fut)
                if len(self.waiters[key]) == 0:
                    self.waiters.pop(key)

    async def release(self, key: str) -> None:
        if (
            self.redis_lock is None or self.redis_subscr is None
        ):  # pragma: no-cover
            raise UserWarning
        await self.redis_lock.delete(key)
        await self.redis_lock.publish(self.cfg.channel, key)
