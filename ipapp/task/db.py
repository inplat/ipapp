import asyncio
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    AsyncContextManager,
    Callable,
    List,
    Optional,
    Tuple,
    Union,
)

import asyncpg
from pydantic import BaseModel, Field

from ipapp import Component
from ipapp.ctx import span
from ipapp.db.pg import Postgres
from ipapp.error import PrepareError
from ipapp.logger import Span, wrap2span
from ipapp.misc import json_encode as default_json_encoder
from ipapp.misc import mask_url_pwd
from ipapp.rpc.main import Executor

TaskHandler = Union[Callable, str]
ETA = Union[datetime, float, int]

STATUS_PENDING = 'pending'
STATUS_IN_PROGRESS = 'in_progress'
STATUS_SUCCESSFUL = 'successful'
STATUS_ERROR = 'error'
STATUS_RETRY = 'retry'
STATUS_CANCELED = 'canceled'


CREATE_TABLE_QUERY = """\
CREATE TYPE {schema}.task_status AS ENUM
   ('pending',
    'progress',
    'successful',
    'error',
    'retry',
    'canceled');

CREATE SEQUENCE {schema}.task_id_seq;

CREATE TABLE {schema}.task
(
  id bigint NOT NULL DEFAULT nextval('{schema}.task_id_seq'::regclass),
  reference text,
  eta timestamp with time zone NOT NULL DEFAULT now(),
  name text NOT NULL,
  params jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  max_retries integer NOT NULL DEFAULT 0,
  retry_delay interval NOT NULL DEFAULT '00:01:00'::interval,
  status {schema}.task_status NOT NULL,
  last_stamp timestamp with time zone NOT NULL DEFAULT now(),
  retries integer,
  CONSTRAINT task_pkey PRIMARY KEY (id),
  CONSTRAINT task_empty_table_check CHECK (false) NO INHERIT,
  CONSTRAINT task_max_retries_check CHECK (max_retries >= 0),
  CONSTRAINT task_params_check CHECK (jsonb_typeof(params) = 'object'::text)
);

CREATE TABLE {schema}.task_pending
(
  id bigint NOT NULL DEFAULT nextval('{schema}.task_id_seq'::regclass),
  status {schema}.task_status NOT NULL DEFAULT 'pending'::{schema}.task_status,
  CONSTRAINT task_pending_pkey PRIMARY KEY (id),
  CONSTRAINT task_max_retries_check CHECK (max_retries >= 0),
  CONSTRAINT task_params_check CHECK (jsonb_typeof(params) = 'object'::text),
  CONSTRAINT task_pending_status_check CHECK (status = ANY (ARRAY[
      'pending'::{schema}.task_status,
      'retry'::{schema}.task_status,
      'progress'::{schema}.task_status]))
)
INHERITS ({schema}.task);

CREATE TABLE {schema}.task_arch
(
  id bigint NOT NULL DEFAULT nextval('{schema}.task_id_seq'::regclass),
  status {schema}.task_status NOT NULL
      DEFAULT 'canceled'::{schema}.task_status,
  CONSTRAINT task_arch_pkey PRIMARY KEY (id),
  CONSTRAINT task_max_retries_check CHECK (max_retries >= 0),
  CONSTRAINT task_params_check CHECK (jsonb_typeof(params) = 'object'::text),
  CONSTRAINT task_pending_status_check CHECK (status <> ALL (ARRAY[
      'pending'::{schema}.task_status,
      'retry'::{schema}.task_status,
      'progress'::{schema}.task_status]))
)
INHERITS ({schema}.task);

CREATE TABLE {schema}.task_log
(
  id bigserial NOT NULL,
  task_id bigint NOT NULL,
  eta timestamp with time zone NOT NULL,
  started timestamp with time zone,
  finished timestamp with time zone,
  result jsonb,
  error text,
  error_cls text,
  traceback text,
  CONSTRAINT task_log_pkey PRIMARY KEY (id)
);

CREATE INDEX task_pending_eta_idx
  ON {schema}.task_pending
  USING btree
  (eta)
  WHERE status = ANY (ARRAY['pending'::{schema}.task_status,
                            'retry'::{schema}.task_status]);

CREATE INDEX task_pending_reference_idx
  ON {schema}.task_pending
  USING btree
  (reference);

CREATE INDEX task_arch_reference_idx
  ON {schema}.task_arch
  USING btree
  (reference);

CREATE INDEX task_log_task_id_idx
  ON {schema}.task_log
  USING btree
  (task_id);
"""


