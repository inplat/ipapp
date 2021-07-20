from __future__ import annotations

import asyncio
import traceback
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import time
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    Union,
)

import asyncpg
import asyncpg.exceptions
import pytz
from crontab import CronTab
from pydantic import BaseModel, Field

from ipapp import Component
from ipapp.ctx import span
from ipapp.db.pg import Postgres
from ipapp.error import PrepareError
from ipapp.logger import Span, wrap2span
from ipapp.misc import json_encode as default_json_encoder
from ipapp.misc import mask_url_pwd
from ipapp.rpc.error import RpcError
from ipapp.rpc.main import Executor, RpcRegistry

TaskHandler = Union[Callable, str]
ETA = Union[datetime, float, int]

STATUS_PENDING = 'pending'
STATUS_IN_PROGRESS = 'in_progress'
STATUS_SUCCESSFUL = 'successful'
STATUS_ERROR = 'error'
STATUS_RETRY = 'retry'
STATUS_CANCELED = 'canceled'


CREATE_TABLE_QUERY = """\


CREATE SCHEMA IF NOT EXISTS {schema};

DO $$
BEGIN
    IF NOT EXISTS (
      SELECT 1 FROM pg_type t
      LEFT JOIN pg_namespace p ON t.typnamespace=p.oid
      WHERE t.typname='task_status' AND p.nspname='{schema}'
    ) THEN
        CREATE TYPE {schema}.task_status AS ENUM
       ('pending',
        'progress',
        'successful',
        'error',
        'retry',
        'canceled');
    END IF;
END
$$;



CREATE SEQUENCE IF NOT EXISTS {schema}.task_id_seq;

CREATE TABLE IF NOT EXISTS {schema}.task
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
  trace_id character varying(32),
  trace_span_id character varying(16),
  CONSTRAINT task_pkey PRIMARY KEY (id),
  CONSTRAINT task_empty_table_check CHECK (false) NO INHERIT,
  CONSTRAINT task_max_retries_check CHECK (max_retries >= 0),
  CONSTRAINT task_params_check CHECK (jsonb_typeof(params) = 'object'::text)
);

CREATE TABLE IF NOT EXISTS {schema}.task_pending
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

CREATE TABLE IF NOT EXISTS {schema}.task_arch
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

CREATE TABLE IF NOT EXISTS {schema}.task_log
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

CREATE TABLE IF NOT EXISTS {schema}.task_cron_tick (
  id integer NOT NULL,
  last_stamp timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT task_cron_tick_pkey PRIMARY KEY (id),
  CONSTRAINT task_cron_tick_pkey_check CHECK (id=0)
);

CREATE INDEX IF NOT EXISTS task_pending_eta_idx
  ON {schema}.task_pending
  USING btree
  (eta)
  WHERE status = ANY (ARRAY['pending'::{schema}.task_status,
                            'retry'::{schema}.task_status]);

CREATE INDEX IF NOT EXISTS task_pending_reference_idx
  ON {schema}.task_pending
  USING btree
  (reference);

CREATE INDEX IF NOT EXISTS task_arch_reference_idx
  ON {schema}.task_arch
  USING btree
  (reference);

CREATE INDEX IF NOT EXISTS task_log_task_id_idx
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
    trace_id: Optional[str]
    trace_span_id: Optional[str]


@dataclass
class PeriodicTask:
    fn: Callable
    name: str
    crontab: CronTab
    strict: bool = False
    date_attr: Optional[str] = None


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
    timezone: str = Field(
        'UTC', description="Временная зона для периодических задач"
    )
    create_database_objects: bool = Field(
        False,
        description="При запуске будет попытка создать объекты базы данных, "
        "если они не существуют",
    )


class TaskManager(Component):
    def __init__(
        self,
        registry: Union['TaskRegistry', object],
        cfg: TaskManagerConfig,
    ) -> None:
        if not isinstance(registry, TaskRegistry):
            warnings.warn(
                "Task manager handler as object is deprecated. "
                "Use TaskRegistry instead",
                DeprecationWarning,
                stacklevel=2,
            )

        self._executor: Executor = Executor(registry)
        self.cfg = cfg
        self._stopping = False
        self._scan_fut: Optional[asyncio.Future] = None
        self._scan_sleep_fut: Optional[asyncio.Future] = None
        self.stamp_early: float = 0.0
        self._lock: Optional[asyncio.Lock] = None
        self._db: Optional[Db] = None
        self._tick_fut: Optional[asyncio.Future] = None
        self._tick_lock: Optional[asyncio.Lock] = None
        self._periodic_tasks: List[PeriodicTask] = []
        self._find_periodic_tasks(registry)
        self._registry = registry

    def _check_deprecated_decorator(self) -> None:
        for fn in Executor.iter_handler(self._registry):
            if not hasattr(fn, '__task_decorator__'):
                warnings.warn(
                    "Decorator @ipapp.rpc.method is deprecated for "
                    "TaskManager tasks declaration. "
                    "Use TaskRegistry instead",
                    DeprecationWarning,
                    stacklevel=2,
                )

    async def prepare(self) -> None:
        if self.app is None:  # pragma: no cover
            raise UserWarning('Unattached component')

        self._check_deprecated_decorator()

        self._lock = asyncio.Lock()

        self._db = Db(self, self.cfg)
        await self._db.init()

        self._tick_lock = asyncio.Lock()
        self._tick_fut = asyncio.ensure_future(self._tick())

    async def start(self) -> None:
        if self.app is None:  # pragma: no cover
            raise UserWarning('Unattached component')

        if not self.cfg.idle:
            self._scan_fut = asyncio.ensure_future(self._scan())

    async def stop(self) -> None:
        self._stopping = True

        # stop tick
        if self._tick_lock is not None and self._tick_fut is not None:
            await self._tick_lock.acquire()
            self._tick_fut.cancel()
            self._tick_lock.release()
            self._tick_lock = None
            self._tick_fut = None

        if self._lock is None:
            return
        await self._lock.acquire()
        if self._scan_fut is not None:
            if not self._scan_fut.done():
                if (
                    self._scan_sleep_fut is not None
                    and not self._scan_sleep_fut.cancelled()
                ):
                    self._scan_sleep_fut.cancel()
                async with self._lock:
                    self._scan_fut.cancel()

    async def health(self) -> None:
        if self._db is not None:
            await self._db.health(lock=True)

    def _find_periodic_tasks(
        self, registry: Union['TaskRegistry', object]
    ) -> None:
        self._periodic_tasks = []
        for fn in Executor.iter_handler(registry):

            if not hasattr(fn, '__crontab__'):
                continue

            task = PeriodicTask(
                fn=fn,
                name=getattr(fn, '__rpc_name__'),
                crontab=getattr(fn, '__crontab__'),
                strict=getattr(fn, '__crontab_do_not_miss__', False),
                date_attr=getattr(fn, '__crontab_date_attr__', None),
            )

            self._periodic_tasks.append(task)

    async def _tick(self) -> None:
        if not self._tick_lock:  # pragma: no cover
            raise UserWarning
        if self._db is None:  # pragma: no cover
            raise UserWarning
        tz = pytz.timezone(self.cfg.timezone)
        boot = True
        while True:
            next_sleep: Optional[float] = None
            to_exec: List[Tuple[Callable, datetime, Optional[str]]] = []
            async with self._tick_lock:
                try:
                    async with self._db.transaction():
                        await self._db.lock_tick_table()
                        now, last = await self._db.get_last_tick_stamp(
                            self.cfg.timezone
                        )
                        if last is None:
                            now, last = await self._db.create_last_tick_stamp(
                                self.cfg.timezone
                            )
                        if now is None:  # pragma: no cover
                            raise RuntimeError

                        now = tz.localize(now)
                        last = tz.localize(last)

                        # now = now + timedelta(microseconds=1)
                        for task in self._periodic_tasks:
                            if boot and not task.strict:
                                next_dt = now + timedelta(
                                    seconds=task.crontab.next(now)
                                )
                            else:
                                next_dt = last + timedelta(
                                    seconds=task.crontab.next(last)
                                )
                            # TODO pass next_dt to tasks as arg
                            while next_dt <= now:
                                to_exec.append(
                                    (task.fn, next_dt, task.date_attr)
                                )
                                if not task.strict:
                                    next_dt = now + timedelta(
                                        seconds=task.crontab.next(now)
                                    )
                                    break
                                next_dt += timedelta(
                                    seconds=task.crontab.next(next_dt)
                                )

                            next_secs = (next_dt - now).total_seconds()
                            if next_sleep is None or next_sleep > next_secs:
                                if next_secs > 0:
                                    next_sleep = next_secs

                        await self._db.update_last_tick_stamp(now)
                except Exception as err:
                    self.app.log_err(err)
                    to_exec = []

            for fn, date_attr_val, date_attr_name in to_exec:
                params = {}
                if date_attr_name is not None:
                    params = {date_attr_name: date_attr_val}
                await self.schedule(fn, params)

            await asyncio.sleep(next_sleep if next_sleep is not None else 1)
            boot = False

    async def schedule(
        self,
        func: TaskHandler,
        params: dict,
        reference: Optional[str] = None,
        eta: Optional[ETA] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        propagate_trace: bool = False,
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

            if max_retries is None:
                max_retries = getattr(func, '__task_max_retries__', 0)
            if retry_delay is None:
                retry_delay = getattr(func, '__task_retry_delay__', 60.0)

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
            add_params: List[Any] = [
                eta_dt,
                func_name,
                params,
                reference,
                max_retries,
                retry_delay,
            ]
            if propagate_trace:
                add_params.append(span.trace_id)
                add_params.append(span.id)
            task_id, task_delay = await self._db.task_add(
                *add_params,
                lock=True,
            )

            span.annotate(TaskManagerSpan.ANN_DELAY, 'Delay: %s' % task_delay)

            eta_float = time() + task_delay
            self.stamp_early = eta_float
            if (
                self._scan_sleep_fut is not None
                and not self._scan_sleep_fut.cancelled()
            ):
                self._scan_sleep_fut.cancel()
            # self.loop.call_at(eta_float, self._scan_later, eta_float)

            return task_id

    async def cancel(self, task_id: int) -> bool:
        if self._db is None or self._lock is None:  # pragma: no cover
            raise UserWarning
        async with self._lock:
            async with self._db.transaction():
                task = await self._db.task_search4cancel(task_id, lock=False)
                if task is not None:
                    await self._db.task_move_arch(
                        task_id,
                        STATUS_CANCELED,
                        None,
                        lock=False,
                        with_trace_id=task.trace_id is not None,
                    )
                    return True
                return False

    async def _scan(self) -> None:
        if self.app is None or self._lock is None:  # pragma: no cover
            raise UserWarning
        while True:
            if self._stopping:
                return
            async with self._lock:
                delay = 1.0  # default: 1 second
                try:
                    with wrap2span(
                        name=TaskManagerSpan.NAME_SCAN,
                        kind=Span.KIND_SERVER,
                        # ignore_ctx=True,
                        cls=TaskManagerSpan,
                        app=self.app,
                    ) as span:
                        try:
                            tasks, delay = await self._search_and_exec()
                            if len(tasks) == 0:
                                span.skip()
                        except Exception as err:
                            span.error(err)
                            self.app.log_err(err)
                        finally:
                            if not self._stopping:
                                span.annotate(
                                    TaskManagerSpan.ANN_NEXT_SCAN,
                                    'next: %s' % delay,
                                )

                finally:
                    if not self._stopping:
                        self._scan_fut = None
                        eta = time() + delay
                        self.stamp_early = eta
            sleep = self.stamp_early - time()
            if sleep > 0:
                try:
                    self._scan_sleep_fut = asyncio.create_task(
                        asyncio.sleep(sleep)
                    )
                    await self._scan_sleep_fut
                except asyncio.CancelledError:
                    pass
            self._scan_sleep_fut = None

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
        await asyncio.gather(*coros)

        return tasks, 0

    async def _exec(self, parent_trace_id: str, task: Task) -> None:
        with wrap2span(
            name=TaskManagerSpan.NAME_EXEC,
            kind=Span.KIND_SERVER,
            ignore_ctx=True,
            cls=TaskManagerSpan,
            app=self.app,
        ) as span:
            if task.trace_id is not None:
                span.trace_id = task.trace_id
                span.parent_id = task.trace_span_id

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
                time_begin = time()
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
                time_finish = time()

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
                                task.id,
                                STATUS_ERROR,
                                retries,
                                lock=True,
                                with_trace_id=task.trace_id is not None,
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
                            task.id,
                            STATUS_ERROR,
                            retries,
                            lock=True,
                            with_trace_id=task.trace_id is not None,
                        )
                else:
                    await self._db.task_move_arch(
                        task.id,
                        STATUS_SUCCESSFUL,
                        retries,
                        lock=True,
                        with_trace_id=task.trace_id is not None,
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

                await self._check_or_create_database_objects()

                await Postgres._conn_init(self._conn)  # noqa
                self._tm.app.logger.app.log_info(
                    "Connected to %s", self._masked_url
                )
                return self._conn
            except Exception as err:
                self._tm.app.logger.app.log_err(err)
                await asyncio.sleep(self._cfg.db_connect_retry_delay)
        raise Exception("Could not connect to %s" % self._masked_url)

    async def _check_or_create_database_objects(self) -> None:
        # TODO сделать полноценное сравнение объектов в БД и их безопасную
        #      модификацию при необходимости
        if self._cfg.create_database_objects:
            await self._create_database_objects()

        # проверка наличия таблицы в БД, для поддержки совместимости
        # со старой версией
        try:
            await self._execute(
                'SELECT 1 FROM {schema}.task_cron_tick'.format(  # nosec
                    schema=self._cfg.db_schema
                )
            )
        except asyncpg.exceptions.UndefinedTableError:
            await self._create_database_objects()

    async def _create_database_objects(self) -> None:
        try:
            await self._execute(
                CREATE_TABLE_QUERY.format(schema=self._cfg.db_schema)
            )
        except Exception as err:
            raise Exception(
                'Failed to create task manager objects at %s with error: %s'
                '' % (self._masked_url, err)
            )

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

    @asynccontextmanager
    async def transaction(
        self,
        isolation: str = 'read_committed',
        readonly: bool = False,
        deferrable: bool = False,
    ) -> AsyncGenerator[Db, None]:
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
        trace_id: Optional[str] = None,
        trace_span_id: Optional[str] = None,
        *,
        lock: bool = False,
    ) -> Tuple[int, float]:
        if trace_id is not None:
            query = (  # nosec
                "INSERT INTO %s.task_pending"
                "(eta,name,params,reference,max_retries,retry_delay,"
                "trace_id,trace_span_id) "
                "VALUES(COALESCE($1, NOW()),$2,$3,$4,$5,"
                "make_interval(secs=>$6::float),$7,$8) "
                "RETURNING id, "
                "greatest(extract(epoch from eta-NOW()), 0) as delay"
            ) % self._cfg.db_schema
            query_params: Tuple[Any, ...] = (
                eta,
                name,
                params,
                reference,
                max_retries,
                retry_delay,
                trace_id,
                trace_span_id,
            )
        else:
            query = (  # nosec
                "INSERT INTO %s.task_pending"
                "(eta,name,params,reference,max_retries,retry_delay) "
                "VALUES(COALESCE($1, NOW()),$2,$3,$4,$5,"
                "make_interval(secs=>$6::float)) "
                "RETURNING id, "
                "greatest(extract(epoch from eta-NOW()), 0) as delay"
            ) % self._cfg.db_schema
            query_params = (
                eta,
                name,
                params,
                reference,
                max_retries,
                retry_delay,
            )

        res = await self._fetchrow(
            query,
            *query_params,
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
            "*"
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
    ) -> Optional[Task]:
        query = (  # nosec
            "UPDATE %s.task_pending SET status='progress',last_stamp=NOW() "
            "WHERE id=$1 AND status IN ('retry','pending') "
            "RETURNING "
            "*"
        ) % (self._cfg.db_schema,)
        res = await self._fetchrow(query, task_id, lock=lock)
        if res is None:
            return None
        return Task(**res)

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
        with_trace_id: bool = False,
    ) -> None:
        if with_trace_id:
            query = (  # nosec
                'WITH del AS (DELETE FROM %s.task_pending WHERE id=$1 '
                'RETURNING id,eta,name,params,max_retries,retry_delay,'
                'retries,reference,trace_id,trace_span_id)'
                'INSERT INTO %s.task_arch'
                '(id,eta,name,params,max_retries,retry_delay,status,'
                'retries,last_stamp,reference,trace_id,trace_span_id)'
                'SELECT '
                'id,eta,name,params,max_retries,retry_delay,$2,'
                'COALESCE($3,retries),NOW(),reference,'
                'trace_id,trace_span_id '
                'FROM del'
            ) % (self._cfg.db_schema, self._cfg.db_schema)
        else:
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

    async def lock_tick_table(self) -> None:
        query = (  # nosec
            'LOCK TABLE %s.task_cron_tick ' 'IN SHARE UPDATE EXCLUSIVE MODE'
        ) % self._cfg.db_schema
        await self._execute(query)

    async def get_last_tick_stamp(
        self, tz: str
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        query = (  # nosec
            'SELECT NOW()::timestamptz(6) at time zone $1 as now,'
            ' last_stamp at time zone $1 as last_stamp '
            'FROM %s.task_cron_tick'
        ) % self._cfg.db_schema
        res = await self._fetchrow(query, tz)
        if res is None:
            return None, None
        return res['now'], res['last_stamp']

    async def create_last_tick_stamp(
        self, tz: str
    ) -> Tuple[datetime, datetime]:
        query = (  # nosec
            'INSERT INTO %s.task_cron_tick(id, last_stamp) '
            'VALUES (0, NOW()::timestamptz(0)) '
            'RETURNING NOW() at time zone $1 as now,'
            ' last_stamp at time zone $1 as last_stamp'
        ) % self._cfg.db_schema
        res = await self._fetchrow(query, tz)
        if res is None:
            raise RuntimeError
        return res['now'], res['last_stamp']

    async def update_last_tick_stamp(self, stamp: datetime) -> None:
        query = (  # nosec
            'UPDATE %s.task_cron_tick SET last_stamp=$1'
        ) % self._cfg.db_schema
        await self._execute(query, stamp)

    async def health(self, *, lock: bool = False) -> None:
        await self._execute('SELECT 1', lock=lock)


class TaskRegistry(RpcRegistry):
    def task(
        self,
        *,
        name: Optional[str] = None,
        errors: Optional[List[Type[RpcError]]] = None,
        deprecated: Optional[bool] = False,
        summary: str = "",
        description: str = "",
        request_model: Optional[Any] = None,
        response_model: Optional[Any] = None,
        request_ref: Optional[str] = None,
        response_ref: Optional[str] = None,
        validators: Optional[Dict[str, dict]] = None,
        examples: Optional[List[Dict[str, Optional[str]]]] = None,
        crontab: Optional[str] = None,
        crontab_do_not_miss: bool = False,
        crontab_date_attr: Optional[str] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
    ) -> Callable:
        def decorator(func: Callable) -> Callable:
            _validate_crontab_fn(
                func, name, crontab, crontab_do_not_miss, crontab_date_attr
            )
            setattr(func, '__task_decorator__', True)
            if max_retries is not None:
                setattr(func, '__task_max_retries__', max_retries)
            if retry_delay is not None:
                setattr(func, '__task_retry_delay__', retry_delay)
            if crontab is not None:
                setattr(func, '__crontab__', CronTab(crontab))
                setattr(func, '__crontab_do_not_miss__', crontab_do_not_miss)
                setattr(func, '__crontab_date_attr__', crontab_date_attr)

            return super(TaskRegistry, self).method(
                name=name,
                errors=errors,
                deprecated=deprecated,
                summary=summary,
                description=description,
                request_model=request_model,
                response_model=response_model,
                request_ref=request_ref,
                response_ref=response_ref,
                validators=validators,
                examples=examples,
            )(func)

        return decorator

    def method(self, *args: Any, **kwargs: Any) -> Callable:
        raise UserWarning('Use "task" method instead "method"')


global_tr = TaskRegistry()


def task(*args: Any, **kwargs: Any) -> Callable:
    warnings.warn(
        "@task decorator is deprecated. User TaskRegistry instead.\n"
        "Also use TaskRegistry as handler object.\n"
        "Example:\n\n"
        "reg = TaskRegistry()\n"
        "\n"
        "@reg.task()\n"
        "def some_task(): pass\n\n"
        "IMPORTANT! Do not use \"self\" argument! "
        "Function can not be a method",
        DeprecationWarning,
        stacklevel=2,
    )
    return global_tr.task(*args, **kwargs)


def _validate_crontab_fn(
    fn: Callable,
    name: Optional[str] = None,
    crontab: Optional[str] = None,
    crontab_do_not_miss: bool = False,
    crontab_date_attr: Optional[str] = None,
) -> None:
    task_name = name or fn.__name__

    if crontab_date_attr is not None:  # pragma: no cover
        if crontab_date_attr not in fn.__code__.co_varnames:
            raise UserWarning(
                'Task "%s" has not required argument "%s" for crontab'
                % (task_name, crontab_date_attr)
            )
