import asyncio
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import asyncpg
import asyncpg.exceptions
from pydantic import BaseModel, Field

import ipapp.http as ht
import ipapp.logger  # noqa

from ...error import PrepareError
from ...misc import json_encode as default_json_encode
from ...misc import mask_url_pwd
from ..span import Span
from ._abc import AbcAdapter, AbcConfig, AdapterConfigurationError

try:
    import ipapp.mq.pika as amqp
except ImportError:
    amqp = None  # type: ignore

CREATE_TABLE_QUERY = """\
CREATE SCHEMA IF NOT EXISTS {schema_name};

CREATE TABLE IF NOT EXISTS {table_name}
(
    id bigserial PRIMARY KEY,
    stamp_begin timestamptz NOT NULL,
    stamp_end timestamptz NOT NULL,
    service text,
    type text,
    is_out boolean NOT NULL,
    trace_id character varying(32) NOT NULL,
    url text,
    method text,
    req_hdrs text,
    req_body text,
    resp_hdrs text,
    resp_body text,
    status_code int,
    error text,
    tags jsonb
);
"""

CHECK_SQL = """\
WITH req(r_name,r_type,r_notnull) AS (
    values
    ('id', array['bigint'::regtype, 'int'::regtype], true),
    ('stamp_begin',array['timestamp with time zone'::regtype], true),
    ('stamp_end',array['timestamp with time zone'::regtype], true),
    ('is_out',array['boolean'::regtype], true),
    ('trace_id',array['text'::regtype, 'character varying'::regtype], true),
    ('url',array['text'::regtype, 'character varying'::regtype], false),
    ('method',array['text'::regtype, 'character varying'::regtype], false),
    ('req_hdrs',array['text'::regtype, 'character varying'::regtype], false),
    ('req_body',array['text'::regtype, 'character varying'::regtype], false),
    ('resp_hdrs',array['text'::regtype, 'character varying'::regtype], false),
    ('resp_body',array['text'::regtype, 'character varying'::regtype], false),
    ('status_code',array['bigint'::regtype, 'smallint'::regtype,
                         'integer'::regtype], false),
    ('error',array['text'::regtype, 'character varying'::regtype], false),
    ('tags',array['jsonb'::regtype, 'json'::regtype], false),
    ('service',array['text'::regtype, 'character varying'::regtype], false),
    ('type',array['text'::regtype, 'character varying'::regtype], false)
),
ex AS (
    SELECT
        attname            AS r_name,
        atttypid::regtype  AS r_type,
        attnotnull as r_notnull,
        atthasdef as r_hasdef
    FROM   pg_attribute
    WHERE  attrelid = '{table_name}'::regclass
    AND    attnum > 0
    AND    NOT attisdropped
    ORDER  BY attnum
),
checks AS (
    SELECT
        *,
        CASE WHEN ex.r_name IS NULL THEN
            format('column "%s" does not exist', req.r_name)
        WHEN NOT ex.r_type = ANY(req.r_type) THEN
            format('column "%s" has invalid type "%s" (not any of %s)',
                   req.r_name, ex.r_type, req.r_type)
        END as res
    FROM
        req
    LEFT JOIN
        ex USING (r_name)
)
SELECT res FROM checks WHERE res IS NOT NULL
UNION
SELECT format('table has column "%s" which has NOT NULL'
              || ' constraint and without default value',
              r_name)
FROM ex
WHERE
    r_notnull
    AND
    NOT r_hasdef
    AND
    r_name NOT IN (SELECT r_name FROM req)
"""


class Request(BaseModel):
    service: Optional[str] = None
    type: Optional[str] = None
    trace_id: str = ''
    stamp_begin: float = 0
    stamp_end: float = 0
    is_out: bool = False
    url: Optional[str] = None
    method: Optional[str] = None
    req_hdrs: Optional[str] = None
    req_body: Optional[str] = None
    resp_hdrs: Optional[str] = None
    resp_body: Optional[str] = None
    status_code: Optional[int] = None
    error: Optional[str] = None
    tags: Optional[str] = None


