import asyncio
import json
import time
from contextvars import Token
from typing import Any, Callable, Dict, List, Optional, Type, Union

import asyncpg
import asyncpg.pool
import asyncpg.prepared_stmt
import asyncpg.protocol
import asyncpg.transaction
from pydantic.main import BaseModel

from ipapp.component import Component
from ipapp.error import PrepareError
from ipapp.logger import Span, wrap2span
from ipapp.misc import json_encode, mask_url_pwd

from ..misc import ctx_span_get, ctx_span_reset, ctx_span_set

JsonType = Union[None, int, float, str, bool, List[Any], Dict[str, Any]]
ConnFactory = Callable[['Postgres', asyncpg.Connection], 'Connection']


class PostgresConfig(BaseModel):
    url: Optional[str]
    pool_min_size: int = 4
    pool_max_size: int = 12
    pool_max_queries: int = 50000
    pool_max_inactive_connection_lifetime: float = 300.0
    connect_max_attempts: int = 10
    connect_retry_delay: float = 1.0
    log_query: bool = False
    log_result: bool = False


class PgSpan(Span):
    NAME_CONNECT = 'db::connect'
    NAME_DISCONNECT = 'db::disconnect'
    NAME_ACQUIRE = 'db::connection'
    NAME_XACT_COMMITED = 'db::xact (commited)'
    NAME_XACT_REVERTED = 'db::xact (reverted)'
    NAME_EXECUTE = 'db::execute'
    NAME_EXECUTEMANY = 'db::executemany'
    NAME_PREPARE = 'db::prepare'
    NAME_EXECUTE_PREPARED = 'db::execute (prepared)'

    P8S_NAME_ACQUIRE = 'db_connection'
    P8S_NAME_XACT_COMMITED = 'db_xact_commited'
    P8S_NAME_XACT_REVERTED = 'db_xact_reverted'
    P8S_NAME_EXECUTE = 'db_execute'
    P8S_NAME_EXECUTEMANY = 'db_executemany'
    P8S_NAME_PREPARE = 'db_prepare'
    P8S_NAME_EXECUTE_PREPARED = 'db_execute_prepared'

    TAG_POOL_MAX_SIZE = 'db.pool.size'
    TAG_POOL_FREE_COUNT = 'db.pool.free'

    TAG_QUERY_NAME = 'db.query'

    ANN_PID = 'pg_pid'
    ANN_ACQUIRE = 'pg_conn'
    ANN_XACT_BEGIN = 'pg_xact_begin'
    ANN_XACT_END = 'pg_xact_end'
    ANN_STMT_NAME = 'pg_stmt_name'
    ANN_QUERY = 'query'
    ANN_RESULT = 'result'