class Task(BaseModel):
    id: int
    eta: datetime
    name: str
    params: dict
    max_retries: int
    retry_delay: timedelta
    status: str
    retries: Optional[int]


class TaskManagerSpan(Span):
    NAME_SCHEDULE = 'dbtm::schedule'
    NAME_SCAN = 'dbtm::scan'
    NAME_EXEC = 'dbtm::exec'

    TAG_PARENT_TRACE_ID = 'dbtm.parent_trace_id'
    TAG_TASK_ID = 'dbtm.task_id'
    TAG_TASK_NAME = 'dbtm.task_name'

    ANN_ETA = 'eta'
    ANN_DELAY = 'delay'
    ANN_NEXT_SCAN = 'next_scan'
    ANN_TASKS = 'tasks'


class Retry(Exception):
    def __init__(self, err: Exception) -> None:
        self.err = err

    def __str__(self) -> str:
        return 'Retry: %r' % (str(self.err) or repr(self.err))


class TaskManagerConfig(BaseModel):
    db_url: Optional[str] = Field(
        None,
        description="Строка подключения к базе данных",
        example="postgresql://own@localhost:5432/main",
    )
    db_schema: str = Field("main", description="Название схемы в базе данных")
    db_connect_max_attempts: int = Field(
        10,
        description=(
            "Максимальное количество попыток подключения к базе данных"
        ),
    )
    db_connect_retry_delay: float = Field(
        1.0,
        description=(
            "Задержка перед повторной попыткой подключения к базе данных"
        ),
    )
    batch_size: int = Field(
        1, description="Количество задач, которое берется в работу за раз"
    )
    max_scan_interval: float = Field(
        60.0, description="Максимальный интервал для поиска новых задач"
    )
    idle: bool = Field(
        False, description="Пока включено задачи не берутся в работу"
    )


