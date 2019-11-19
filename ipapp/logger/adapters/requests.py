import asyncio
import json
from collections import deque
from typing import Any, Deque, List, Optional, Tuple

import asyncpg
from pydantic import BaseModel

import ipapp.logger  # noqa

from ..span import HttpSpan, Span
from ._abc import AbcAdapter, AbcConfig


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
    send_interval: float = 5.  # 5 seconds
    send_max_count: int = 10  # 10 requests
    max_hdrs_length: int = 64 * 1024  # 64kB
    max_body_length: int = 64 * 1024  # 64kB
    max_queue_size: int = 2 * 1024


class RequestsAdapter(AbcAdapter):
    name = 'requests'
    cfg: RequestsConfig

    _QUERY_COLS = ('stamp_begin', 'stamp_end', 'is_out', 'url', 'method',
                   'req_hdrs', 'req_body', 'resp_hdrs', 'resp_body',
                   'status_code', 'error', 'tags')

    def __init__(self):
        self.logger: Optional['ipapp.logger.Logger'] = None
        self.db: Optional[asyncpg.Connection] = None
        self._queue: Optional[Deque[Request]] = None
        self._send_lock = asyncio.Lock()
        self._send_fut = asyncio.Future()
        self._sleep_fut: Optional[asyncio.Future] = None
        self._tags_mapping: List[Tuple[str, str]] = []
        self._anns_mapping: List[Tuple[str, str, int]] = []
        self._stopping: bool = False
        self._query_template = (
            'INSERT INTO {table_name}'
            '(%s)'
            'VALUES{placeholders}') % ','.join(self._QUERY_COLS)

    async def start(self, logger: 'ipapp.logger.Logger',
                    cfg: RequestsConfig):
        self.cfg = cfg
        self.logger = logger
        self._tags_mapping = [
            ('url', HttpSpan.TAG_HTTP_URL),
            ('method', HttpSpan.TAG_HTTP_METHOD),
            ('status_code', HttpSpan.TAG_HTTP_STATUS_CODE),
            ('error', HttpSpan.TAG_ERROR_MESSAGE),
        ]
        self._anns_mapping = [
            ('req_hdrs', HttpSpan.ANN_REQUEST_HDRS,
             self.cfg.max_hdrs_length),
            ('req_body', HttpSpan.ANN_REQUEST_BODY,
             self.cfg.max_body_length),
            ('resp_hdrs', HttpSpan.ANN_RESPONSE_HDRS,
             self.cfg.max_hdrs_length),
            ('resp_body', HttpSpan.ANN_RESPONSE_BODY,
             self.cfg.max_body_length),
        ]

        self._queue = deque(maxlen=cfg.max_queue_size)
        self.db = await asyncpg.connect(cfg.dsn)
        self._send_fut = asyncio.ensure_future(self._send_loop())
        # TODO validate table struct

    def handle(self, span: Span):
        if self._stopping:
            self.logger.app.log_warn('WTF??? RAHSWS')
        if not isinstance(span, HttpSpan):
            return

        kwargs = dict(
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
        HttpSpan.TAG_ERROR in tags and tags.pop(HttpSpan.TAG_ERROR)
        HttpSpan.TAG_ERROR_CLASS in tags and tags.pop(HttpSpan.TAG_ERROR_CLASS)
        HttpSpan.TAG_HTTP_HOST in tags and tags.pop(HttpSpan.TAG_HTTP_HOST)
        HttpSpan.TAG_HTTP_PATH in tags and tags.pop(HttpSpan.TAG_HTTP_PATH)
        HttpSpan.TAG_HTTP_ROUTE in tags and tags.pop(HttpSpan.TAG_HTTP_ROUTE)
        HttpSpan.TAG_HTTP_REQUEST_SIZE in tags and tags.pop(
            HttpSpan.TAG_HTTP_REQUEST_SIZE)
        HttpSpan.TAG_HTTP_RESPONSE_SIZE in tags and tags.pop(
            HttpSpan.TAG_HTTP_RESPONSE_SIZE)

        if len(tags) > 0:
            kwargs['tags'] = json.dumps(tags)

        self._queue.append(Request(**kwargs))

        if self.cfg.send_max_count <= len(self._queue):
            if self._sleep_fut is not None and not self._sleep_fut.done():
                self._sleep_fut.cancel()

    async def stop(self):
        self._stopping = True
        while len(self._queue) > 0:
            await self._send()

    async def _send_loop(self):
        while not self._stopping:
            try:
                await self._send()
            except Exception as err:
                self.logger.app.log_err(err)
            try:
                self._sleep_fut = asyncio.ensure_future(
                    asyncio.sleep(self.cfg.send_interval))
                await asyncio.wait_for(self._sleep_fut, None)
            except asyncio.CancelledError:
                pass
            finally:
                self._sleep_fut = None

    async def _send(self):
        if len(self._queue) == 0:
            return
        async with self._send_lock:
            cnt = min(self.cfg.max_queue_size, len(self._queue))
            phs, params = self._build_query(cnt)
            query = self._query_template.format(
                table_name=self.cfg.db_table_name,
                placeholders=','.join(phs))
            await self.db.execute(query, *params)

    def _build_query(self, count) -> Tuple[List[str], List[Any]]:
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