class Postgres(Component):
    def __init__(
        self,
        cfg: PostgresConfig,
        *,
        connection_factory: Optional[ConnFactory] = None,
    ) -> None:
        self.cfg = cfg
        self._pool: Optional[asyncpg.pool.Pool] = None
        self._connections: List['Connection'] = []

        if connection_factory is None:
            connection_factory = self._def_conn_factory

        self._connection_factory: ConnFactory = connection_factory

    def _def_conn_factory(
        self, pg: 'Postgres', conn: 'asyncpg.Connection'
    ) -> 'Connection':
        return Connection(pg, conn)

    @property
    def pool(self) -> asyncpg.pool.Pool:
        if self._pool is None:  # pragma: no cover
            raise UserWarning
        return self._pool

    @property
    def _masked_url(self) -> Optional[str]:
        if self.cfg.url is not None:
            return mask_url_pwd(self.cfg.url)
        return None

    @wrap2span(name=PgSpan.NAME_CONNECT, kind=PgSpan.KIND_CLIENT)
    async def _connect(self) -> None:
        if self.app is None:  # pragma: no cover
            raise UserWarning('Unattached component')

        self.app.log_info("Connecting to %s", self._masked_url)
        self._pool = await asyncpg.create_pool(
            dsn=self.cfg.url,
            max_size=self.cfg.pool_max_size,
            min_size=self.cfg.pool_min_size,
            max_queries=self.cfg.pool_max_queries,
            max_inactive_connection_lifetime=(
                self.cfg.pool_max_inactive_connection_lifetime
            ),
            init=Postgres._conn_init,
            loop=self.app.loop,
        )
        self.app.log_info("Connected to %s", self._masked_url)

    @staticmethod
    async def _conn_init(conn: asyncpg.pool.PoolConnectionProxy) -> None:
        def _json_encoder(value: JsonType) -> str:
            return json.dumps(value)

        def _json_decoder(value: str) -> JsonType:
            return json.loads(value)

        await conn.set_type_codec(
            'json',
            encoder=_json_encoder,
            decoder=_json_decoder,
            schema='pg_catalog',
        )

        def _jsonb_encoder(value: JsonType) -> bytes:
            return b'\x01' + json.dumps(value).encode('utf-8')

        def _jsonb_decoder(value: bytes) -> JsonType:
            return json.loads(value[1:].decode('utf-8'))

        # Example was got from https://github.com/MagicStack/asyncpg/issues/140
        await conn.set_type_codec(
            'jsonb',
            encoder=_jsonb_encoder,
            decoder=_jsonb_decoder,
            schema='pg_catalog',
            format='binary',
        )

    async def prepare(self) -> None:
        if self.app is None:  # pragma: no cover
            raise UserWarning('Unattached component')

        for i in range(self.cfg.connect_max_attempts):
            try:
                await self._connect()
                return
            except Exception as e:
                self.app.log_err(str(e))
                await asyncio.sleep(self.cfg.connect_retry_delay)
        raise PrepareError("Could not connect to %s" % self._masked_url)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        if self.app is None:  # pragma: no cover
            raise UserWarning('Unattached component')

        stop_timeout = 60
        stop_start = time.time()

        xact_locks = [
            conn._xact_lock.acquire()
            for conn in self._connections
            if conn._xact_lock is not None
        ]
        if len(xact_locks) > 0:
            await asyncio.wait(
                xact_locks,
                loop=self.loop,
                timeout=max(stop_timeout - stop_start + stop_start, 0.001),
            )

        conn_locks = [
            conn._lock.acquire()
            for conn in self._connections
            if conn._lock is not None
        ]
        if len(conn_locks):
            await asyncio.wait(
                conn_locks,
                loop=self.loop,
                timeout=max(stop_timeout - stop_start + stop_start, 0.001),
            )

        if self._pool:
            self.app.log_info("Disconnecting from %s" % self._masked_url)
            await self._pool.close()

    def connection(
        self, acquire_timeout: Optional[float] = None
    ) -> 'ConnectionContextManager':
        return ConnectionContextManager(self, acquire_timeout=acquire_timeout)

    async def query_one(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
        query_name: Optional[str] = None,
        model_cls: Optional[Type[BaseModel]] = None,
    ) -> Optional[Union[asyncpg.Record, BaseModel]]:
        async with self.connection() as conn:
            res = await conn.query_one(
                query,
                *args,
                timeout=timeout,
                query_name=query_name,
                model_cls=model_cls,
            )
        return res

    async def query_all(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
        query_name: Optional[str] = None,
        model_cls: Optional[Type[BaseModel]] = None,
    ) -> List[Union[asyncpg.Record, BaseModel]]:
        async with self.connection() as conn:
            res = await conn.query_all(
                query,
                *args,
                timeout=timeout,
                query_name=query_name,
                model_cls=model_cls,
            )
        return res

    async def execute(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
        query_name: Optional[str] = None,
    ) -> str:
        async with self.connection() as conn:
            res = await conn.execute(
                query, *args, timeout=timeout, query_name=query_name
            )
        return res

    async def executemany(
        self,
        query: str,
        args: Any,
        timeout: Optional[float] = None,
        query_name: Optional[str] = None,
    ) -> str:
        async with self.connection() as conn:
            res = await conn.executemany(
                query, args, timeout=timeout, query_name=query_name
            )
        return res

    async def health(self) -> None:
        async with self.connection() as conn:
            await conn.execute('SELECT 1', query_name='health')


