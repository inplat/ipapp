import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from aiohttp import web
from multidict import CIMultiDict
from pydantic import BaseModel

from ipapp.http.server import ServerHandler as _ServerHandler
from ipapp.rpc.jsonrpc.main import JsonRpcExecutor
from ipapp.rpc.jsonrpc.openrpc.models import ExternalDocs, Server
from ipapp.rpc.main import RpcRegistry


@dataclass
class _SetCookie:
    name: str
    value: str
    expires: Optional[str] = None
    domain: Optional[str] = None
    max_age: Optional[Union[int, str]] = None
    path: str = "/"
    secure: Optional[bool] = None
    httponly: Optional[bool] = None
    version: Optional[str] = None
    samesite: Optional[str] = None


@dataclass
class _DelCookie:
    name: str
    domain: Optional[str] = None
    path: str = "/"


response_set_headers: ContextVar[CIMultiDict] = ContextVar(
    'response_set_headers', default=CIMultiDict()
)


response_set_cookies: ContextVar[List[_SetCookie]] = ContextVar(
    'response_set_cookies', default=[]
)
response_del_cookies: ContextVar[List[_DelCookie]] = ContextVar(
    'response_del_cookies', default=[]
)


class JsonRpcHttpHandlerConfig(BaseModel):
    path: str = '/'
    healthcheck_path: str = '/health'
    shield: bool = False
    discover_enabled: bool = True
    cors_enabled: bool = True
    cors_origin: str = 'https://playground.open-rpc.org'


class JsonRpcHttpHandler(_ServerHandler):
    _rpc: JsonRpcExecutor

    def __init__(
        self,
        registry: Union[RpcRegistry, object],
        cfg: JsonRpcHttpHandlerConfig,
        servers: Optional[List[Server]] = None,
        external_docs: Optional[ExternalDocs] = None,
    ) -> None:
        self._cfg = cfg
        self._registry = registry
        self._servers = servers
        self._external_docs = external_docs

    async def prepare(self) -> None:
        self._rpc = JsonRpcExecutor(
            self._registry,
            self.app,
            discover_enabled=self._cfg.discover_enabled,
            servers=self._servers,
            external_docs=self._external_docs,
        )
        if self._cfg.healthcheck_path:
            self._setup_healthcheck(self._cfg.healthcheck_path)
        self.server.add_route('POST', self._cfg.path, self.rpc_handler)
        self.server.add_route(
            'OPTIONS', self._cfg.path, self.rpc_options_handler
        )
        await self._rpc.start_scheduler()

    async def stop(self) -> None:
        await self._rpc.stop_scheduler()

    def _get_cors_headers(self) -> Dict[str, str]:
        if self._cfg.cors_enabled:
            return {
                'Access-Control-Allow-Origin': self._cfg.cors_origin,
                'Access-Control-Allow-Methods': 'OPTIONS, POST',
                'Access-Control-Allow-Headers': '*',
            }
        else:
            return {}

    async def rpc_options_handler(self, request: web.Request) -> web.Response:
        return web.HTTPOk(headers=self._get_cors_headers())

    async def rpc_handler(self, request: web.Request) -> web.Response:
        if self._cfg.shield:
            return await asyncio.shield(self._handle(request))
        else:
            return await self._handle(request)

    async def _handle(self, request: web.Request) -> web.Response:
        req_body = await request.read()
        response_set_headers_token = response_set_headers.set(CIMultiDict())
        response_set_cookies_token = response_set_cookies.set([])
        response_del_cookies_token = response_del_cookies.set([])
        resp_body = await self._rpc.exec(req_body)
        resp = web.Response(
            body=resp_body,
            content_type='application/json',
            headers=self._get_cors_headers(),
        )

        set_headers = response_set_headers.get()
        resp.headers.extend(set_headers)

        set_cookies = response_set_cookies.get()
        for sc in set_cookies:
            resp.set_cookie(
                sc.name,
                sc.value,
                expires=sc.expires,
                domain=sc.domain,
                max_age=sc.max_age,
                path=sc.path,
                secure=sc.secure,
                httponly=sc.httponly,
                version=sc.version,
                samesite=sc.samesite,
            )

        del_cookies = response_del_cookies.get()
        for dc in del_cookies:
            resp.del_cookie(dc.name, domain=dc.domain, path=dc.path)

        response_set_headers.reset(response_set_headers_token)
        response_set_cookies.reset(response_set_cookies_token)
        response_del_cookies.reset(response_del_cookies_token)
        return resp


def set_reponse_header(name: str, value: str) -> CIMultiDict:
    h = response_set_headers.get()
    h[name] = value
    return h


def set_response_cookie(
    name: str,
    value: str,
    *,
    expires: Optional[str] = None,
    domain: Optional[str] = None,
    max_age: Optional[Union[int, str]] = None,
    path: str = "/",
    secure: Optional[bool] = None,
    httponly: Optional[bool] = None,
    version: Optional[str] = None,
    samesite: Optional[str] = None,
) -> List[_SetCookie]:
    scl = response_set_cookies.get()
    scl.append(
        _SetCookie(
            name,
            value,
            expires,
            domain,
            max_age,
            path,
            secure,
            httponly,
            version,
            samesite,
        )
    )
    return scl


def del_response_cookie(
    name: str, domain: Optional[str] = None, path: str = "/"
) -> List[_DelCookie]:
    dcl = response_del_cookies.get()
    dcl.append(_DelCookie(name, domain, path))
    return dcl
