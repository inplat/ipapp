import json
import re
from typing import Optional

from multidict import MultiMapping
from yarl import URL

import ipapp.app  # noqa
from ipapp.logger import HttpSpan, Span

RE_SECRET_WORDS = re.compile(
    "(pas+wo?r?d|pass(phrase)?|pwd|token|secrete?)", re.IGNORECASE
)


class ClientServerAnnotator:
    app: 'ipapp.app.Application'

    def _mask_url(self, url: URL) -> str:
        for key, val in url.query.items():
            if RE_SECRET_WORDS.match(key):
                url = url.update_query({key: '***'})
        return str(url)

    def _span_annotate_req_hdrs(
        self, span: HttpSpan, headers: MultiMapping, ts: float
    ) -> None:
        if not span.ann_req_hdrs:
            return
        try:
            hdrs = '\r\n'.join('%s: %s' % (k, v) for k, v in headers.items())
            span.annotate(HttpSpan.ANN_REQUEST_HDRS, hdrs, ts)
            self._span_ann_format4zipkin(
                span, HttpSpan.ANN_REQUEST_HDRS, hdrs, ts
            )

        except Exception as err:
            self.app.log_err(err)

    def _span_annotate_req_body(
        self,
        span: HttpSpan,
        body: Optional[bytes],
        ts: float,
        encoding: Optional[str] = None,
    ) -> None:
        if not span.ann_req_body:
            return
        try:
            if body is None:
                content = ''
            else:
                content = self._decode_bytes(body, encoding=encoding)

            span.annotate(HttpSpan.ANN_REQUEST_BODY, content, ts)
            self._span_ann_format4zipkin(
                span, HttpSpan.ANN_REQUEST_BODY, content, ts
            )

        except Exception as err:
            self.app.log_err(err)

    def _span_annotate_resp_hdrs(
        self, span: HttpSpan, headers: MultiMapping, ts: float
    ) -> None:
        if not span.ann_resp_hdrs:
            return
        try:
            hdrs = '\r\n'.join('%s: %s' % (k, v) for k, v in headers.items())
            span.annotate(HttpSpan.ANN_RESPONSE_HDRS, hdrs, ts)
            self._span_ann_format4zipkin(
                span, HttpSpan.ANN_RESPONSE_HDRS, hdrs, ts
            )
        except Exception as err:
            self.app.log_err(err)

    def _span_annotate_resp_body(
        self,
        span: HttpSpan,
        body: bytes,
        ts: float,
        encoding: Optional[str] = None,
    ) -> None:
        if not span.ann_resp_body:
            return
        try:
            content = self._decode_bytes(body, encoding=encoding)
            span.annotate(HttpSpan.ANN_RESPONSE_BODY, content, ts)
            self._span_ann_format4zipkin(
                span, HttpSpan.ANN_RESPONSE_BODY, content, ts
            )
        except Exception as err:
            self.app.log_err(err)

    @staticmethod
    def _decode_bytes(b: bytes, encoding: Optional[str] = None) -> str:
        if encoding is not None:
            try:
                return b.decode(encoding)
            except Exception:
                pass
        try:
            return b.decode()
        except Exception:
            return str(b)

    def _span_ann_format4zipkin(
        self, span: Span, kind: str, content: str, ts: float
    ) -> None:
        span.annotate4adapter(
            self.app.logger.ADAPTER_ZIPKIN,
            kind,
            json.dumps({kind: content}),
            ts=ts,
        )

    def _span_ann_format4requests(
        self, span: Span, kind: str, content: str, ts: float
    ) -> None:
        span.annotate4adapter(
            self.app.logger.ADAPTER_REQUESTS, kind, content, ts=ts
        )
