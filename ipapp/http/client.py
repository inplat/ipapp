import json
import logging
import re
import time
from ssl import SSLContext
from typing import Optional, Dict

from aiohttp import ClientResponse
from aiohttp import ClientSession
from aiohttp import ClientTimeout
from aiohttp.typedefs import StrOrURL
from multidict import CIMultiDictProxy
from yarl import URL

from ipapp.app import Component
from ipapp.logger import (Span, HttpSpan, wrap2span)
from ..misc import ctx_span_get
from ._base import ClientServerAnnotator
__version__ = '0.0.1b6'

SPAN_TYPE_HTTP = 'http'
SPAN_KIND_HTTP_IN = 'in'
SPAN_KIND_HTTP_OUT = 'out'

access_logger = logging.getLogger('aiohttp.access')
RE_SECRET_WORDS = re.compile("(pas+wo?r?d|pass(phrase)?|pwd|token|secrete?)",
                             re.IGNORECASE)


class ClientHttpSpan(HttpSpan):
    P8S_NAME = 'http_out'

    # ann_req_hdrs: bool = True
    # ann_req_body: bool = True
    # ann_resp_hdrs: bool = True
    # ann_resp_body: bool = True

    def finish(self, ts: Optional[float] = None,
               exception: Optional[Exception] = None) -> 'Span':

        method = self._tags.get(self.TAG_HTTP_METHOD)
        host = self._tags.get(self.TAG_HTTP_HOST)
        if not self._name:
            self._name = 'http::out'
            if method:
                self._name += '::' + method.lower()
            if host:
                self._name += ' (' + host + ')'
        self.set_name4adapter(self.logger.ADAPTER_PROMETHEUS, self.P8S_NAME)

        return super().finish(ts, exception)


class Client(Component, ClientServerAnnotator):
    # TODO make pool of clients

    async def prepare(self):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass

    @wrap2span(kind=HttpSpan.KIND_SERVER, cls=ClientHttpSpan)
    async def request(self,
                      method: str,
                      url: StrOrURL, *,
                      body: Optional[bytes] = None,
                      headers: Dict[str, str] = None,
                      timeout: Optional[ClientTimeout] = None,
                      ssl: Optional[SSLContext] = None,
                      session_kwargs: Optional[dict] = None,
                      request_kwargs: Optional[dict] = None
                      ) -> ClientResponse:
        span = ctx_span_get()
        if span is None:  # pragma: no cover
            raise UserWarning

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

        async with ClientSession(loop=self.app.loop,
                                 timeout=timeout,
                                 **(session_kwargs or {})) as session:
            ts1 = time.time()
            resp = await session.request(
                method=method,
                url=url,
                data=body,
                headers=headers,
                ssl=ssl,
                **(request_kwargs or {})
            )
            ts2 = time.time()
            self._span_annotate_req_hdrs(span, resp.request_info.headers, ts1)
            self._span_annotate_req_body(span, body, ts1)
            self._span_annotate_resp_hdrs(span, resp.headers, ts2)
            resp_body = await resp.read()
            self._span_annotate_resp_body(span, resp_body, ts2)

            span.tag(HttpSpan.TAG_HTTP_RESPONSE_SIZE, resp.content_length)
            span.tag(HttpSpan.TAG_HTTP_STATUS_CODE, str(resp.status))

            return resp



    async def health(self):
        pass