class ConnectionContextManager:
    def __init__(
        self, db: Postgres, acquire_timeout: Optional[float] = None
    ) -> None:
        self._db = db
        self._conn: Optional[asyncpg.Connection] = None
        self._acquire_timeout = acquire_timeout
        self._pg_conn: Optional['Connection'] = None
        self._span: Optional[PgSpan] = None
        self._ctx_token: Optional[Token] = None

    async def __aenter__(self) -> 'Connection':
        if self._db is None or self._db._pool is None:  # noqa
            raise UserWarning
        pspan = ctx_span_get()

        if pspan is None:
            span: PgSpan = self._db.app.logger.span_new(  # type: ignore
                PgSpan.NAME_ACQUIRE, cls=PgSpan,
            )
        else:
            span = pspan.new_child(  # type: ignore
                PgSpan.NAME_ACQUIRE, cls=PgSpan,
            )
        self._span = span
        span.set_name4adapter(
            self._db.app.logger.ADAPTER_PROMETHEUS, PgSpan.P8S_NAME_ACQUIRE
        )
        self._ctx_token = ctx_span_set(span)
        span.start()
        try:
            self._conn = await self._db._pool.acquire(  # noqa
                timeout=self._acquire_timeout
            )
            span.annotate(PgSpan.ANN_ACQUIRE, '')
        except BaseException as err:
            span.finish(exception=err)
            ctx_span_reset(self._ctx_token)
            raise
        else:
            pool_queue = self._db._pool._queue  # noqa
            pg_conn = self._conn._con  # noqa
            pid = pg_conn.get_server_pid()
            span.tag(PgSpan.TAG_POOL_MAX_SIZE, pool_queue.maxsize)
            span.tag(PgSpan.TAG_POOL_FREE_COUNT, pool_queue.qsize())
            span.annotate(PgSpan.ANN_PID, pid)
            span.annotate4adapter(
                self._db.app.logger.ADAPTER_ZIPKIN,
                PgSpan.ANN_PID,
                json.dumps({'pid': str(pid)}),
            )

            self._pg_conn = self._db._connection_factory(self._db, self._conn)
            self._db._connections.append(self._pg_conn)  # noqa
            return self._pg_conn

    async def __aexit__(
        self, exc_type: type, exc: BaseException, tb: type
    ) -> bool:
        if (
            self._conn is None
            or self._db is None  # noqa
            or self._db._pool is None
        ):  # noqa
            raise UserWarning
        if self._ctx_token is not None:
            ctx_span_reset(self._ctx_token)
        if self._span is not None:
            self._span.finish()
        if self._pg_conn is not None:
            self._db._connections.remove(self._pg_conn)  # noqa
        await self._db._pool.release(self._conn)  # noqa
        return False


class TransactionContextManager:
    def __init__(
        self,
        conn: 'Connection',
        isolation_level: str = None,
        readonly: bool = False,
        deferrable: bool = False,
        xact_lock: asyncio.Lock = None,
    ) -> None:
        self._conn = conn
        if isolation_level is None:
            self._isolation_level = 'read_committed'
        else:
            self._isolation_level = isolation_level.lower().replace(' ', '_')
        self._readonly = readonly
        self._deferrable = deferrable
        self._xact_lock = xact_lock
        self._tr: Optional[asyncpg.transaction.Transaction] = None
        self._span: Optional[PgSpan] = None
        self._ctx_token: Optional[Token] = None

    async def __aenter__(self) -> 'asyncpg.transaction.Transaction':
        if self._conn.in_transaction:
            raise UserWarning('Transaction already started')

        pspan = ctx_span_get()
        if pspan is None:  # pragma: no cover
            raise UserWarning
        span: PgSpan = pspan.new_child(cls=PgSpan)  # type: ignore
        self._span = span
        self._ctx_token = ctx_span_set(span)
        span.start()

        try:
            span.annotate(PgSpan.ANN_PID, self._conn.pid)
            span.annotate4adapter(
                self._conn._db.app.logger.ADAPTER_ZIPKIN,
                PgSpan.ANN_PID,
                json.dumps({'pid': str(self._conn.pid)}),
            )

            if self._xact_lock is not None:
                await self._xact_lock.acquire()

            with await self._conn._lock:
                self._tr = self._conn._conn.transaction(
                    isolation=self._isolation_level,
                    readonly=self._readonly,
                    deferrable=self._deferrable,
                )
                await self._tr.__aenter__()
                span.annotate(PgSpan.ANN_XACT_BEGIN, 'BEGIN')
        except BaseException as err:
            span.finish(exception=err)
            ctx_span_reset(self._ctx_token)
            raise
        return self._tr

    async def __aexit__(
        self, exc_type: type, exc: BaseException, tb: type
    ) -> bool:
        if self._span is not None:
            if exc_type is not None:
                self._span.name = PgSpan.NAME_XACT_REVERTED
                self._span.set_name4adapter(
                    self._conn._db.app.logger.ADAPTER_PROMETHEUS,
                    PgSpan.P8S_NAME_XACT_REVERTED,
                )
            else:
                self._span.name = PgSpan.NAME_XACT_COMMITED
                self._span.set_name4adapter(
                    self._conn._db.app.logger.ADAPTER_PROMETHEUS,
                    PgSpan.P8S_NAME_XACT_COMMITED,
                )

        with await self._conn._lock:
            if self._span is not None:
                self._span.annotate(
                    PgSpan.ANN_XACT_END,
                    'ROLLBACK' if exc_type is not None else 'COMMIT',
                )
            if self._tr is None:  # pragma: no cover
                raise UserWarning
            await self._tr.__aexit__(exc_type, exc, tb)
            self._tr = None

            if self._xact_lock is not None:
                self._xact_lock.release()

        if self._conn is None:  # pragma: no cover
            raise UserWarning
        if self._ctx_token is not None:
            ctx_span_reset(self._ctx_token)
        if self._span is not None:
            self._span.finish()

        return False


