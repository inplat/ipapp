import asyncio
from concurrent.futures import TimeoutError

import pytest
from async_timeout import timeout as atimeout

from ipapp import BaseApplication, BaseConfig
from ipapp.misc import rndstr
from ipapp.utils.lock import Lock, LockConfig


async def create_lock(redis_url: str) -> Lock:
    app = BaseApplication(BaseConfig())
    app.add(
        'lock',
        Lock(LockConfig(url=redis_url)),
    )
    await app.start()
    lock: Lock = app.get('lock')  # type: ignore
    return lock


async def test_lock_same_keys(redis_url: str):
    lock = await create_lock(redis_url)

    locks = []

    async def coro(key: str, sleep: float) -> None:
        async with lock(key):
            assert len(locks) == 0
            locks.append(key)
            await asyncio.sleep(sleep)
            locks.remove(key)
            assert len(locks) == 0

    key = rndstr()

    with atimeout(10):
        assert len(locks) == 0
        await asyncio.gather(coro(key, 1), coro(key, 2))
        assert len(locks) == 0


async def test_lock_diff_keys(redis_url: str):
    lock = await create_lock(redis_url)

    locks = []

    async def coro(key: str, sleep: float, end_lock_cnt: int):
        async with lock(key):
            locks.append(key)
            await asyncio.sleep(sleep)
            locks.remove(key)
            assert len(locks) == end_lock_cnt

    with atimeout(10):
        await asyncio.gather(coro('1', 1, 1), coro('2', 2, 0))
        assert len(locks) == 0


async def test_lock_timeout(redis_url: str):
    lock = await create_lock(redis_url)

    await lock.acquire('3')
    try:
        with pytest.raises(TimeoutError):
            await lock.acquire('3', 1)
    finally:
        await lock.release('3')
