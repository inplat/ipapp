import asyncio
from typing import Dict, List, Optional, Union

from aiohttp import web
from pydantic import BaseModel

from ipapp.http.server import ServerHandler as _ServerHandler
from ipapp.rpc.jsonrpc.main import JsonRpcExecutor
from ipapp.rpc.jsonrpc.openrpc.models import ExternalDocs, Server
from ipapp.rpc.main import RpcRegistry


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
        resp_body = await self._rpc.exec(req_body)
        return web.Response(
            body=resp_body,
            content_type='application/json',
            headers=self._get_cors_headers(),
        )
