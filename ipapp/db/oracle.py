"""
https://oracle.github.io/odpi/doc/installation.html#linux

https://cx-oracle.readthedocs.io/en/latest/user_guide/introduction.html

https://container-registry.oracle.com/pls/apex/f?p=113:4:103772846430850::NO:::
"""
import asyncio
from contextvars import Token
from typing import Any, Callable, List, Optional, Type, Union

import cx_Oracle
from pydantic.main import BaseModel

from ipapp.component import Component
from ipapp.error import PrepareError
from ipapp.logger import Span, wrap2span
from ipapp.misc import ctx_span_get, ctx_span_reset, ctx_span_set, json_encode

ConnFactory = Callable[['Oracle', cx_Oracle.Connection], 'Connection']


class OracleConfig(BaseModel):
    user: Optional[str]
    password: Optional[str]
    dsn: Optional[str]
    pool_min_size: int = 1
    pool_max_size: int = 10
    pool_increment: int = 1
    pool_max_lifetime_session: int = 0
    encoding: str = 'utf-8'
    connect_max_attempts: int = 10
    connect_retry_delay: float = 1.0
    log_query: bool = False
    log_result: bool = False


class OraSpan(Span):
    NAME_CONNECT = 'db::connect'
    NAME_DISCONNECT = 'db::disconnect'
    NAME_ACQUIRE = 'db::connection'
    NAME_XACT_COMMITED = 'db::xact (commited)'
    NAME_XACT_REVERTED = 'db::xact (reverted)'
    NAME_EXECUTE = 'db::execute'
    NAME_EXECUTEMANY = 'db::executemany'
    NAME_PREPARE = 'db::prepare'
    NAME_EXECUTE_PREPARED = 'db::execute_prepared'

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

    # ANN_PID = 'ora_pid'
    ANN_ACQUIRE = 'ora_conn'
    ANN_XACT_BEGIN = 'ora_xact_begin'
    ANN_XACT_END = 'ora_xact_end'
    # ANN_STMT_NAME = 'ora_stmt_name'
    ANN_QUERY = 'query'
    ANN_PARAMS = 'query_params'
    ANN_RESULT = 'result'

    def finish(
        self,
        ts: Optional[float] = None,
        exception: Optional[BaseException] = None,
    ) -> 'Span':
        if self.TAG_QUERY_NAME in self._tags:
            self.name += ' (' + str(self._tags.get(self.TAG_QUERY_NAME)) + ')'
        return super().finish(ts, exception)


class Oracle(Component):
    def __init__(
        self,
        cfg: OracleConfig,
        *,
        connection_factory: Optional[ConnFactory] = None,
    ):
        self.cfg = cfg
        self._pool: Optional[cx_Oracle.SessionPool] = None

        if connection_factory is None:
            connection_factory = self._def_conn_factory

        self._connection_factory: ConnFactory = connection_factory

    def _def_conn_factory(
        self, pg: 'Oracle', conn: 'cx_Oracle.Connection'
    ) -> 'Connection':
        return Connection(pg, conn)

    async def prepare(self) -> None:
        if self.app is None:  # pragma: no cover
            raise UserWarning('Unattached component')

        for i in range(self.cfg.connect_max_attempts):
            try:
                self.app.log_info("Connecting to %s", self.cfg.dsn)
                with wrap2span(
                    name=OraSpan.NAME_CONNECT,
                    kind=OraSpan.KIND_CLIENT,
                    app=self.app,
                ):
                    await self.app.loop.run_in_executor(None, self._connect)
                self.app.log_info("Connected to %s", self.cfg.dsn)
                return
            except Exception as e:
                self.app.log_err(str(e))
                await asyncio.sleep(self.cfg.connect_retry_delay)
        raise PrepareError("Could not connect to %s" % self.cfg.dsn)

    def _connect(self) -> None:
        self._pool = cx_Oracle.SessionPool(
            self.cfg.user,
            self.cfg.password,
            self.cfg.dsn,
            min=self.cfg.pool_min_size,
            max=self.cfg.pool_max_size,
            encoding=self.cfg.encoding,
            getmode=cx_Oracle.SPOOL_ATTRVAL_WAIT,
            maxLifetimeSession=self.cfg.pool_max_lifetime_session,
            sessionCallback=self._init_session,
            threaded=True,
        )

    def _init_session(
        self, connection: cx_Oracle.Connection, requestedTag: Optional[str]
    ) -> None:
        # cursor = connection.cursor()
        # cursor.execute('alter session set "_ORACLE_SCRIPT"=true')
        # cursor.close()
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        if self.app is None:  # pragma: no cover
            raise UserWarning('Unattached component')

        if self._pool:
            self.app.log_info("Disconnecting from %s" % self.cfg.dsn)
            await self.loop.run_in_executor(None, self._pool.close, True)

    def connection(
        self, acquire_timeout: Optional[float] = None
    ) -> 'ConnectionContextManager':
        return ConnectionContextManager(self, acquire_timeout=acquire_timeout)


