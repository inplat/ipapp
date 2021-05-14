from typing import Any, Iterable, Mapping, Optional, Type, Union

from aiohttp import ClientTimeout
from pydantic import BaseModel, Field

from ipapp.http.client import Client, ClientConfig
from ipapp.rpc.post_rpc.error import PostRpcError
from ipapp.rpc.post_rpc.main import PostRpcCall
from ipapp.rpc.post_rpc.main import PostRpcClient as _PostRpcClient


class PostRpcHttpClientConfig(ClientConfig):
    url: str = Field("http://0:8080/", description="Адрес Post-RPC сервера")
    timeout: float = Field(60.0, description="Таймаут Post-RPC вызова")


class PostRpcHttpClient(Client):
    cfg: PostRpcHttpClientConfig
    clt: _PostRpcClient

    def __init__(self, cfg: PostRpcHttpClientConfig) -> None:
        super().__init__(cfg)
        self.cfg = cfg

    async def prepare(self) -> None:
        self.clt = _PostRpcClient(
            self._send_request,
            self.app,
            exception_mapping_callback=self._raise_post_rpc_error,
        )

    def _raise_post_rpc_error(
        self,
        code: Optional[int] = None,
        message: Optional[str] = None,
        data: Optional[Any] = None,
    ) -> None:
        raise PostRpcError(code=code, message=message, data=data)

    def exec(
        self,
        method: str,
        params: Union[Iterable[Any], Mapping[str, Any], None] = None,
        one_way: bool = False,
        timeout: Optional[float] = None,
        model: Optional[Type[BaseModel]] = None,
    ) -> PostRpcCall:
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
        url = self.cfg.url.removesuffix('/')
        resp = await self.request(
            'POST',
            f'{url}/{method_name}/',
            body=request,
            timeout=_clt_timeout,
        )
        res = await resp.read()
        return res
