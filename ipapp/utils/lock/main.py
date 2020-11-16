from typing import Optional

from pydantic import AnyUrl, BaseModel, Field

from ipapp import Component
from ipapp.misc import mask_url_pwd

# TODO add postgres support (pg_try_advisory_lock + listen/notify)


class AnyRedisUrl(AnyUrl):
    allowed_schemes = {'redis', 'rediss', 'postgres', 'postgresql'}


class LockConfig(BaseModel):
    url: Optional[AnyRedisUrl] = Field(
        None,
        description='Строка подключения к redis или postgres. '
        'Если не передано, то блокировка на уровне экземпляра приложения',
    )
    key_prefix: str = 'autopay__'
    channel: str = 'autopay_locks'
    default_timeout: float = 90.0
    max_lock_time: float = Field(
        600.0,
        description='Максимальное время жизни блокировки в секундах (только для Redis)',
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


class LockerInterface:

    cfg: LockConfig

    def __init__(self, cfg: LockConfig) -> None:
        pass

    async def start(self) -> None:  # pragma: no-cover
        pass

    async def stop(self) -> None:  # pragma: no-cover
        pass

    async def acquire(
        self, key: str, timeout: Optional[float] = None
    ) -> None:  # pragma: no-cover
        pass

    async def release(self, key: str) -> None:  # pragma: no-cover
        pass

    async def health(self) -> None:  # pragma: no-cover
        pass


class Lock(Component):
    def __init__(self, cfg: LockConfig) -> None:
        self.cfg = cfg
        self._locker: LockerInterface
        if cfg.url is None:
            from .local import LocalLock

            self._locker = LocalLock(cfg)
        elif cfg.url.startswith('redis'):
            from .redis import RedisLock

            self._locker = RedisLock(cfg)
        elif cfg.url.startswith('postgres'):
            from .pg import PostgresLock

            self._locker = PostgresLock(cfg)
        else:  # pragma: no-cover
            raise UserWarning

    async def prepare(self) -> None:
        await self._locker.start()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        await self._locker.stop()

    async def health(self) -> None:
        await self._locker.health()

    def __call__(
        self, key: str = '', timeout: Optional[float] = None
    ) -> 'LockCtx':
        return LockCtx(self, key, timeout)

    async def acquire(self, key: str, timeout: Optional[float] = None) -> None:
        await self._locker.acquire(key, timeout)

    async def release(self, key: str) -> None:
        await self._locker.release(key)


def masked_url(url: Optional[str]) -> Optional[str]:
    if url is not None:
        return mask_url_pwd(url)
    return None
