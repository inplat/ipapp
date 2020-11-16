import asyncio
import time
from typing import Dict, List, Optional

import asyncpg
import asyncpg.pool

from ipapp.ctx import app
from ipapp.error import PrepareError

from .local import LocalLock
from .main import LockConfig, masked_url


class PostgresLock(LocalLock):
    def __init__(self, cfg: LockConfig) -> None:
        super().__init__(cfg)
        self.cfg = cfg
        self.pg: Optional[asyncpg.Connection] = None
        # self._reader_fut: Optional[asyncio.Future] = None
        self.waiters: Dict[str, List[asyncio.Future]] = {}
        self._ttl = int(self.cfg.max_lock_time * 1000)
        self._conn_lock = asyncio.Lock()

    async def start(self) -> None:
        await self._connect()
        if self.pg is None:
            raise UserWarning
        await self.pg.add_listener(self.cfg.channel, self._notification)
        # self.mpsc = Receiver(loop=app.loop)
        # await self.redis.subscribe(self.mpsc.channel('locks'))
        # self._reader_fut = asyncio.ensure_future(self._reader(self.mpsc))

    def _notification(
        self,
        connection: asyncpg.Connection,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        if payload in self.waiters:
            for fut in self.waiters[payload]:
                fut.set_result(None)

    async def _connect(self) -> None:
        for i in range(self.cfg.connect_max_attempts):
            app.log_info("Connecting to %s", masked_url(self.cfg.url))
            try:
                self.pg = await asyncpg.connect(self.cfg.url)
                self._conn_lock = asyncio.Lock()
                app.log_info("Connected to %s", masked_url(self.cfg.url))
                return
            except Exception as e:
                app.log_err(str(e))
                await asyncio.sleep(self.cfg.connect_retry_delay)
        raise PrepareError(
            "Could not connect to %s" % masked_url(self.cfg.url)
        )

    async def health(self) -> None:
        if self.pg is None:  # pragma: no-cover
            raise UserWarning

        async with self._conn_lock:
            await self.pg.execute('SELECT 1')

    async def acquire(self, key: str, timeout: Optional[float] = None) -> None:
        if self.pg is None:  # pragma: no-cover
            raise UserWarning
        _timeout = timeout or self.cfg.default_timeout
        est = _timeout
        start_time = time.time()

        # нужно захватывать бокировку в разрезе подключения к БД.
        # поскольку оно одно на все клбчи блокировки, надо делать
        # захват внутри приложения
        await super(PostgresLock, self).acquire(key, timeout)
        try:
            while True:
                fut: asyncio.Future = asyncio.Future()
                if key not in self.waiters:
                    self.waiters[key] = []
                self.waiters[key].append(fut)
                try:
                    if self.pg.is_closed():
                        await self._connect()

                    async with self._conn_lock:
                        row = await self.pg.fetchrow(
                            'SELECT pg_try_advisory_lock(hashtext($1)) as r',
                            key,
                        )
                    if row['r']:
                        return
                    await asyncio.wait_for(fut, timeout=est)
                    est = _timeout - (time.time() - start_time)
                finally:
                    self.waiters[key].remove(fut)
                    if len(self.waiters[key]) == 0:
                        self.waiters.pop(key)
        except Exception:
            await super(PostgresLock, self).release(key)

    async def release(self, key: str) -> None:
        try:
            if self.pg is None:  # pragma: no-cover
                raise UserWarning

            if self.pg.is_closed():
                await self._connect()
            async with self._conn_lock:
                await self.pg.fetchrow(
                    'SELECT pg_advisory_unlock(hashtext($1))', key
                )
                await self.pg.execute(
                    'SELECT pg_notify($1,$2)', self.cfg.channel, key
                )
        finally:
            await super(PostgresLock, self).release(key)