class RequestsConfig(AbcConfig):
    dsn: Optional[str] = Field(
        None,
        description="Строка подключения к базе данных",
        example="postgresql://own@localhost:5432/main",
    )
    name: Optional[str] = Field(
        None, description="Название сервиса в базе данных", example="orders"
    )
    db_table_name: str = Field(
        "log.request",
        description="Название таблицы в базе данных с логами запросов",
    )
    send_interval: float = Field(
        5.0, description="Интервал записи запросов в базу данных (в секундах)"
    )
    send_max_count: int = Field(
        10,
        description=(
            "Максимальное количество запросов для записи в базу данных"
        ),
    )
    max_hdrs_length: int = Field(
        64 * 1024,
        description=(
            "Максимальный размер заголовков запросов, которые будут "
            "записаны в базу данных (в байтах)"
        ),
    )
    max_body_length: int = Field(
        64 * 1024,
        description=(
            "Максимальный размер тела запросов, которые будут "
            "записаны в базу данных (в байтах)"
        ),
    )
    max_queue_size: int = Field(
        2 * 1024,
        description=(
            "Максимальный размер очереди запросов на запись в базу данных"
        ),
    )
    connect_max_attempts: int = Field(
        10,
        description=(
            "Максимальное количество попыток подключения к базе данных"
        ),
    )
    connect_retry_delay: float = Field(
        1.0,
        description=(
            "Задержка перед повторной попыткой подключения к базе данных"
        ),
    )
    create_database_objects: bool = Field(
        True,
        description="При запуске будет попытка создать объекты базы данных, "
        "если они не существуют",
    )


