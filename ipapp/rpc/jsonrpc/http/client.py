from typing import Any, Iterable, Mapping, Optional, Tuple, Type, Union

from aiohttp import ClientTimeout
from pydantic import BaseModel, Field

from ipapp.http.client import Client, ClientConfig
from ipapp.rpc.jsonrpc.main import JsonRpcCall
from ipapp.rpc.jsonrpc.main import JsonRpcClient as _JsonRpcClient

from ..error import JsonRpcError


class JsonRpcHttpClientConfig(ClientConfig):
    url: str = Field("http://0:8080/", description="Адрес JSON-RPC сервера")
    timeout: float = Field(60.0, description="Таймаут JSON-RPC вызова")


class JsonRpcHttpClient(Client):
    cfg: JsonRpcHttpClientConfig
    clt: _JsonRpcClient

    def __init__(self, cfg: JsonRpcHttpClientConfig) -> None:
        super().__init__(cfg)
        self.cfg = cfg

    async def prepare(self) -> None:
        self.clt = _JsonRpcClient(
            self._send_request,
            self.app,
            exception_mapping_callback=self._raise_jsonrpc_error,
        )

    def _raise_jsonrpc_error(
        self,
        code: Optional[int] = None,
        message: Optional[str] = None,
        data: Optional[Any] = None,
    ) -> None:
        raise JsonRpcError(jsonrpc_error_code=code, message=message, data=data)

    def exec(
        self,
        method: str,
        params: Union[Iterable[Any], Mapping[str, Any], None] = None,
        one_way: bool = False,
        timeout: Optional[float] = None,
        model: Optional[Type[BaseModel]] = None,
    ) -> JsonRpcCall:
        return self.clt.exec(method, params, one_way, timeout, model)

    async def exec_batch(
        self, *calls: JsonRpcCall, timeout: Optional[float] = None
    ) -> Tuple[Union[JsonRpcError, Any], ...]:
        return await self.clt.exec_batch(*calls, timeout=timeout)

    async def _send_request(
        self, request: bytes, timeout: Optional[float]
    ) -> bytes:
        _timeout = self.cfg.timeout
        if timeout is not None:
            _timeout = timeout
        _clt_timeout: Optional[ClientTimeout] = None
        if _timeout:
            _clt_timeout = ClientTimeout(_timeout)

        resp = await self.request(
            'POST', self.cfg.url, body=request, timeout=_clt_timeout
        )
        res = await resp.read()

        return res
