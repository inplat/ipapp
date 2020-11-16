import asyncio
import socket
from asyncio import TimeoutError
from typing import Optional

import pytest
from async_timeout import timeout as atimeout

from ipapp import BaseApplication, BaseConfig
from ipapp.misc import rndstr
from ipapp.utils.lock import Lock, LockConfig
from ipapp.utils.lock.local import LocalLock


async def create_lock(url: Optional[str]) -> Lock:
    app = BaseApplication(BaseConfig())
    app.add(
        'lock',
        Lock(LockConfig(url=url)),
    )
    await app.start()
    lock: Lock = app.get('lock')  # type: ignore
    return lock


async def test_lock_redis_same_keys(redis_url: str):
    lock1 = await create_lock(redis_url)
    lock2 = await create_lock(redis_url)

    locks = []

    async def coro(lock, key: str, sleep: float) -> None:
        async with lock(key):
            assert len(locks) == 0
            locks.append(key)
            await asyncio.sleep(sleep)
            locks.remove(key)
            assert len(locks) == 0

    key = rndstr()

    with atimeout(10):
        assert len(locks) == 0
        await asyncio.gather(coro(lock1, key, 1), coro(lock2, key, 2))
        assert len(locks) == 0


async def test_lock_redis_diff_keys(redis_url: str):
    lock1 = await create_lock(redis_url)
    lock2 = await create_lock(redis_url)

    locks = []

    async def coro(lock, key: str, sleep: float, end_lock_cnt: int):
        async with lock(key):
            locks.append(key)
            await asyncio.sleep(sleep)
            locks.remove(key)
            assert len(locks) == end_lock_cnt

    with atimeout(10):
        await asyncio.gather(coro(lock1, '1', 1, 1), coro(lock2, '2', 2, 0))
        assert len(locks) == 0


async def test_lock_redis_timeout(redis_url: str):
    lock = await create_lock(redis_url)

    key = rndstr()

    await lock.acquire(key)
    try:
        with pytest.raises(TimeoutError):
            await lock.acquire(key, 1)
    finally:
        await lock.release(key)


async def test_lock_redis_connection_lost(redis_url: str):
    lock = await create_lock(redis_url)

    key = rndstr()

    lock._locker.redis_lock.connection._writer.transport._sock.shutdown(
        socket.SHUT_RDWR
    )

    with pytest.raises(Exception):
        await lock.acquire(key)
    assert lock._locker.redis_lock.connection.closed

    await lock.acquire(key)


async def test_lock_redis_connection_sub_lost(redis_url: str):
    lock = await create_lock(redis_url)

    lock._locker.redis_subscr.connection._writer.transport._sock.shutdown(
        socket.SHUT_RDWR
    )
    key = rndstr()

    async def coro():
        await lock.acquire(key, timeout=5)
        await asyncio.sleep(0.1)
        await lock.release(key)

    # with pytest.raises(TimeoutError)
    await asyncio.gather(coro(), coro())


async def test_lock_pg_same_keys(postgres_url: str):
    lock1 = await create_lock(postgres_url)
    lock2 = await create_lock(postgres_url)

    locks = []

    async def coro(lock, key: str, sleep: float) -> None:
        async with lock(key):
            assert len(locks) == 0
            locks.append(key)
            await asyncio.sleep(sleep)
            locks.remove(key)
            assert len(locks) == 0

    key = rndstr()

    with atimeout(10):
        assert len(locks) == 0
        await asyncio.gather(coro(lock1, key, 1), coro(lock2, key, 2))
        assert len(locks) == 0


async def test_lock_pg_diff_keys(postgres_url: str):
    lock1 = await create_lock(postgres_url)
    lock2 = await create_lock(postgres_url)

    locks = []

    async def coro(lock, key: str, sleep: float, end_lock_cnt: int):
        async with lock(key):
            locks.append(key)
            await asyncio.sleep(sleep)
            locks.remove(key)
            assert len(locks) == end_lock_cnt

    with atimeout(10):
        await asyncio.gather(coro(lock1, '1', 1, 1), coro(lock2, '2', 2, 0))
        assert len(locks) == 0


async def test_lock_pg_timeout(postgres_url: str):
    lock = await create_lock(postgres_url)

    await lock.acquire('3')
    try:
        with pytest.raises(TimeoutError):
            await lock.acquire('3', 1)
    finally:
        await lock.release('3')


async def test_local():
    lc = LocalLock(LockConfig())

    await lc.acquire('1', timeout=1)
    with pytest.raises(TimeoutError):
        await lc.acquire('1', timeout=1)
    await lc.release('1')

    await lc.acquire('1', timeout=1)
    await lc.acquire('2', timeout=1)


async def test_local_seq():
    lc = LocalLock(LockConfig())

    seq = []
    rnd = []

    async def coro(key, n, sleep):
        await asyncio.sleep(sleep)
        rnd.append(n)
        await lc.acquire(key, timeout=10)
        seq.append(n)
        await asyncio.sleep(sleep)
        seq.append(n)
        await lc.release(key)
        rnd.append(n)

    await asyncio.gather(
        coro('k', 1, 0.15),
        coro('k', 2, 0.23),
        coro('k', 3, 0.1),
        coro('k', 4, 0.11),
        coro('k', 5, 0.2),
        coro('k', 6, 0.25),
    )

    assert seq == [3, 3, 4, 4, 1, 1, 5, 5, 2, 2, 6, 6]
    assert rnd == [3, 4, 1, 5, 3, 2, 6, 4, 1, 5, 2, 6]


async def test_local_timeout():
    lock = await create_lock(None)

    async def coro():
        await lock.acquire('3', timeout=0.2)
        await asyncio.sleep(0.2)
        await lock.release('3')

    with pytest.raises(TimeoutError):
        await asyncio.gather(coro(), coro())
