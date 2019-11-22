import json
import logging
import time
from abc import ABCMeta
from contextvars import Token
from datetime import datetime, timezone
from ssl import SSLContext
from typing import Awaitable, Callable, Dict, List, Optional, Type, Union

from aiohttp import web
from aiohttp.abc import AbstractAccessLogger
from aiohttp.payload import Payload
from aiohttp.web_log import AccessLogger
from aiohttp.web_runner import AppRunner, BaseSite, TCPSite
from aiohttp.web_urldispatcher import AbstractRoute
from pydantic.env_settings import BaseSettings

import ipapp.app  # noqa
from ipapp.component import Component
from ipapp.http._base import HttpSpan
from ipapp.logger import Span
from ipapp.misc import (
    ctx_request_reset,
    ctx_request_set,
    ctx_span_get,
    ctx_span_reset,
    ctx_span_set,
)
from ._base import ClientServerAnnotator

access_logger = logging.getLogger('aiohttp.access')


class ServerConfig(BaseSettings):
        host: str = '127.0.0.1'
        port: int = 8080
        access_log_class: Type[AbstractAccessLogger] = AccessLogger
        access_log_format: str = AccessLogger.LOG_FORMAT
        access_log: Optional[logging.Logger] = access_logger
        handle_signals: bool = True
        shutdown_timeout: float = 60.0
        ssl_context: Optional[SSLContext] = None
        backlog: int = 128
        reuse_address: Optional[bool] = None
        reuse_port: Optional[bool] = None


class ServerHandler(object):
    __metaclass__ = ABCMeta

    server: 'Server'

    @property
    def app(self) -> 'ipapp.app.Application':
        return self.server.app

    def _set_server(self, srv: 'Server') -> None:
        setattr(self, 'server', srv)

    def _setup_healthcheck(self, path: str) -> None:
        self.server.add_route('GET', path, self._health_handler_get)
        self.server.add_route('HEAD', path, self._health_handler_head)

    async def _health_handler_get(self, request: web.Request) -> web.Response:
        result = await self._healthcheck()
        headers = {"Content-Type": "application/json;charset=utf-8"}

        if result["is_sick"]:
            raise web.HTTPInternalServerError(
                text=json.dumps(result, indent=4), headers=headers
            )
        else:
            span = ctx_span_get()
            if span:
                span.skip()

        return web.Response(text=json.dumps(result, indent=4), headers=headers)

    async def _health_handler_head(self, request: web.Request) -> web.Response:
        result = await self._healthcheck()

        if result["is_sick"]:
            raise web.HTTPInternalServerError()
        else:
            span = ctx_span_get()
            if span:
                span.skip()
        return web.Response(text='')

    async def _healthcheck(
        self,
    ) -> Dict[str, Union[str, bool, None, Dict[str, str]]]:
        health = await self.app.health()
        is_sick = False
        for key, val in health.items():
            if val is not None:
                is_sick = True
                break
        res: Dict[str, Optional[Union[str, bool, None, Dict[str, str]]]] = {
            "is_sick": is_sick,
            "checks": {
                key: str(val) if val is not None else 'ok'
                for key, val in health.items()
            },
        }
        if self.app.version:
            res['version'] = self.app.version
        if self.app.build_stamp:
            bdt = datetime.fromtimestamp(self.app.build_stamp, tz=timezone.utc)
            res['build_time'] = bdt.strftime('%Y-%m-%dT%H:%M:%SZ')

        if self.app.start_stamp:
            sdt = datetime.fromtimestamp(self.app.start_stamp, tz=timezone.utc)

            res['start_time'] = sdt.strftime('%Y-%m-%dT%H:%M:%SZ')
            res['up_time'] = str(datetime.now(tz=timezone.utc) - sdt)
        return res

    async def prepare(self) -> None:
        pass

    async def error_handler(
        self, request: web.Request, err: Exception
    ) -> web.Response:
        return web.HTTPInternalServerError()


class ServerHttpSpan(HttpSpan):
    P8S_NAME = 'http_in'

    def finish(
        self,
        ts: Optional[float] = None,
        exception: Optional[BaseException] = None,
    ) -> 'Span':

        method = self._tags.get(self.TAG_HTTP_METHOD)
        route = self._tags.get(self.TAG_HTTP_ROUTE)
        if not self._name:
            self._name = 'http::in'
            if method:
                self._name += '::' + method.lower()
            if route:
                self._name += ' (' + route + ')'
        if self.logger is not None:
            self.set_name4adapter(
                self.logger.ADAPTER_PROMETHEUS, self.P8S_NAME
            )

        return super().finish(ts, exception)


