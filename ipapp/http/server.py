import logging
import time
from abc import ABCMeta
from contextvars import Token
from datetime import datetime, timezone
from ssl import SSLContext
from typing import Awaitable, Callable, Dict, List, Optional, Union

from aiohttp import web
from aiohttp.payload import Payload
from aiohttp.web_log import AccessLogger
from aiohttp.web_runner import AppRunner, BaseSite, TCPSite
from aiohttp.web_urldispatcher import AbstractResource, AbstractRoute
from pydantic import BaseModel, Field

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
from ipapp.misc import json_encode as default_json_encode

from ._base import ClientServerAnnotator

access_logger = logging.getLogger('aiohttp.access')

SERVER = 'ipapp-http/%s' % ipapp.__version__


class ServerConfig(BaseModel):
    """
    Конфигурация HTTP сервера
    """

    host: str = Field("0.0.0.0", description="Адрес HTTP сервера")  # nosec
    port: int = Field(8080, ge=1, le=65535, description="Порт HTTP сервера")
    shutdown_timeout: float = Field(
        60.0,
        description=(
            "Максимальное время ожидания завершения обработки "
            "входящих запросов перед остановкой сервиса"
        ),
    )
    backlog: int = Field(128, description="Максимально количество соединений")
    reuse_address: Optional[bool] = Field(
        None,
        description=(
            "Повторное использование TIME-WAIT сокетов (SO_REUSEADDR)"
        ),
        example=False,
    )
    reuse_port: Optional[bool] = Field(
        None,
        description=("Повторное использование порта (SO_REUSEPORT)"),
        example=False,
    )
    log_req_hdrs: bool = Field(
        True, description="Логирование заголовков запросов HTTP сервера"
    )
    log_req_body: bool = Field(
        True, description="Логирование тела запросов HTTP сервера"
    )
    log_resp_hdrs: bool = Field(
        True, description="Логирование заголовков ответов HTTP сервера"
    )
    log_resp_body: bool = Field(
        True, description="Логирование тела ответов HTTP сервера"
    )


class ServerHandler(object):
    __metaclass__ = ABCMeta

    server: 'Server'

    @property
    def app(self) -> 'ipapp.app.BaseApplication':
        return self.server.app

    def _set_server(self, srv: 'Server') -> None:
        setattr(self, 'server', srv)

    def _setup_healthcheck(self, path: str = '/health') -> None:
        self.server.add_route('GET', path, self._health_handler_get)
        self.server.add_route('HEAD', path, self._health_handler_head)

    async def _health_handler_get(self, request: web.Request) -> web.Response:
        result = await self._healthcheck()
        headers = {"Content-Type": "application/json;charset=utf-8"}

        if result["is_sick"]:
            raise web.HTTPInternalServerError(
                text=default_json_encode(result, indent=4), headers=headers
            )
        else:
            span = ctx_span_get()
            if span:
                span.skip()

        return web.Response(
            text=default_json_encode(result, indent=4), headers=headers
        )

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

    async def stop(self) -> None:
        pass

    async def error_handler(
        self, request: web.Request, err: Exception
    ) -> web.Response:
        if isinstance(err, web.Response):
            return err
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
            name = self.get_name4adapter(self.logger.ADAPTER_PROMETHEUS)
            if not name:
                self.set_name4adapter(
                    self.logger.ADAPTER_PROMETHEUS, self.P8S_NAME
                )

        return super().finish(ts, exception)


