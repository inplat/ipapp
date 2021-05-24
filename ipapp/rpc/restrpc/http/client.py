from typing import Any, Mapping, Optional, Type

from aiohttp import ClientTimeout
from pydantic import BaseModel, Field

from ipapp.http.client import Client, ClientConfig
from ipapp.rpc.restrpc.error import RestRpcError
from ipapp.rpc.restrpc.main import RestRpcCall
from ipapp.rpc.restrpc.main import RestRpcClient as _RestRpcClient


class RestRpcHttpClientConfig(ClientConfig):
    url: str = Field("http://0:8080/", description="Адрес Rest-RPC сервера")
    timeout: float = Field(60.0, description="Таймаут Rest-RPC вызова")


class RestRpcHttpClient(Client):
    cfg: RestRpcHttpClientConfig
    clt: _RestRpcClient

    def __init__(self, cfg: RestRpcHttpClientConfig) -> None:
        super().__init__(cfg)
        self.cfg = cfg

    async def prepare(self) -> None:
        self.clt = _RestRpcClient(
            self._send_request,
            self.app,
            exception_mapping_callback=self._raise_restrpc_error,
        )

    def _raise_restrpc_error(
        self,
        code: Optional[int] = None,
        message: Optional[str] = None,
        data: Optional[Any] = None,
    ) -> None:
        raise RestRpcError(code=code, message=message, data=data)

    def exec(
        self,
        method: str,
        params: Mapping[str, Any],
        one_way: bool = False,
        timeout: Optional[float] = None,
        model: Optional[Type[BaseModel]] = None,
    ) -> RestRpcCall:
        return self.clt.exec(method, params, one_way, timeout, model)

    async def _send_request(
        self, request: bytes, method_name: str, timeout: Optional[float]
    ) -> bytes:
        _timeout = self.cfg.timeout
        if timeout is not None:
            _timeout = timeout
        _clt_timeout: Optional[ClientTimeout] = None
        if _timeout:
            _clt_timeout = ClientTimeout(_timeout)
        url = self.cfg.url
        if url.endswith('/'):
            url = url[:-1]
        resp = await self.request(
            'POST',
            f'{url}/{method_name}/',
            body=request,
            timeout=_clt_timeout,
        )
        res = await resp.read()
        return res
