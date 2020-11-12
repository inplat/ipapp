import asyncio
import time
from typing import Dict, List, Optional

from aioredis import Redis, create_redis_pool
from aioredis.pubsub import Receiver
from pydantic import AnyUrl, BaseModel, Field

from ipapp import Component
from ipapp.error import PrepareError
from ipapp.misc import mask_url_pwd

# TODO add postgres support (pg_try_advisory_lock + listen/notify)


class AnyRedisUrl(AnyUrl):
    allowed_schemes = {'redis', 'rediss'}


class LockConfig(BaseModel):
    url: AnyRedisUrl = Field(
        'redis://127.0.0.1:6379/0', description='Строка подключения к redis'
    )
    encoding: str = 'UTF-8'
    key_prefix: str = 'autopay__'
    channel: str = 'autopay_locks'
    default_timeout: float = 90.0
    pool_minsize: int = 1
    pool_maxsize: int = 10
    max_lock_time: float = Field(
        600.0, description='Максимальное время жизни блокировки в секундах'
    )
    connect_max_attempts: int = Field(
        60,
        description=(
            "Максимальное количество попыток подключения к базе данных"
        ),
    )
    connect_retry_delay: float = Field(
        2.0,
        description=(
            "Задержка перед повторной попыткой подключения к базе данных"
        ),
    )


class LockCtx:
    def __init__(
        self, lock: 'Lock', key: str, timeout: Optional[float]
    ) -> None:
        self.lock = lock
        self.key = key
        self.timeout = timeout

    async def __aenter__(self) -> 'Lock':
        await self.lock.acquire(self.key, self.timeout)
        return self.lock

    async def __aexit__(
        self, exc_type: type, exc: BaseException, tb: type
    ) -> None:
        await self.lock.release(self.key)


class Lock(Component):
    def __init__(self, cfg: LockConfig) -> None:
        self.cfg = cfg
        self.redis: Optional[Redis] = None
        self.mpsc: Optional[Receiver] = None
        self._reader_fut: Optional[asyncio.Future] = None
        self.waiters: Dict[str, List[asyncio.Future]] = {}
        self._ttl = int(self.cfg.max_lock_time * 1000)

    async def prepare(self) -> None:
        await self._connect()
        if self.redis is None:
            raise UserWarning
        self.mpsc = Receiver(loop=self.app.loop)
        await self.redis.subscribe(self.mpsc.channel('locks'))
        self._reader_fut = asyncio.ensure_future(self._reader(self.mpsc))

    async def _connect(self) -> None:
        for i in range(self.cfg.connect_max_attempts):
            self.app.log_info("Connecting to %s", self._masked_url)
            try:
                self.redis = await create_redis_pool(
                    self.cfg.url,
                    minsize=self.cfg.pool_minsize,
                    maxsize=self.cfg.pool_maxsize,
                    encoding=self.cfg.encoding,
                )
                self.app.log_info("Connected to %s", self._masked_url)
                return
            except Exception as e:
                self.app.log_err(str(e))
                await asyncio.sleep(self.cfg.connect_retry_delay)
        raise PrepareError("Could not connect to %s" % self._masked_url)

    @property
    def _masked_url(self) -> Optional[str]:
        if self.cfg.url is not None:
            return mask_url_pwd(self.cfg.url)
        return None

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        if self.mpsc is not None:
            self.mpsc.stop()
        if self.redis is not None:
            self.redis.close()
            await self.redis.wait_closed()

    async def health(self) -> None:
        if self.redis is None:
            raise RuntimeError
        await self.redis.get('none')

    async def acquire(self, key: str, timeout: Optional[float] = None) -> None:
        if self.redis is None:  # pragma: no-cover
            raise UserWarning

        _timeout = timeout or self.cfg.default_timeout
        est = _timeout
        start_time = time.time()

        while True:
            fut: asyncio.Future = asyncio.Future()
            if key not in self.waiters:
                self.waiters[key] = []
            self.waiters[key].append(fut)
            try:
                res = await self.redis.execute(
                    'SET', key, 1, 'PX', self._ttl, 'NX'
                )
                if res is None:  # no acquired
                    await asyncio.wait_for(fut, timeout=est)
                    est = _timeout - (time.time() - start_time)
                else:
                    return
            finally:
                self.waiters[key].remove(fut)
                if len(self.waiters[key]) == 0:
                    self.waiters.pop(key)

    async def release(self, key: str) -> None:
        if self.redis is None:  # pragma: no-cover
            raise UserWarning
        await self.redis.delete(key)
        await self.redis.publish('locks', key)

    def __call__(
        self, key: str = '', timeout: Optional[float] = None
    ) -> 'LockCtx':
        return LockCtx(self, key, timeout)

    async def _reader(self, mpsp: Receiver) -> None:
        async for channel, msg in mpsp.iter():
            msg_str = msg.decode(self.cfg.encoding)
            if msg_str in self.waiters:
                for fut in self.waiters[msg_str]:
                    fut.set_result(None)