class PreparedStatement:
    def __init__(
        self,
        conn: 'Connection',
        pg_stmt: asyncpg.prepared_stmt.PreparedStatement,
        stmt_name: str,
        query_name: Optional[str] = None,
    ):
        self._conn = conn
        self._pg_stmt = pg_stmt
        self._query_name = query_name
        self.stmt_name = stmt_name

    @wrap2span(
        name=PgSpan.NAME_EXECUTE_PREPARED, kind=PgSpan.KIND_CLIENT, cls=PgSpan
    )
    async def query_one(
        self, *args: Any, timeout: float = None
    ) -> asyncpg.Record:
        span: PgSpan = ctx_span_get()  # type: ignore
        span.set_name4adapter(
            self._conn._db.app.logger.ADAPTER_PROMETHEUS,
            PgSpan.P8S_NAME_EXECUTE_PREPARED,
        )
        span.annotate(PgSpan.ANN_PID, self._conn.pid)
        span.annotate4adapter(
            self._conn._db.app.logger.ADAPTER_ZIPKIN,
            PgSpan.ANN_PID,
            json.dumps({'pid': str(self._conn.pid)}),
        )
        span.annotate(PgSpan.ANN_STMT_NAME, self.stmt_name)
        span.annotate4adapter(
            self._conn._db.app.logger.ADAPTER_ZIPKIN,
            PgSpan.ANN_STMT_NAME,
            json.dumps({'statement_name': self.stmt_name}),
        )
        if self._query_name is not None:
            span.tag(PgSpan.TAG_QUERY_NAME, self._query_name)
        with await self._conn._lock:
            res = await self._pg_stmt.fetchrow(*args, timeout=timeout)

            if self._conn._db.cfg.log_result:
                _res = dict(res) if res is not None else None
                span.annotate(PgSpan.ANN_RESULT, json_encode(_res))
                span.annotate4adapter(
                    self._conn._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_RESULT,
                    json_encode({'result': repr(_res)}),
                )

            return res

    @wrap2span(
        name=PgSpan.NAME_EXECUTE_PREPARED, kind=PgSpan.KIND_CLIENT, cls=PgSpan
    )
    async def query_all(
        self, *args: Any, timeout: float = None
    ) -> List[asyncpg.Record]:
        span: PgSpan = ctx_span_get()  # type: ignore
        span.set_name4adapter(
            self._conn._db.app.logger.ADAPTER_PROMETHEUS,
            PgSpan.P8S_NAME_EXECUTE_PREPARED,
        )
        span.annotate(PgSpan.ANN_PID, self._conn.pid)
        span.annotate4adapter(
            self._conn._db.app.logger.ADAPTER_ZIPKIN,
            PgSpan.ANN_PID,
            json.dumps({'pid': str(self._conn.pid)}),
        )
        span.annotate(PgSpan.ANN_STMT_NAME, self.stmt_name)
        span.annotate4adapter(
            self._conn._db.app.logger.ADAPTER_ZIPKIN,
            PgSpan.ANN_STMT_NAME,
            json.dumps({'statement_name': self.stmt_name}),
        )
        if self._query_name is not None:
            span.tag(PgSpan.TAG_QUERY_NAME, self._query_name)
        with await self._conn._lock:
            res = await self._pg_stmt.fetch(*args, timeout=timeout)

            if self._conn._db.cfg.log_result:
                res_dict = [dict(row) for row in res]
                span.annotate(PgSpan.ANN_RESULT, json_encode(res_dict))
                span.annotate4adapter(
                    self._conn._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_RESULT,
                    json_encode({'result': repr(res_dict)}),
                )

            return res


