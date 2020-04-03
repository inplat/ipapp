import logging
import re
import time
from ssl import SSLContext
from typing import Any, Callable, Dict, Optional

from aiohttp import ClientResponse, ClientSession, ClientTimeout
from aiohttp.typedefs import StrOrURL
from pydantic import BaseModel, Field
from yarl import URL

import ipapp
from ipapp.component import Component
from ipapp.logger import Span, wrap2span
from ipapp.misc import json_encode as default_json_encode

from ._base import ClientServerAnnotator, HttpSpan

__version__ = '0.0.1b6'

USER_AGENT = 'ipapp-http/%s' % ipapp.__version__

access_logger = logging.getLogger('aiohttp.access')
RE_SECRET_WORDS = re.compile(
    "(pas+wo?r?d|pass(phrase)?|pwd|token|secrete?)", re.IGNORECASE
)


class ClientConfig(BaseModel):
    log_req_hdrs: bool = Field(
        True, description="Логирование заголовков запросов HTTP клиента"
    )
    log_req_body: bool = Field(
        True, description="Логирование тела запросов HTTP клиента"
    )
    log_resp_hdrs: bool = Field(
        True, description="Логирование заголовков ответов HTTP клиента"
    )
    log_resp_body: bool = Field(
        True, description="Логирование тела ответов HTTP клиента"
    )


class ClientHttpSpan(HttpSpan):
    P8S_NAME = 'http_out'

    def finish(
        self,
        ts: Optional[float] = None,
        exception: Optional[BaseException] = None,
    ) -> 'Span':

        method = self._tags.get(self.TAG_HTTP_METHOD)
        host = self._tags.get(self.TAG_HTTP_HOST)
        if not self._name:
            self._name = 'http::out'
            if method:
                self._name += '::' + method.lower()
            if host:
                self._name += ' (' + host + ')'
        if self.logger is not None:
            self.set_name4adapter(
                self.logger.ADAPTER_PROMETHEUS, self.P8S_NAME
            )

        return super().finish(ts, exception)


class Client(Component, ClientServerAnnotator):
    # TODO make pool of clients

    cfg = ClientConfig()

    def __init__(
        self,
        cfg: Optional[ClientConfig] = None,
        json_encode: Callable[[Any], str] = default_json_encode,
    ):
        if cfg is not None:
            self.cfg = cfg
        self._json_encode = json_encode

    async def prepare(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def request(
        self,
        method: str,
        url: StrOrURL,
        *,
        body: Optional[bytes] = None,
        headers: Dict[str, str] = None,
        timeout: Optional[ClientTimeout] = None,
        ssl: Optional[SSLContext] = None,
        session_kwargs: Optional[Dict[str, Optional[str]]] = None,
        request_kwargs: Optional[Dict[str, Optional[str]]] = None,
        propagate_trace: bool = True,
    ) -> ClientResponse:
        span: 'ClientHttpSpan'
        with wrap2span(  # type: ignore
            kind=HttpSpan.KIND_CLIENT, cls=ClientHttpSpan, app=self.app
        ) as span:
            if not isinstance(url, URL):
                url = URL(url)

            span.tag(HttpSpan.TAG_HTTP_URL, self._mask_url(url))
            span.tag(HttpSpan.TAG_HTTP_HOST, url.host)
            span.tag(HttpSpan.TAG_HTTP_METHOD, method)
            span.tag(HttpSpan.TAG_HTTP_PATH, url.path)
            if body is not None:
                span.tag(HttpSpan.TAG_HTTP_REQUEST_SIZE, len(body))
            else:
                span.tag(HttpSpan.TAG_HTTP_REQUEST_SIZE, 0)

            if timeout is None:
                timeout = ClientTimeout()

            if headers is None:
                headers = {}
            if propagate_trace:
                headers.update(span.to_headers())
            if 'User-Agent' not in headers:
                headers['User-Agent'] = USER_AGENT

            async with ClientSession(
                loop=self.app.loop,
                timeout=timeout,
                **(session_kwargs or {}),  # type: ignore
            ) as session:
                ts1 = time.time()
                resp = await session.request(
                    method=method,
                    url=url,
                    data=body,
                    headers=headers,
                    ssl=ssl,
                    **(request_kwargs or {}),
                )
                ts2 = time.time()
                if self.cfg.log_req_hdrs:
                    self._span_annotate_req_hdrs(
                        span, resp.request_info.headers, ts1
                    )
                if self.cfg.log_req_body:
                    self._span_annotate_req_body(span, body, ts1)
                if self.cfg.log_resp_hdrs:
                    self._span_annotate_resp_hdrs(span, resp.headers, ts2)
                if self.cfg.log_resp_body:
                    resp_body = await resp.read()
                    self._span_annotate_resp_body(span, resp_body, ts2)

                span.tag(HttpSpan.TAG_HTTP_RESPONSE_SIZE, resp.content_length)
                span.tag(HttpSpan.TAG_HTTP_STATUS_CODE, str(resp.status))

                return resp

    async def health(self) -> None:
        pass
