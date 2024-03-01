import asyncio
import time
from typing import Dict, List, Optional

import redis.asyncio as redis  # type: ignore

from ipapp.ctx import app
from ipapp.error import PrepareError

from .main import LockConfig, LockerInterface, masked_url

NO_WAIT = object()


class RedisLock(LockerInterface):

    def __init__(self, cfg: LockConfig) -> None:
        super().__init__(cfg)
        self.cfg = cfg
        self.redis_lock = redis.from_url(
            self.cfg.url,
            encoding='utf-8',
            single_connection_client=self.cfg.single_connection_client,
        )
        self.redis_subscr = redis.from_url(
            self.cfg.url,
            encoding='utf-8',
            single_connection_client=self.cfg.single_connection_client,
        )

        self.pubsub = self.redis_subscr.pubsub(ignore_subscribe_messages=True)
        self.waiters: Dict[str, List[asyncio.Future]] = {}
        self._ttl = int(self.cfg.max_lock_time * 1000)

    async def start(self) -> None:
        for i in range(self.cfg.connect_max_attempts):
            app.log_info("Connecting to %s", masked_url(self.cfg.url))
            try:
                await self.redis_lock.ping()
                await self.redis_subscr.ping()
                app.log_info("Connected to %s", masked_url(self.cfg.url))
                break
            except Exception as e:
                app.log_err(str(e))
                await asyncio.sleep(self.cfg.connect_retry_delay)
        else:
            raise PrepareError(
                "Could not connect to %s" % masked_url(self.cfg.url)
            )

        await self.pubsub.subscribe(self.cfg.channel)
        asyncio.create_task(self._reader(self.pubsub))

    async def stop(self) -> None:
        if self.redis_subscr:
            if self.pubsub:
                await self.pubsub.unsubscribe(self.cfg.channel)
                await self.pubsub.aclose()
            await self.redis_subscr.aclose()
        if self.redis_lock:
            await self.redis_lock.aclose()

    async def _reader(self, channel: redis.client.PubSub) -> None:
        try:
            async for message in channel.listen():
                if message is not None:
                    key = message['data'].decode()
                    if key in self.waiters:
                        for fut in self.waiters[key]:
                            fut.set_result(None)
        except Exception as err:
            app.log_err(err)
            # сбрасываем все Future, т.к. они скорее всего не дождутся
            # поступления из канала, т.о. они будут
            # бороться за захват в реальном времени
            for wl in self.waiters.values():
                for fut in wl:
                    if not fut.done():
                        fut.set_result(NO_WAIT)

    async def health(self) -> None:
        self.redis_lock.hello()
        if self.redis_lock is None or self.redis_subscr is None:
            raise RuntimeError
        await self.redis_lock.get('none')

    async def acquire(self, key: str, timeout: Optional[float] = None) -> None:
        if self.redis_lock is None:  # pragma: no-cover
            raise UserWarning

        _timeout = timeout or self.cfg.default_timeout
        est = _timeout
        start_time = time.time()

        no_wait = False
        while True:
            fut: asyncio.Future = asyncio.Future()
            if key not in self.waiters:
                self.waiters[key] = []
            self.waiters[key].append(fut)
            try:

                res = await self.redis_lock.execute_command(
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
        await self.redis_lock.delete(key)
        await self.redis_lock.publish(self.cfg.channel, key)
