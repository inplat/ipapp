import asyncio
import json
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import asyncpg
from pydantic.main import BaseModel

import ipapp.http as ht
import ipapp.logger  # noqa

from ..span import Span
from ._abc import AbcAdapter, AbcConfig, AdapterConfigurationError


class Request(BaseModel):
    stamp_begin: float
    stamp_end: float
    is_out: bool
    url: Optional[str]
    method: Optional[str]
    req_hdrs: Optional[str] = None
    req_body: Optional[str] = None
    resp_hdrs: Optional[str] = None
    resp_body: Optional[str] = None
    status_code: Optional[int] = None
    error: Optional[str] = None
    tags: Optional[str] = None


class RequestsConfig(AbcConfig):
    dsn: str
    db_table_name: str = 'log.request'
    send_interval: float = 5.0  # 5 seconds
    send_max_count: int = 10  # 10 requests
    max_hdrs_length: int = 64 * 1024  # 64kB
    max_body_length: int = 64 * 1024  # 64kB
    max_queue_size: int = 2 * 1024


class RequestsAdapter(AbcAdapter):
    name = 'requests'
    cfg: RequestsConfig

    _QUERY_COLS = (
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
        self.db: Optional[asyncpg.Connection] = None
        self._queue: Optional[Deque[Request]] = None
        self._send_lock: asyncio.Lock = asyncio.Lock()
        self._send_fut: asyncio.Future[Any] = asyncio.Future()
        self._sleep_fut: Optional[asyncio.Future[Any]] = None
        self._tags_mapping: List[Tuple[str, str]] = []
        self._anns_mapping: List[Tuple[str, str, int]] = []
        self._stopping: bool = False
        self._query_template = (
            'INSERT INTO {table_name}' '(%s)' 'VALUES{placeholders}'
        ) % ','.join(self._QUERY_COLS)

    async def start(self, logger: 'ipapp.logger.Logger') -> None:
        self.logger = logger
        self._tags_mapping = [
            ('url', ht.HttpSpan.TAG_HTTP_URL),
            ('method', ht.HttpSpan.TAG_HTTP_METHOD),
            ('status_code', ht.HttpSpan.TAG_HTTP_STATUS_CODE),
            ('error', ht.HttpSpan.TAG_ERROR_MESSAGE),
        ]
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

        self._queue = deque(maxlen=self.cfg.max_queue_size)
        self.db = await asyncpg.connect(self.cfg.dsn)
        self._send_fut = asyncio.ensure_future(self._send_loop())
        # TODO validate table struct

    def handle(self, span: Span) -> None:
        if self.logger is None:
            raise UserWarning
        if self._stopping:
            self.logger.app.log_warn('WTF??? RAHSWS')
        if self._queue is None:
            raise UserWarning
        if not isinstance(span, ht.HttpSpan):
            return

        kwargs: Dict[str, Any] = dict(
            stamp_begin=span.start_stamp,
            stamp_end=span.finish_stamp,
            is_out=span.kind != span.KIND_SERVER,
        )
        tags = span.get_tags4adapter(self.name).copy()
        anns = span.get_annotations4adapter(self.name).copy()

        for key, tag_name in self._tags_mapping:
            if tag_name in tags:
                kwargs[key] = tags.pop(tag_name)
            else:
                kwargs[key] = None

        for key, ann_name, max_len in self._anns_mapping:
            if ann_name in anns:
                val = "\n\n".join([a for a, _ in anns.pop(ann_name)])
                if len(val) > max_len:
                    val = val[:max_len]
                kwargs[key] = val
            else:
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

        if len(tags) > 0:
            kwargs['tags'] = json.dumps(tags)

        self._queue.append(Request(**kwargs))

        if self.cfg.send_max_count <= len(self._queue):
            if self._sleep_fut is not None and not self._sleep_fut.done():
                self._sleep_fut.cancel()

    async def stop(self) -> None:
        self._stopping = True
        if self._queue is not None:
            while len(self._queue) > 0:
                await self._send()

    async def _send_loop(self) -> None:
        if self.logger is None:
            raise AdapterConfigurationError(
                '%s is not configured' % self.__class__.__name__
            )
        while not self._stopping:
            try:
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
        if self._queue is None or self.db is None:
            return
        if len(self._queue) == 0:
            return
        async with self._send_lock:
            cnt = min(self.cfg.max_queue_size, len(self._queue))
            phs, params = self._build_query(cnt)
            query = self._query_template.format(
                table_name=self.cfg.db_table_name, placeholders=','.join(phs)
            )
            await self.db.execute(query, *params)

    def _build_query(self, count: int) -> Tuple[List[str], List[Any]]:
        if self._queue is None:
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