class Server(Component, ClientServerAnnotator):
    def __init__(
            self,
            cfg: ServerConfig,
            handler: ServerHandler
    ) -> None:
        handler._set_server(self)
        self.cfg = cfg
        self.handler = handler
        self.host = cfg.host
        self.port = cfg.port
        self.shutdown_timeout = cfg.shutdown_timeout
        self.ssl_context = cfg.ssl_context
        self.backlog = cfg.backlog
        self.reuse_address = cfg.reuse_address
        self.reuse_port = cfg.reuse_port

        self.sites: List[BaseSite] = []

        self.web_app = web.Application()
        self.runner = AppRunner(
            self.web_app,
            handle_signals=cfg.handle_signals,
            access_log_class=cfg.access_log_class,
            access_log_format=cfg.access_log_format,
            access_log=cfg.access_log,
        )
        self.web_app.middlewares.append(self.req_wrapper)

    @web.middleware
    async def req_wrapper(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.Response:
        span_token: Optional[Token] = None
        request_token: Optional[Token] = None
        try:
            span: HttpSpan = self.app.logger.span_from_headers(  # type: ignore
                request.headers, cls=ServerHttpSpan
            )
            span_token = ctx_span_set(span)
            request_token = ctx_request_set(request)
            with span:
                ts1 = time.time()

                span.kind = HttpSpan.KIND_SERVER

                span.tag(HttpSpan.TAG_HTTP_HOST, request.host)
                span.tag(HttpSpan.TAG_HTTP_PATH, request.raw_path)
                span.tag(HttpSpan.TAG_HTTP_METHOD, request.method.upper())
                span.tag(HttpSpan.TAG_HTTP_URL, self._mask_url(request.url))
                self._span_annotate_req_hdrs(span, request.headers, ts=ts1)
                self._span_annotate_req_body(
                    span,
                    await request.read(),
                    ts=ts1,
                    encoding=request.charset,
                )

                resource = request.match_info.route.resource
                # available only in aiohttp >= 3.3.1
                if getattr(resource, 'canonical', None) is not None:
                    route = request.match_info.route.resource.canonical
                    span.tag(HttpSpan.TAG_HTTP_ROUTE, route)

                ts2: float
                try:
                    resp = await handler(request)
                    ts2 = time.time()
                except Exception as err:
                    try:
                        resp = await self.handler.error_handler(request, err)
                        ts2 = time.time()
                    except Exception as err2:
                        span.error(err2)
                        if isinstance(err2, web.Response):
                            resp = err2
                        ts2 = time.time()
                if not isinstance(resp, web.Response):
                    raise UserWarning('Invalid response: %s' % resp)

                span.tag(HttpSpan.TAG_HTTP_STATUS_CODE, str(resp.status))

                self._span_annotate_resp_hdrs(span, resp.headers, ts=ts2)
                if resp.body is not None:
                    if isinstance(resp.body, Payload):
                        body = (
                            '--- payload %s ---' % resp.body.__class__.__name__
                        ).encode()
                    else:
                        body = resp.body
                    self._span_annotate_resp_body(
                        span, body, ts=ts2, encoding=resp.charset
                    )

                return resp
        finally:
            if request_token:
                ctx_request_reset(request_token)
            if span_token:
                ctx_span_reset(span_token)

    def add_route(
        self,
        method: str,
        path: str,
        handler: Callable[[web.Request], Awaitable[web.Response]],
    ) -> 'AbstractRoute':
        if self.web_app is None:  # pragma: no cover
            raise UserWarning('You must add routes in ServerHandler.prepare')
        return self.web_app.router.add_route(method, path, handler)

    async def prepare(self) -> None:
        await self.handler.prepare()
        await self.runner.setup()
        self.sites = []
        self.sites.append(
            TCPSite(
                self.runner,
                self.host,
                self.port,
                shutdown_timeout=self.shutdown_timeout,
                ssl_context=self.ssl_context,
                backlog=self.backlog,
                reuse_address=self.reuse_address,
                reuse_port=self.reuse_port,
            )
        )

    async def start(self) -> None:
        self.app.log_info("Starting HTTP server")
        for site in self.sites:
            await site.start()

        names = sorted(str(s.name) for s in self.runner.sites)
        self.app.log_info("Running HTTP server on %s", ', '.join(names))

    async def stop(self) -> None:
        self.app.log_info("Stopping HTTP server")
        await self.runner.cleanup()

    async def health(self) -> None:
        pass