class ConnectionContextManager:
    def __init__(
        self, db: Oracle, acquire_timeout: Optional[float] = None
    ) -> None:
        self._db = db
        self._conn: Optional[cx_Oracle.Connection] = None
        self._acquire_timeout = acquire_timeout
        self._pg_conn: Optional['Connection'] = None
        self._span: Optional[OraSpan] = None
        self._ctx_token: Optional[Token] = None

    async def __aenter__(self) -> 'Connection':
        if self._db is None or self._db._pool is None:  # noqa
            raise UserWarning
        pspan = ctx_span_get()

        if pspan is None:
            span: OraSpan = self._db.app.logger.span_new(  # type: ignore
                OraSpan.NAME_ACQUIRE, cls=OraSpan,
            )
        else:
            span = pspan.new_child(  # type: ignore
                OraSpan.NAME_ACQUIRE, cls=OraSpan,
            )
        self._span = span
        span.set_name4adapter(
            self._db.app.logger.ADAPTER_PROMETHEUS, OraSpan.P8S_NAME_ACQUIRE
        )
        self._ctx_token = ctx_span_set(span)
        span.start()
        try:
            # todo acquire_timeout
            self._conn = await self._db.loop.run_in_executor(
                None, self._db._pool.acquire
            )
            span.annotate(OraSpan.ANN_ACQUIRE, '')
        except BaseException as err:
            span.finish(exception=err)
            ctx_span_reset(self._ctx_token)
            raise
        else:
            return self._db._connection_factory(self._db, self._conn)

    async def __aexit__(
        self, exc_type: type, exc: BaseException, tb: type
    ) -> bool:
        if self._ctx_token is not None:
            ctx_span_reset(self._ctx_token)
        if self._span is not None:
            self._span.finish()
        if self._db._pool is None:
            raise UserWarning
        await self._db.loop.run_in_executor(
            None, self._db._pool.release, self._conn
        )  # noqa
        return False