class TaskManager(Component):
    def __init__(self, api: object, cfg: TaskManagerConfig) -> None:
        self._executor: Executor = Executor(api)
        self.cfg = cfg
        self._stopping = False
        self._scan_fut: Optional[asyncio.Future] = None
        self.stamp_early: float = 0.0
        self._lock: Optional[asyncio.Lock] = None
        self._db: Optional[Db] = None

    @property
    def _masked_url(self) -> Optional[str]:
        if self.cfg.db_url is not None:
            return mask_url_pwd(self.cfg.db_url)
        return None

    async def prepare(self) -> None:
        if self.app is None:  # pragma: no cover
            raise UserWarning('Unattached component')

        self._lock = asyncio.Lock(loop=self.loop)

        self._db = Db(self, self.cfg)
        await self._db.init()

    async def start(self) -> None:
        if self.app is None:  # pragma: no cover
            raise UserWarning('Unattached component')

        if not self.cfg.idle:
            self._scan_fut = asyncio.ensure_future(
                self._scan(), loop=self.loop
            )

    async def stop(self) -> None:
        self._stopping = True
        if self._lock is None:
            return
        await self._lock.acquire()
        if self._scan_fut is not None:
            if not self._scan_fut.done():
                await self._scan_fut

    async def health(self) -> None:
        if self._db is not None:
            await self._db.health(lock=True)

    async def schedule(
        self,
        func: TaskHandler,
        params: dict,
        reference: Optional[str] = None,
        eta: Optional[ETA] = None,
        max_retries: int = 0,
        retry_delay: float = 60.0,
    ) -> int:
        with wrap2span(
            name=TaskManagerSpan.NAME_SCHEDULE,
            kind=Span.KIND_CLIENT,
            cls=TaskManagerSpan,
            app=self.app,
        ) as span:
            if self._db is None:  # pragma: no cover
                raise UserWarning

            if not isinstance(func, str):
                if not hasattr(func, '__rpc_name__'):  # pragma: no cover
                    raise UserWarning('Invalid task handler')
                func_name = getattr(func, '__rpc_name__')
            else:
                func_name = func

            span.name = '%s::%s' % (TaskManagerSpan.NAME_SCHEDULE, func_name)

            eta_dt: Optional[datetime] = None
            if isinstance(eta, int) or isinstance(eta, float):
                eta_dt = datetime.fromtimestamp(eta, tz=timezone.utc)
            elif isinstance(eta, datetime):
                eta_dt = eta
            elif eta is not None:  # pragma: no cover
                raise UserWarning

            if eta_dt is not None:
                span.annotate(
                    TaskManagerSpan.ANN_ETA, 'ETA: %s' % eta_dt.isoformat()
                )

            task_id, task_delay = await self._db.task_add(
                eta_dt,
                func_name,
                params,
                reference,
                max_retries,
                retry_delay,
                lock=True,
            )

            span.annotate(TaskManagerSpan.ANN_DELAY, 'Delay: %s' % task_delay)

            eta_float = self.loop.time() + task_delay
            self.stamp_early = eta_float
            self.loop.call_at(eta_float, self._scan_later, eta_float)

            return task_id

    async def cancel(self, task_id: int) -> bool:
        if self._db is None or self._lock is None:  # pragma: no cover
            raise UserWarning
        with await self._lock:
            async with self._db.transaction():
                if await self._db.task_search4cancel(task_id, lock=False):
                    await self._db.task_move_arch(
                        task_id, STATUS_CANCELED, None, lock=False
                    )
                    return True
                return False

    async def _scan(self) -> List[int]:
        with wrap2span(
            name=TaskManagerSpan.NAME_SCAN,
            kind=Span.KIND_SERVER,
            ignore_ctx=True,
            cls=TaskManagerSpan,
            app=self.app,
        ) as span:
            if self.app is None or self._lock is None:  # pragma: no cover
                raise UserWarning
            if self._stopping:
                return []

            async with self._lock:
                delay = 1.0  # default: 1 second
                try:
                    tasks, delay = await self._search_and_exec()
                    if len(tasks) == 0:
                        span.skip()
                    return [task.id for task in tasks]
                except Exception as err:
                    span.error(err)
                    self.app.log_err(err)
                finally:
                    self._scan_fut = None
                    if not self._stopping:
                        span.annotate(
                            TaskManagerSpan.ANN_NEXT_SCAN, 'next: %s' % delay
                        )
                        eta = self.loop.time() + delay
                        self.stamp_early = eta
                        self.loop.call_at(eta, self._scan_later, eta)
                return []

    def _scan_later(self, when: float) -> None:
        if self._db is None:  # pragma: no cover
            raise UserWarning
        if when != self.stamp_early:
            return
        if self._db is None:  # pragma: no cover
            raise UserWarning
        if self._stopping:
            return
        if not self.cfg.idle:
            self._scan_fut = asyncio.ensure_future(
                self._scan(), loop=self.loop
            )

    async def _search_and_exec(self) -> Tuple[List[Task], float]:
        if self._db is None:  # pragma: no cover
            raise UserWarning
        async with self._db.transaction():

            tasks = await self._db.task_search(self.cfg.batch_size, lock=False)
            span.annotate(TaskManagerSpan.ANN_TASKS, repr(tasks))
            if len(tasks) == 0:
                next_delay = await self._db.task_next_delay(lock=False)
                if (
                    next_delay is None
                    or next_delay >= self.cfg.max_scan_interval
                ):
                    return tasks, self.cfg.max_scan_interval
                if next_delay <= 0:
                    return tasks, 0
                return tasks, next_delay

        coros = [self._exec(span.trace_id, task) for task in tasks]
        await asyncio.gather(*coros, loop=self.loop)

        return tasks, 0

    async def _exec(self, parent_trace_id: str, task: Task) -> None:
        with wrap2span(
            name=TaskManagerSpan.NAME_EXEC,
            kind=Span.KIND_SERVER,
            ignore_ctx=True,
            cls=TaskManagerSpan,
            app=self.app,
        ) as span:
            if self._db is None or self._executor is None:  # pragma: no cover
                raise UserWarning

            span.name = '%s::%s' % (TaskManagerSpan.NAME_EXEC, task.name)
            span.tag(TaskManagerSpan.TAG_PARENT_TRACE_ID, parent_trace_id)
            span.tag(TaskManagerSpan.TAG_TASK_ID, task.id)
            span.tag(TaskManagerSpan.TAG_TASK_NAME, task.name)
            try:
                err: Optional[Exception] = None
                err_str: Optional[str] = None
                err_trace: Optional[str] = None
                res: Any = None
                time_begin = time.time()
                try:
                    res = await self._executor.exec(
                        task.name, kwargs=task.params
                    )
                except Exception as e:
                    err = e
                    if isinstance(err, Retry):
                        err_str = str(err.err)
                    else:
                        err_str = str(err)
                    err_trace = traceback.format_exc()
                    span.error(err)
                    self.app.log_err(err)
                time_finish = time.time()

                await self._db.task_log_add(
                    task.id,
                    task.eta,
                    time_begin,
                    time_finish,
                    res,
                    err_str,
                    err_trace,
                    lock=True,
                )

                if task.retries is None:
                    retries = 0
                else:
                    retries = task.retries + 1

                if err is not None:
                    if isinstance(err, Retry):
                        if retries >= task.max_retries:
                            await self._db.task_move_arch(
                                task.id, STATUS_ERROR, retries, lock=True
                            )
                        else:
                            await self._db.task_retry(
                                task.id,
                                retries,
                                task.retry_delay.total_seconds(),
                                lock=True,
                            )
                    else:
                        await self._db.task_move_arch(
                            task.id, STATUS_ERROR, retries, lock=True
                        )
                else:
                    await self._db.task_move_arch(
                        task.id, STATUS_SUCCESSFUL, retries, lock=True
                    )
            except Exception as err:
                span.error(err)
                self.app.log_err(err)
                raise