class RequestsAdapter(AbcAdapter):
    name = 'requests'
    cfg: RequestsConfig

    _QUERY_COLS = (
        'service',
        'type',
        'trace_id',
        'stamp_begin',
        'stamp_end',
        'is_out',
        'url',
        'method',
        'req_hdrs',
        'req_body',
        'resp_hdrs',
        'resp_body',
        'status_code',
        'error',
        'tags',
    )

    def __init__(self, cfg: RequestsConfig) -> None:
        self.cfg = cfg
        self.logger: Optional['ipapp.logger.Logger'] = None
        self._db: Optional[asyncpg.Connection] = None
        self._queue: Optional[Deque[Request]] = None
        self._send_lock: asyncio.Lock = asyncio.Lock()
        self._send_fut: asyncio.Future[Any] = asyncio.Future()
        self._sleep_fut: Optional[asyncio.Future[Any]] = None
        self._anns_mapping: List[Tuple[str, str, int]] = []
        self._stopping: bool = False
        self._query_template = (
            'INSERT INTO {table_name}' '(%s)' 'VALUES{placeholders}'
        ) % ','.join(self._QUERY_COLS)

    async def start(self, logger: 'ipapp.logger.Logger') -> None:
        self.logger = logger

        self._anns_mapping = [
            (
                'req_hdrs',
                ht.HttpSpan.ANN_REQUEST_HDRS,
                self.cfg.max_hdrs_length,
            ),
            (
                'req_body',
                ht.HttpSpan.ANN_REQUEST_BODY,
                self.cfg.max_body_length,
            ),
            (
                'resp_hdrs',
                ht.HttpSpan.ANN_RESPONSE_HDRS,
                self.cfg.max_hdrs_length,
            ),
            (
                'resp_body',
                ht.HttpSpan.ANN_RESPONSE_BODY,
                self.cfg.max_body_length,
            ),
        ]
        if amqp is not None:
            self._anns_mapping.append(
                (
                    'req_hdrs',
                    amqp.AmqpSpan.ANN_IN_PROPS,
                    self.cfg.max_hdrs_length,
                )
            )
            self._anns_mapping.append(
                (
                    'req_body',
                    amqp.AmqpSpan.ANN_IN_BODY,
                    self.cfg.max_body_length,
                )
            )
            self._anns_mapping.append(
                (
                    'resp_hdrs',
                    amqp.AmqpSpan.ANN_OUT_PROPS,
                    self.cfg.max_hdrs_length,
                )
            )
            self._anns_mapping.append(
                (
                    'resp_body',
                    amqp.AmqpSpan.ANN_OUT_BODY,
                    self.cfg.max_body_length,
                )
            )

        self._queue = deque(maxlen=self.cfg.max_queue_size)

        await self.get_conn()
        self._send_fut = asyncio.ensure_future(self._send_loop())
        # TODO validate table struct

    @property
    def _masked_url(self) -> Optional[str]:
        if self.cfg.dsn is not None:
            return mask_url_pwd(self.cfg.dsn)
        return None

    async def get_conn(self) -> asyncpg.Connection:
        if self.logger is None or self.logger.app is None:  # pragma: no cover
            raise UserWarning
        if self._db is not None and not self._db.is_closed():
            return self._db
        for _ in range(self.cfg.connect_max_attempts):
            try:
                self.logger.app.log_info("Connecting to %s", self._masked_url)
                self._db = await asyncpg.connect(self.cfg.dsn)
                try:
                    await self._check_conn()
                except PrepareError:
                    if not self.cfg.create_database_objects:
                        raise
                    # сервис пытается создать нужные объекты в БД
                    spl = self.cfg.db_table_name.split('.', 1)
                    schema_name = spl[0] if len(spl) == 2 else 'public'
                    await self._db.execute(
                        CREATE_TABLE_QUERY.format(
                            schema_name=schema_name,
                            table_name=self.cfg.db_table_name,
                        )
                    )
                    # если все запросы выполнились, то делаем еще раз проверку,
                    # т.к. в запросах есть IF NOT EXISTS
                    await self._check_conn()

                self.logger.app.log_info("Connected to %s", self._masked_url)
                return self._db
            except Exception as err:
                self.logger.app.log_err(err)
                await asyncio.sleep(self.cfg.connect_retry_delay)
        raise PrepareError("Could not connect to %s" % self._masked_url)

    async def _check_conn(self) -> None:
        if self._db is None:  # pragma: no cover
            raise UserWarning
        query = CHECK_SQL.format(table_name=self.cfg.db_table_name)
        try:
            res = await self._db.fetch(query)
        except asyncpg.exceptions.UndefinedTableError as err:
            raise PrepareError(err)
        if len(res) > 0:
            msg = 'Invalid table "%s" for requests log  [%s]: %s' '' % (
                self.cfg.db_table_name,
                self._masked_url,
                ', '.join([row['res'] for row in res]),
            )
            raise PrepareError(msg)

    def handle(self, span: Span) -> None:  # noqa
        if self.logger is None:  # pragma: no cover
            raise UserWarning
        if self._stopping:  # pragma: no cover
            self.logger.app.log_warn('WTF??? RAHSWS')
        if self._queue is None:  # pragma: no cover
            raise UserWarning

        kwargs: Dict[str, Any] = dict(
            trace_id=span.trace_id,
            stamp_begin=round(span.start_stamp or 0, 6),
            stamp_end=round(span.finish_stamp or 0, 6),
            service=self.cfg.name,
        )

        tags = span.get_tags4adapter(self.name).copy()

        if isinstance(span, ht.HttpSpan):
            kwargs['type'] = 'http'
            kwargs['is_out'] = span.kind != ht.HttpSpan.KIND_SERVER
            if kwargs['is_out']:
                kwargs['type'] += '_out'
            else:
                kwargs['type'] += '_in'

            if ht.HttpSpan.TAG_HTTP_URL in tags:
                kwargs['url'] = tags.pop(ht.HttpSpan.TAG_HTTP_URL)

            if 'rpc.method' in tags:
                kwargs['method'] = tags.pop('rpc.method')
                kwargs['type'] = 'rpc_' + kwargs['type']
            elif ht.HttpSpan.TAG_HTTP_METHOD in tags:
                kwargs['method'] = tags.pop(ht.HttpSpan.TAG_HTTP_METHOD)

            if 'rpc.code' in tags:
                kwargs['status_code'] = tags.pop('rpc.code')
            elif ht.HttpSpan.TAG_HTTP_STATUS_CODE in tags:
                kwargs['status_code'] = tags.pop(
                    ht.HttpSpan.TAG_HTTP_STATUS_CODE
                )

        elif amqp is not None and isinstance(
            span, (amqp.AmqpInSpan, amqp.AmqpOutSpan)
        ):
            kwargs['type'] = 'amqp'
            kwargs['is_out'] = span.kind != amqp.AmqpSpan.KIND_SERVER
            if kwargs['is_out']:
                kwargs['type'] += '_out'
            else:
                kwargs['type'] += '_in'

            if 'rpc.code' in tags:
                kwargs['status_code'] = tags.pop('rpc.code')

            if 'rpc.method' in tags:
                kwargs['method'] = tags.pop('rpc.method')
                kwargs['type'] = 'rpc_' + kwargs['type']
            else:
                exchange: Optional[str] = None
                routing_key: Optional[str] = None
                if amqp.AmqpSpan.TAG_EXCHANGE in tags:
                    exchange = tags.pop(amqp.AmqpSpan.TAG_EXCHANGE)
                if amqp.AmqpSpan.TAG_ROUTING_KEY in tags:
                    routing_key = tags.pop(amqp.AmqpSpan.TAG_ROUTING_KEY)
                if exchange:
                    kwargs['method'] = 'ex:%s rk:%s' % (
                        exchange or '',
                        routing_key or '',
                    )
                else:
                    kwargs['method'] = 'rk:%s' % (routing_key or '')
                if isinstance(span, amqp.AmqpInSpan):
                    kwargs['method'] = 'receive: %s' % kwargs['method']
                else:
                    kwargs['method'] = 'publish: %s' % kwargs['method']

            if amqp.AmqpSpan.TAG_URL in tags:
                kwargs['url'] = tags.pop(amqp.AmqpSpan.TAG_URL)
        else:
            return

        if Span.TAG_ERROR_MESSAGE in tags:
            kwargs['error'] = tags.pop(Span.TAG_ERROR_MESSAGE)

        anns = span.get_annotations4adapter(self.name).copy()

        for key, ann_name, max_len in self._anns_mapping:
            if ann_name in anns:
                val = "\n\n".join([a for a, _ in anns.pop(ann_name)])
                if len(val) > max_len:
                    val = val[:max_len]
                kwargs[key] = val
            elif key not in kwargs:
                kwargs[key] = None

        # удаляем лишние теги
        ht.HttpSpan.TAG_ERROR in tags and tags.pop(ht.HttpSpan.TAG_ERROR)
        ht.HttpSpan.TAG_ERROR_CLASS in tags and tags.pop(
            ht.HttpSpan.TAG_ERROR_CLASS
        )
        ht.HttpSpan.TAG_HTTP_HOST in tags and tags.pop(
            ht.HttpSpan.TAG_HTTP_HOST
        )
        ht.HttpSpan.TAG_HTTP_PATH in tags and tags.pop(
            ht.HttpSpan.TAG_HTTP_PATH
        )
        ht.HttpSpan.TAG_HTTP_ROUTE in tags and tags.pop(
            ht.HttpSpan.TAG_HTTP_ROUTE
        )
        ht.HttpSpan.TAG_HTTP_REQUEST_SIZE in tags and tags.pop(
            ht.HttpSpan.TAG_HTTP_REQUEST_SIZE
        )
        ht.HttpSpan.TAG_HTTP_RESPONSE_SIZE in tags and tags.pop(
            ht.HttpSpan.TAG_HTTP_RESPONSE_SIZE
        )
        if amqp is not None:
            amqp.AmqpSpan.TAG_CHANNEL_NUMBER in tags and tags.pop(
                amqp.AmqpSpan.TAG_CHANNEL_NUMBER
            )

        if len(tags) > 0:
            kwargs['tags'] = default_json_encode(tags)

        self._queue.append(Request(**kwargs))

        if self.cfg.send_max_count <= len(self._queue):
            if self._sleep_fut is not None and not self._sleep_fut.done():
                self._sleep_fut.cancel()

    async def stop(self) -> None:
        self._stopping = True

        if self._queue is not None:
            async with self._send_lock:
                while len(self._queue) > 0:
                    await self._send()

        if self._send_fut is not None:
            self._send_fut.cancel()

    async def _send_loop(self) -> None:
        if self.logger is None:
            raise AdapterConfigurationError(
                '%s is not configured' % self.__class__.__name__
            )
        while not self._stopping:
            try:
                async with self._send_lock:
                    await self._send()
            except Exception as err:
                self.logger.app.log_err(err)
            try:
                self._sleep_fut = asyncio.ensure_future(
                    asyncio.sleep(self.cfg.send_interval)
                )
                await asyncio.wait_for(self._sleep_fut, None)
            except asyncio.CancelledError:
                pass
            finally:
                self._sleep_fut = None

    async def _send(self) -> None:
        if not self._send_lock.locked():
            raise UserWarning
        if self._queue is None:
            return
        if len(self._queue) == 0:
            return
        conn = await self.get_conn()
        cnt = min(self.cfg.max_queue_size, len(self._queue))
        phs, params = self._build_query(cnt)
        query = self._query_template.format(
            table_name=self.cfg.db_table_name, placeholders=','.join(phs)
        )
        await conn.execute(query, *params)

    def _build_query(self, count: int) -> Tuple[List[str], List[Any]]:
        if self._queue is None:  # pragma: no cover
            raise UserWarning
        _query_placeholders: List[str] = []
        _query_params: List[Any] = []

        n = 1
        for _ in range(count):
            req: Request = self._queue.popleft()
            _vals_ph = []
            for col in self._QUERY_COLS:
                val = getattr(req, col)
                if col in ('stamp_begin', 'stamp_end'):
                    _vals_ph.append('to_timestamp($' + str(n) + ')')
                else:
                    _vals_ph.append('$' + str(n))
                _query_params.append(val)
                n += 1
            _query_placeholders.append('(' + (','.join(_vals_ph)) + ')')
        return _query_placeholders, _query_params
