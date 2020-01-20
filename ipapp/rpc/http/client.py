import json
from typing import Any, Dict, Optional

from aiohttp import ClientTimeout
from pydantic.main import BaseModel

from ipapp.ctx import app
from ipapp.http.client import Client, ClientHttpSpan

from ..const import SPAN_TAG_RPC_CODE, SPAN_TAG_RPC_METHOD


class RpcClientConfig(BaseModel):
    url: str = 'http://0:8080/'
    timeout: float = 60.0


class RpcError(Exception):
    def __init__(
        self, code: int, message: Optional[str], detail: Optional[str]
    ) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__('%s[%s] %s' % (message, code, detail))


class RpcClient(Client):
    def __init__(self, cfg: RpcClientConfig) -> None:
        self._cfg = cfg

    async def call(
        self, method: str, params: Dict[str, Any], timeout: float = 60.0
    ) -> Any:
        body = json.dumps({"method": method, "params": params}).encode()

        with app.logger.capture_span(ClientHttpSpan) as trap:
            req_err: Optional[Exception] = None
            try:
                tout = timeout or self._cfg.timeout
                if tout:
                    otout = ClientTimeout(tout)
                resp = await self.request(
                    'POST', self._cfg.url, body=body, timeout=otout
                )
            except Exception as err:
                req_err = err

            if trap.is_captured:
                trap.span.name = 'rpc::out (%s)' % method
                trap.span.set_name4adapter(
                    app.logger.ADAPTER_PROMETHEUS, 'rpc_out'
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