class Db:
    def __init__(self, tm: TaskManager, cfg: TaskManagerConfig) -> None:
        self._lock = asyncio.Lock()
        self._tm = tm
        self._cfg = cfg
        self._conn: Optional[asyncpg.Connection] = None

    async def init(self) -> None:
        try:
            await self.get_conn(can_reconnect=True)
        except Exception as err:
            raise PrepareError(str(err))

    @property
    def _masked_url(self) -> Optional[str]:
        if self._cfg.db_url is not None:
            return mask_url_pwd(self._cfg.db_url)
        return None

    async def get_conn(self, can_reconnect: bool) -> asyncpg.Connection:
        if self._conn is not None and not self._conn.is_closed():
            return self._conn
        if not can_reconnect:
            raise Exception('Not connected to %s' % self._masked_url)
        for _ in range(self._cfg.db_connect_max_attempts):
            try:
                self._tm.app.logger.app.log_info(
                    "Connecting to %s", self._masked_url
                )
                self._conn = await asyncpg.connect(self._cfg.db_url)
                await Postgres._conn_init(self._conn)  # noqa
                self._tm.app.logger.app.log_info(
                    "Connected to %s", self._masked_url
                )
                return self._conn
            except Exception as err:
                self._tm.app.logger.app.log_err(err)
                await asyncio.sleep(
                    self._cfg.db_connect_retry_delay,
                    loop=self._tm.app.logger.app.loop,
                )
        raise Exception("Could not connect to %s" % self._masked_url)

    async def _fetch(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
        lock: bool = False,
    ) -> List[asyncpg.Record]:
        conn = await self.get_conn(can_reconnect=lock)
        if lock:
            async with self._lock:
                if conn.is_in_transaction():  # pragma: no cover
                    raise UserWarning
                return await conn.fetch(query, *args, timeout=timeout)
        else:
            return await conn.fetch(query, *args, timeout=timeout)

    async def _fetchrow(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
        lock: bool = False,
    ) -> Optional[asyncpg.Record]:
        conn = await self.get_conn(can_reconnect=lock)
        if lock:
            async with self._lock:
                if conn.is_in_transaction():  # pragma: no cover
                    raise UserWarning
                return await conn.fetchrow(query, *args, timeout=timeout)
        else:
            return await conn.fetchrow(query, *args, timeout=timeout)

    async def _execute(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
        lock: bool = False,
    ) -> None:
        conn = await self.get_conn(can_reconnect=lock)
        if lock:
            async with self._lock:
                if conn.is_in_transaction():  # pragma: no cover
                    raise UserWarning
                await conn.execute(query, *args, timeout=timeout)
        else:
            await conn.execute(query, *args, timeout=timeout)

    @asynccontextmanager  # type: ignore
    async def transaction(
        self,
        isolation: str = 'read_committed',
        readonly: bool = False,
        deferrable: bool = False,
    ) -> AsyncContextManager['Db']:
        conn = await self.get_conn(can_reconnect=True)
        async with self._lock:
            async with conn.transaction(
                isolation=isolation, readonly=readonly, deferrable=deferrable
            ):
                yield self

    async def task_add(
        self,
        eta: Optional[datetime],
        name: str,
        params: dict,
        reference: Optional[str],
        max_retries: int,
        retry_delay: float,
        *,
        lock: bool = False,
    ) -> Tuple[int, float]:
        query = (  # nosec
            "INSERT INTO %s.task_pending"
            "(eta,name,params,reference,max_retries,retry_delay) "
            "VALUES(COALESCE($1, NOW()),$2,$3,$4,$5,"
            "make_interval(secs=>$6::float)) "
            "RETURNING id, "
            "greatest(extract(epoch from eta-NOW()), 0) as delay"
        ) % self._cfg.db_schema

        res = await self._fetchrow(
            query,
            eta,
            name,
            params,
            reference,
            max_retries,
            retry_delay,
            lock=lock,
        )
        if res is None:  # pragma: no cover
            raise UserWarning
        return res['id'], res['delay']

    async def task_search(
        self, batch_size: int, *, lock: bool = False
    ) -> List[Task]:
        query = (  # nosec
            "UPDATE %s.task_pending SET status='progress',last_stamp=NOW() "
            "WHERE id IN ("
            "SELECT id FROM %s.task_pending "
            "WHERE eta<NOW() AND "
            "status=ANY(ARRAY['pending'::%s.task_status,"
            "'retry'::%s.task_status])"
            "LIMIT $1 FOR UPDATE SKIP LOCKED) "
            "RETURNING "
            "id,eta,name,params,max_retries,retry_delay,status,retries"
        ) % (
            self._cfg.db_schema,
            self._cfg.db_schema,
            self._cfg.db_schema,
            self._cfg.db_schema,
        )

        res = await self._fetch(query, batch_size, lock=lock)

        return [Task(**dict(row)) for row in res]

    async def task_search4cancel(
        self, task_id: int, *, lock: bool = False
    ) -> Optional[bool]:
        query = (  # nosec
            "UPDATE %s.task_pending SET status='progress',last_stamp=NOW() "
            "WHERE id=$1 AND status IN ('retry','pending') "
            "RETURNING "
            "id"
        ) % (self._cfg.db_schema,)
        res = await self._fetchrow(query, task_id, lock=lock)
        return res is not None

    async def task_next_delay(self, *, lock: bool = False) -> Optional[float]:
        query = (  # nosec
            "SELECT EXTRACT(EPOCH FROM eta-NOW())t "
            "FROM %s.task_pending "
            "WHERE "
            "status=ANY(ARRAY['pending'::%s.task_status,"
            "'retry'::%s.task_status])"
            "ORDER BY eta "
            "LIMIT 1 "
            "FOR SHARE SKIP LOCKED"
        ) % (self._cfg.db_schema, self._cfg.db_schema, self._cfg.db_schema)
        res = await self._fetchrow(query, lock=lock)
        if res:
            return res['t']
        return None

    async def task_retry(
        self,
        task_id: int,
        retries: int,
        eta_delay: Optional[float],
        *,
        lock: bool = False,
    ) -> None:
        query = (  # nosec
            'UPDATE %s.task_pending SET status=$2,retries=$3,'
            'eta=COALESCE(NOW()+make_interval(secs=>$4::float),eta),'
            'last_stamp=NOW() '
            'WHERE id=$1'
        ) % self._cfg.db_schema

        await self._execute(
            query, task_id, STATUS_RETRY, retries, eta_delay, lock=lock
        )

    async def task_move_arch(
        self,
        task_id: int,
        status: str,
        retries: Optional[int],
        *,
        lock: bool = False,
    ) -> None:
        query = (  # nosec
            'WITH del AS (DELETE FROM %s.task_pending WHERE id=$1 '
            'RETURNING id,eta,name,params,max_retries,retry_delay,'
            'retries,reference)'
            'INSERT INTO %s.task_arch'
            '(id,eta,name,params,max_retries,retry_delay,status,'
            'retries,last_stamp,reference)'
            'SELECT '
            'id,eta,name,params,max_retries,retry_delay,$2,'
            'COALESCE($3,retries),NOW(),reference '
            'FROM del'
        ) % (self._cfg.db_schema, self._cfg.db_schema)
        await self._execute(query, task_id, status, retries, lock=lock)

    async def task_log_add(
        self,
        task_id: int,
        eta: datetime,
        started: float,
        finished: float,
        result: Any,
        error: Optional[str],
        trace: Optional[str],
        *,
        lock: bool = False,
    ) -> None:
        query = (  # nosec
            'INSERT INTO %s.task_log'
            '(task_id,eta,started,finished,result,error,traceback)'
            'VALUES($1,$2,to_timestamp($3),to_timestamp($4),'
            '$5::text::jsonb,$6,$7)'
        ) % self._cfg.db_schema
        js = default_json_encoder(result) if result is not None else None

        await self._execute(
            query, task_id, eta, started, finished, js, error, trace, lock=lock
        )

    async def health(self, *, lock: bool = False) -> None:
        await self._execute('SELECT 1', lock=lock)
