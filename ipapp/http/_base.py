import warnings
from typing import Optional, Tuple, Union

from multidict import MultiMapping
from yarl import URL

import ipapp.app  # noqa
import ipapp.misc as misc
from ipapp.logger.span import Span


class ClientServerAnnotator:
    app: 'ipapp.app.BaseApplication'
    _secret_keys: Optional[Tuple[str]] = None

    def _mask_url(self, url: URL) -> str:
        if url.password:
            url = url.with_password('***')
        query = url.query
        if query and self._secret_keys:
            keys_to_upd = {}
            for key_mask in self._secret_keys:
                if key_mask in query:
                    keys_to_upd[key_mask] = '***'
            url = url.update_query(keys_to_upd)
        return str(url)

    def _span_annotate_req_hdrs(
        self, span: 'HttpSpan', headers: MultiMapping[str], ts: float
    ) -> None:
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
        span: 'HttpSpan',
        body: Optional[Union[dict, bytes]],
        ts: float,
        encoding: Optional[str] = None,
    ) -> None:
        try:
            if body is None:
                content = ''
            elif isinstance(body, dict):
                content = str(body)
            else:
                content = self._decode_bytes(body, encoding=encoding)

            span.annotate(HttpSpan.ANN_REQUEST_BODY, content, ts)
            self._span_ann_format4zipkin(
                span, HttpSpan.ANN_REQUEST_BODY, content, ts
            )

        except Exception as err:
            self.app.log_err(err)

    def _span_annotate_resp_hdrs(
        self, span: 'HttpSpan', headers: MultiMapping[str], ts: float
    ) -> None:
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
        span: 'HttpSpan',
        body: bytes,
        ts: float,
        encoding: Optional[str] = None,
    ) -> None:
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
            except Exception:  # nosec
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
            misc.json_encode({kind: content}),
            ts=ts,
        )

    def _span_ann_format4requests(
        self, span: Span, kind: str, content: str, ts: float
    ) -> None:
        span.annotate4adapter(
            self.app.logger.ADAPTER_REQUESTS, kind, content, ts=ts
        )


class HttpSpan(Span):
    TAG_HTTP_HOST = 'http.host'
    TAG_HTTP_METHOD = 'http.method'
    TAG_HTTP_ROUTE = 'http.route'
    TAG_HTTP_PATH = 'http.path'
    TAG_HTTP_REQUEST_SIZE = 'http.request.size'
    TAG_HTTP_RESPONSE_SIZE = 'http.response.size'
    TAG_HTTP_STATUS_CODE = 'http.status_code'
    TAG_HTTP_URL = 'http.url'

    ANN_REQUEST_HDRS = 'request_hdrs'
    ANN_REQUEST_BODY = 'request_body'
    ANN_RESPONSE_HDRS = 'response_hdrs'
    ANN_RESPONSE_BODY = 'response_body'

    @property
    def ann_req_hdrs(self) -> bool:
        warnings.warn(
            "parameter 'ann_req_hdrs' is deprecated, "
            "use component configuration "
            "or app.logger.add_before_handle_cb",
            DeprecationWarning,
            stacklevel=2,
        )
        return True

    @ann_req_hdrs.setter
    def ann_req_hdrs(self, value: bool) -> None:
        pass

    @property
    def ann_req_body(self) -> bool:
        warnings.warn(
            "parameter 'ann_req_body' is deprecated, "
            "use component configuration "
            "or app.logger.add_before_handle_cb",
            DeprecationWarning,
            stacklevel=2,
        )
        return True

    @ann_req_body.setter
    def ann_req_body(self, value: bool) -> None:
        pass

    @property
    def ann_resp_hdrs(self) -> bool:
        warnings.warn(
            "parameter 'ann_resp_hdrs' is deprecated, "
            "use component configuration "
            "or app.logger.add_before_handle_cb",
            DeprecationWarning,
            stacklevel=2,
        )
        return True

    @ann_resp_hdrs.setter
    def ann_resp_hdrs(self, value: bool) -> None:
        pass

    @property
    def ann_resp_body(self) -> bool:
        warnings.warn(
            "parameter 'ann_resp_body' is deprecated, "
            "use component configuration "
            "or app.logger.add_before_handle_cb",
            DeprecationWarning,
            stacklevel=2,
        )
        return True

    @ann_resp_body.setter
    def ann_resp_body(self, value: bool) -> None:
        pass
