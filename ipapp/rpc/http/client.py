import warnings
from typing import Any, Callable, Dict, Optional

from aiohttp import ClientTimeout
from pydantic import Field

from ipapp.http.client import Client, ClientConfig, ClientHttpSpan
from ipapp.misc import json_encode as default_json_encode

from ..const import SPAN_TAG_RPC_CODE, SPAN_TAG_RPC_METHOD

warnings.warn(
    "module is deprecated. Use ipapp.rpc.jsonrpc.http.client instead",
    DeprecationWarning,
    stacklevel=2,
)


class RpcClientConfig(ClientConfig):
    url: str = Field("http://0:8080/", description="Адрес RPC сервера")
    timeout: float = Field(60.0, description="Таймаут RPC вызова")


class RpcError(Exception):
    def __init__(
        self, code: int, message: Optional[str], detail: Optional[str]
    ) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__('%s[%s] %s' % (message, code, detail))


class RpcClient(Client):
    cfg: RpcClientConfig

    def __init__(
        self,
        cfg: RpcClientConfig,
        json_encode: Callable[[Any], str] = default_json_encode,
    ) -> None:
        super().__init__(cfg, json_encode=json_encode)
        self.cfg = cfg

    async def call(
        self, method: str, params: Dict[str, Any], timeout: float = 60.0
    ) -> Any:
        body = self._json_encode({"method": method, "params": params}).encode()

        with self.app.logger.capture_span(ClientHttpSpan) as trap:
            req_err: Optional[Exception] = None
            try:
                tout = timeout or self.cfg.timeout
                if tout:
                    otout = ClientTimeout(tout)
                resp = await self.request(
                    'POST', self.cfg.url, body=body, timeout=otout
                )
            except Exception as err:
                req_err = err

            if trap.is_captured:
                trap.span.name = 'rpc::out (%s)' % method
                trap.span.set_name4adapter(
                    self.app.logger.ADAPTER_PROMETHEUS, 'rpc_out'
                )
                trap.span.tag(SPAN_TAG_RPC_METHOD, method)

            if req_err:
                raise req_err

            js = await resp.json()
            if isinstance(js, dict) and 'code' in js:
                trap.span.tag(SPAN_TAG_RPC_CODE, js['code'])

                code = js['code']
                if code == 0:
                    return js['result']

                raise RpcError(js['code'], js['message'], js.get('detail'))