class Connection:
    def __init__(self, db: Postgres, conn: asyncpg.Connection) -> None:
        self._db = db
        self._conn = conn
        self._pid = conn.get_server_pid()
        self._lock = asyncio.Lock(loop=db.loop)
        self._xact_lock = asyncio.Lock(loop=db.loop)

    @property
    def in_transaction(self) -> bool:
        return self._conn.is_in_transaction()

    @property
    def pid(self) -> int:
        return self._pid

    def xact(
        self,
        isolation_level: str = None,
        readonly: bool = False,
        deferrable: bool = False,
    ) -> 'TransactionContextManager':
        return TransactionContextManager(
            self, isolation_level, readonly, deferrable, self._xact_lock
        )

    @wrap2span(name=PgSpan.NAME_EXECUTE, kind=PgSpan.KIND_CLIENT, cls=PgSpan)
    async def execute(
        self,
        query: str,
        *args: Any,
        timeout: float = None,
        query_name: Optional[str] = None,
    ) -> str:
        span: PgSpan = ctx_span_get()  # type: ignore
        span.set_name4adapter(
            self._db.app.logger.ADAPTER_PROMETHEUS, PgSpan.P8S_NAME_EXECUTE
        )
        if query_name is not None:
            span.tag(PgSpan.TAG_QUERY_NAME, query_name)
        with await self._lock:
            span.annotate(PgSpan.ANN_PID, self.pid)
            span.annotate4adapter(
                self._db.app.logger.ADAPTER_ZIPKIN,
                PgSpan.ANN_PID,
                json.dumps({'pid': str(self.pid)}),
            )

            if self._db.cfg.log_query:
                span.annotate(PgSpan.ANN_QUERY, query)
                span.annotate4adapter(
                    self._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_QUERY,
                    json.dumps({'query': str(query)}),
                )
            res = await self._conn.execute(query, *args, timeout=timeout)
            if self._db.cfg.log_result:
                span.annotate(PgSpan.ANN_RESULT, str(res))
                span.annotate4adapter(
                    self._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_RESULT,
                    json_encode({'result': str(res)}),
                )
            return res

    @wrap2span(
        name=PgSpan.NAME_EXECUTEMANY, kind=PgSpan.KIND_CLIENT, cls=PgSpan
    )
    async def executemany(
        self,
        query: str,
        args: Any,
        timeout: float = None,
        query_name: Optional[str] = None,
    ) -> str:
        span: PgSpan = ctx_span_get()  # type: ignore
        span.set_name4adapter(
            self._db.app.logger.ADAPTER_PROMETHEUS, PgSpan.P8S_NAME_EXECUTEMANY
        )
        if query_name is not None:
            span.tag(PgSpan.TAG_QUERY_NAME, query_name)
        with await self._lock:
            span.annotate(PgSpan.ANN_PID, self.pid)
            span.annotate4adapter(
                self._db.app.logger.ADAPTER_ZIPKIN,
                PgSpan.ANN_PID,
                json.dumps({'pid': str(self.pid)}),
            )

            if self._db.cfg.log_query:
                span.annotate(PgSpan.ANN_QUERY, query)
                span.annotate4adapter(
                    self._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_QUERY,
                    json.dumps({'query': str(query)}),
                )
            res = await self._conn.executemany(query, args, timeout=timeout)
            if self._db.cfg.log_result:
                span.annotate(PgSpan.ANN_RESULT, str(res))
                span.annotate4adapter(
                    self._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_RESULT,
                    json_encode({'result': str(res)}),
                )
            return res

    @wrap2span(name=PgSpan.NAME_EXECUTE, kind=PgSpan.KIND_CLIENT, cls=PgSpan)
    async def query_one(
        self,
        query: str,
        *args: Any,
        timeout: float = None,
        query_name: Optional[str] = None,
        model_cls: Optional[Type[BaseModel]] = None,
    ) -> Optional[Union[asyncpg.Record, BaseModel]]:
        span: PgSpan = ctx_span_get()  # type: ignore
        span.set_name4adapter(
            self._db.app.logger.ADAPTER_PROMETHEUS, PgSpan.P8S_NAME_EXECUTE
        )
        if query_name is not None:
            span.tag(PgSpan.TAG_QUERY_NAME, query_name)
        with await self._lock:
            span.annotate(PgSpan.ANN_PID, self.pid)
            span.annotate4adapter(
                self._db.app.logger.ADAPTER_ZIPKIN,
                PgSpan.ANN_PID,
                json.dumps({'pid': str(self.pid)}),
            )
            if self._db.cfg.log_query:
                span.annotate(PgSpan.ANN_QUERY, query)
                span.annotate4adapter(
                    self._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_QUERY,
                    json.dumps({'query': str(query)}),
                )
            res = await self._conn.fetchrow(query, *args, timeout=timeout)
            if self._db.cfg.log_result:
                _res = dict(res) if res is not None else None
                span.annotate(PgSpan.ANN_RESULT, json_encode(_res))
                span.annotate4adapter(
                    self._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_RESULT,
                    json_encode({'result': repr(_res)}),
                )
            if res is None:
                return None
            if model_cls is not None:
                return model_cls(**(dict(res)))
            else:
                return res

    @wrap2span(name=PgSpan.NAME_EXECUTE, kind=PgSpan.KIND_CLIENT, cls=PgSpan)
    async def query_all(
        self,
        query: str,
        *args: Any,
        timeout: float = None,
        query_name: Optional[str] = None,
        model_cls: Optional[Type[BaseModel]] = None,
    ) -> List[Union[asyncpg.Record, BaseModel]]:
        span: PgSpan = ctx_span_get()  # type: ignore
        span.set_name4adapter(
            self._db.app.logger.ADAPTER_PROMETHEUS, PgSpan.P8S_NAME_EXECUTE
        )
        if query_name is not None:
            span.tag(PgSpan.TAG_QUERY_NAME, query_name)
        with await self._lock:
            span.annotate(PgSpan.ANN_PID, self.pid)
            span.annotate4adapter(
                self._db.app.logger.ADAPTER_ZIPKIN,
                PgSpan.ANN_PID,
                json.dumps({'pid': str(self.pid)}),
            )

            if self._db.cfg.log_query:
                span.annotate(PgSpan.ANN_QUERY, query)
                span.annotate4adapter(
                    self._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_QUERY,
                    json.dumps({'query': str(query)}),
                )
            res = await self._conn.fetch(query, *args, timeout=timeout)
            if self._db.cfg.log_result:
                res_dict = [dict(row) for row in res]
                span.annotate(PgSpan.ANN_RESULT, json_encode(res_dict))
                span.annotate4adapter(
                    self._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_RESULT,
                    json_encode({'result': repr(res_dict)}),
                )

            if model_cls is not None:
                return [model_cls(**(dict(row))) for row in res]
            else:
                return res

    @wrap2span(name=PgSpan.NAME_PREPARE, kind=PgSpan.KIND_CLIENT, cls=PgSpan)
    async def prepare(
        self,
        query: str,
        timeout: float = None,
        query_name: Optional[str] = None,
    ) -> PreparedStatement:
        span: PgSpan = ctx_span_get()  # type: ignore
        span.set_name4adapter(
            self._db.app.logger.ADAPTER_PROMETHEUS, PgSpan.P8S_NAME_PREPARE
        )
        if query_name is not None:
            span.tag(PgSpan.TAG_QUERY_NAME, query_name)
        with await self._lock:
            span.annotate(PgSpan.ANN_PID, self.pid)
            span.annotate4adapter(
                self._db.app.logger.ADAPTER_ZIPKIN,
                PgSpan.ANN_PID,
                json.dumps({'pid': str(self.pid)}),
            )
            if self._db.cfg.log_query:
                span.annotate(PgSpan.ANN_QUERY, query)
                span.annotate4adapter(
                    self._db.app.logger.ADAPTER_ZIPKIN,
                    PgSpan.ANN_QUERY,
                    json.dumps({'query': str(query)}),
                )
            pg_stmt = await self._conn.prepare(query, timeout=timeout)
            stmt_name = pg_stmt._state.name
            stmt = PreparedStatement(self, pg_stmt, stmt_name, query_name)
            span.annotate(PgSpan.ANN_STMT_NAME, stmt_name)

            span.annotate4adapter(
                self._db.app.logger.ADAPTER_ZIPKIN,
                PgSpan.ANN_STMT_NAME,
                json.dumps({'statement_name': stmt_name}),
            )

            return stmt