class Server(Component, ClientServerAnnotator):
    def __init__(
        self,
        cfg: ServerConfig,
        handler: ServerHandler,
        *,
        ssl_context: Optional[SSLContext] = None,
    ) -> None:
        handler._set_server(self)
        self.cfg = cfg
        self.handler = handler
        self.host = cfg.host
        self.port = cfg.port
        self.shutdown_timeout = cfg.shutdown_timeout
        self.ssl_context = ssl_context
        self.backlog = cfg.backlog
        self.reuse_address = cfg.reuse_address
        self.reuse_port = cfg.reuse_port

        self.sites: List[BaseSite] = []

        self.web_app = web.Application()
        self.runner = AppRunner(
            self.web_app,
            handle_signals=True,
            access_log_class=AccessLogger,
            access_log_format=AccessLogger.LOG_FORMAT,
            access_log=access_logger,
        )
        self.web_app.middlewares.append(self._req_wrapper)

    @web.middleware
    async def _req_wrapper(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> Union[web.Response, web.FileResponse]:
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
                if self.cfg.log_req_hdrs:
                    self._span_annotate_req_hdrs(span, request.headers, ts=ts1)
                if self.cfg.log_req_body:
                    self._span_annotate_req_body(
                        span,
                        await request.read(),
                        ts=ts1,
                        encoding=request.charset,
                    )

                if request.match_info.route.resource is not None:
                    route = request.match_info.route.resource.canonical
                    span.tag(HttpSpan.TAG_HTTP_ROUTE, route)

                ts2: float
                try:
                    resp = await handler(request)
                    ts2 = time.time()
                except Exception as err:
                    self.app.log_err(err)
                    span.error(err)
                    try:
                        resp = await self.handler.error_handler(request, err)
                        ts2 = time.time()
                    except Exception as err2:
                        self.app.log_err(err2)
                        span.error(err2)
                        if isinstance(err2, web.Response):
                            resp = err2
                        ts2 = time.time()
                if not isinstance(resp, (web.Response, web.FileResponse)):
                    raise UserWarning('Invalid response: %s' % resp)

                if 'Server' not in resp.headers:
                    resp.headers['Server'] = SERVER

                span.tag(HttpSpan.TAG_HTTP_STATUS_CODE, str(resp.status))

                if self.cfg.log_resp_hdrs:
                    self._span_annotate_resp_hdrs(span, resp.headers, ts=ts2)
                if self.cfg.log_resp_body and isinstance(resp, web.Response):
                    if resp.body is not None:
                        if isinstance(resp.body, Payload):
                            body = (
                                '--- payload %s ---'
                                '' % resp.body.__class__.__name__
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

    def add_head(
        self,
        path: str,
        handler: Callable[[web.Request], Awaitable[web.Response]],
    ) -> 'AbstractRoute':
        if self.web_app is None:  # pragma: no cover
            raise UserWarning('You must add routes in ServerHandler.prepare')
        return self.web_app.router.add_head(path, handler)

    def add_options(
        self,
        path: str,
        handler: Callable[[web.Request], Awaitable[web.Response]],
    ) -> 'AbstractRoute':
        if self.web_app is None:  # pragma: no cover
            raise UserWarning('You must add routes in ServerHandler.prepare')
        return self.web_app.router.add_options(path, handler)

    def add_get(
        self,
        path: str,
        handler: Callable[[web.Request], Awaitable[web.Response]],
    ) -> 'AbstractRoute':
        if self.web_app is None:  # pragma: no cover
            raise UserWarning('You must add routes in ServerHandler.prepare')
        return self.web_app.router.add_get(path, handler)

    def add_post(
        self,
        path: str,
        handler: Callable[[web.Request], Awaitable[web.Response]],
    ) -> 'AbstractRoute':
        if self.web_app is None:  # pragma: no cover
            raise UserWarning('You must add routes in ServerHandler.prepare')
        return self.web_app.router.add_post(path, handler)

    def add_put(
        self,
        path: str,
        handler: Callable[[web.Request], Awaitable[web.Response]],
    ) -> 'AbstractRoute':
        if self.web_app is None:  # pragma: no cover
            raise UserWarning('You must add routes in ServerHandler.prepare')
        return self.web_app.router.add_put(path, handler)

    def add_patch(
        self,
        path: str,
        handler: Callable[[web.Request], Awaitable[web.Response]],
    ) -> 'AbstractRoute':
        if self.web_app is None:  # pragma: no cover
            raise UserWarning('You must add routes in ServerHandler.prepare')
        return self.web_app.router.add_patch(path, handler)

    def add_delete(
        self,
        path: str,
        handler: Callable[[web.Request], Awaitable[web.Response]],
    ) -> 'AbstractRoute':
        if self.web_app is None:  # pragma: no cover
            raise UserWarning('You must add routes in ServerHandler.prepare')
        return self.web_app.router.add_delete(path, handler)

    def add_static(
        self,
        prefix: str,
        path: str,
        *,
        chunk_size: int = 256 * 1024,
        show_index: bool = False,
        follow_symlinks: bool = False,
        append_version: bool = False,
    ) -> 'AbstractResource':
        if self.web_app is None:  # pragma: no cover
            raise UserWarning('You must add routes in ServerHandler.prepare')
        return self.web_app.router.add_static(
            prefix,
            path,
            chunk_size=chunk_size,
            show_index=show_index,
            follow_symlinks=follow_symlinks,
            append_version=append_version,
        )

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
        await self.handler.stop()

    async def health(self) -> None:
        pass