class Connection:
    def __init__(self, db: Oracle, conn: cx_Oracle.Connection) -> None:
        self._db = db
        self._conn = conn
        self._lock = asyncio.Lock(loop=db.loop)

    async def execute(
        self,
        query: str,
        *args: Any,
        timeout: float = None,
        query_name: Optional[str] = None,
    ) -> str:
        with wrap2span(
            name=OraSpan.NAME_EXECUTE,
            kind=OraSpan.KIND_CLIENT,
            cls=OraSpan,
            app=self._db.app,
        ) as span:
            span.set_name4adapter(
                self._db.app.logger.ADAPTER_PROMETHEUS,
                OraSpan.P8S_NAME_EXECUTE,
            )
            if query_name is not None:
                span.tag(OraSpan.TAG_QUERY_NAME, query_name)
            async with self._lock:
                if self._db.cfg.log_query:
                    span.annotate(OraSpan.ANN_QUERY, query)
                    span.annotate4adapter(
                        self._db.app.logger.ADAPTER_ZIPKIN,
                        OraSpan.ANN_QUERY,
                        json_encode({'query': str(query)}),
                    )
                    span.annotate(OraSpan.ANN_PARAMS, repr(args))
                    span.annotate4adapter(
                        self._db.app.logger.ADAPTER_ZIPKIN,
                        OraSpan.ANN_PARAMS,
                        json_encode({'query_params': str(args)}),
                    )

                # todo timeout
                cur = await self._db.loop.run_in_executor(
                    None, self._conn.cursor
                )
                try:
                    res = await self._db.loop.run_in_executor(
                        None, cur.execute, query, *args
                    )
                    rowcount = res.rowcount
                finally:
                    await self._db.loop.run_in_executor(None, cur.close)

                if self._db.cfg.log_result:
                    span.annotate(OraSpan.ANN_RESULT, str(rowcount))
                    span.annotate4adapter(
                        self._db.app.logger.ADAPTER_ZIPKIN,
                        OraSpan.ANN_RESULT,
                        json_encode(
                            {'result': 'rowcount: %s' % str(rowcount)}
                        ),
                    )
                return rowcount

    async def query_one(
        self,
        query: str,
        *args: Any,
        timeout: float = None,
        query_name: Optional[str] = None,
        model_cls: Optional[Type[BaseModel]] = None,
    ) -> Optional[Union[dict, BaseModel]]:
        with wrap2span(
            name=OraSpan.NAME_EXECUTE,
            kind=OraSpan.KIND_CLIENT,
            cls=OraSpan,
            app=self._db.app,
        ) as span:
            span.set_name4adapter(
                self._db.app.logger.ADAPTER_PROMETHEUS,
                OraSpan.P8S_NAME_EXECUTE,
            )
            if query_name is not None:
                span.tag(OraSpan.TAG_QUERY_NAME, query_name)
            async with self._lock:

                if self._db.cfg.log_query:
                    span.annotate(OraSpan.ANN_QUERY, query)
                    span.annotate4adapter(
                        self._db.app.logger.ADAPTER_ZIPKIN,
                        OraSpan.ANN_QUERY,
                        json_encode({'query': str(query)}),
                    )
                    span.annotate(OraSpan.ANN_PARAMS, repr(args))
                    span.annotate4adapter(
                        self._db.app.logger.ADAPTER_ZIPKIN,
                        OraSpan.ANN_PARAMS,
                        json_encode({'query_params': str(args)}),
                    )

                cur = await self._db.loop.run_in_executor(
                    None, self._conn.cursor
                )
                try:
                    await self._db.loop.run_in_executor(
                        None, cur.execute, query, args
                    )
                    row = await self._db.loop.run_in_executor(
                        None, cur.fetchone
                    )

                    if row is not None:
                        column_names = [d[0].lower() for d in cur.description]
                        res: Optional[dict] = dict(zip(column_names, row))
                    else:
                        res = None

                finally:
                    await self._db.loop.run_in_executor(None, cur.close)

                if self._db.cfg.log_result:
                    _res = dict(res) if res is not None else None
                    span.annotate(OraSpan.ANN_RESULT, json_encode(_res))
                    span.annotate4adapter(
                        self._db.app.logger.ADAPTER_ZIPKIN,
                        OraSpan.ANN_RESULT,
                        json_encode({'result': repr(_res)}),
                    )
                if res is None:
                    return None
                if model_cls is not None:
                    return model_cls(**(dict(res)))
                else:
                    return res

    async def query_all(
        self,
        query: str,
        *args: Any,
        timeout: float = None,
        query_name: Optional[str] = None,
        model_cls: Optional[Type[BaseModel]] = None,
    ) -> List[Union[dict, BaseModel]]:
        with wrap2span(
            name=OraSpan.NAME_EXECUTE,
            kind=OraSpan.KIND_CLIENT,
            cls=OraSpan,
            app=self._db.app,
        ) as span:
            span.set_name4adapter(
                self._db.app.logger.ADAPTER_PROMETHEUS,
                OraSpan.P8S_NAME_EXECUTE,
            )
            if query_name is not None:
                span.tag(OraSpan.TAG_QUERY_NAME, query_name)
            async with self._lock:

                if self._db.cfg.log_query:
                    span.annotate(OraSpan.ANN_QUERY, query)
                    span.annotate4adapter(
                        self._db.app.logger.ADAPTER_ZIPKIN,
                        OraSpan.ANN_QUERY,
                        json_encode({'query': str(query)}),
                    )
                    span.annotate(OraSpan.ANN_PARAMS, repr(args))
                    span.annotate4adapter(
                        self._db.app.logger.ADAPTER_ZIPKIN,
                        OraSpan.ANN_PARAMS,
                        json_encode({'query_params': str(args)}),
                    )

                cur = await self._db.loop.run_in_executor(
                    None, self._conn.cursor
                )
                try:
                    await self._db.loop.run_in_executor(
                        None, cur.execute, query, args
                    )
                    rows = await self._db.loop.run_in_executor(
                        None, cur.fetchall
                    )

                    column_names = [d[0].lower() for d in cur.description]
                    res: List[Union[dict, BaseModel]] = []
                    for row in rows:
                        res.append(dict(zip(column_names, row)))

                finally:
                    await self._db.loop.run_in_executor(None, cur.close)

                if self._db.cfg.log_result:
                    res_dict = [dict(row) for row in res]
                    span.annotate(OraSpan.ANN_RESULT, json_encode(res_dict))
                    span.annotate4adapter(
                        self._db.app.logger.ADAPTER_ZIPKIN,
                        OraSpan.ANN_RESULT,
                        json_encode({'result': repr(res_dict)}),
                    )

                if model_cls is not None:
                    return [model_cls(**(dict(row))) for row in res]
                else:
                    return res
