import asyncio
import ssl
from contextvars import Token
from functools import partial
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi.applications import FastAPI
from pydantic.main import BaseModel
from uvicorn.config import LOGGING_CONFIG, SSL_PROTOCOL_VERSION
from uvicorn.main import Config, Server
from yarl import URL

from ipapp.component import Component
from ipapp.ctx import span
from ipapp.http import HttpSpan
from ipapp.http._base import RE_SECRET_WORDS
from ipapp.http.server import ServerHttpSpan
from ipapp.misc import ctx_span_reset, ctx_span_set


class UvicornConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    uds: Optional[str] = None
    fd: Optional[int] = None
    loop: str = "auto"
    http: str = "auto"
    ws: str = "auto"
    lifespan: str = "auto"
    env_file: Optional[str] = None
    log_config: dict = LOGGING_CONFIG
    log_level: Optional[str] = None
    access_log: bool = True
    use_colors: Optional[bool] = None
    interface: str = "auto"
    debug: bool = False
    proxy_headers: bool = True
    forwarded_allow_ips: Optional[str] = None
    root_path: str = ""
    limit_concurrency: Optional[int] = None
    limit_max_requests: Optional[int] = None
    backlog: int = 2048
    timeout_keep_alive: int = 5
    timeout_notify: int = 30
    ssl_keyfile: Optional[str] = None
    ssl_certfile: Optional[str] = None
    ssl_version: Optional[int] = SSL_PROTOCOL_VERSION
    ssl_cert_reqs: int = ssl.CERT_NONE
    ssl_ca_certs: Optional[str] = None
    ssl_ciphers: str = "TLSv1"


class AppWrapper:
    def __init__(self, uvicorn: 'Uvicorn', app: Callable):
        self.uvicorn = uvicorn
        self.app = app
        self._i = 0

    async def receive_wraper(self, receive: Callable[..., Awaitable]) -> Any:
        data = await receive()
        self._i += 1
        span.annotate(str(self._i), str(data))
        return data

    async def send_wraper(
        self, send: Callable[..., Awaitable], event: Any
    ) -> None:
        self._i += 1
        span.annotate(str(self._i), str(event))
        await send(event)

    @staticmethod
    def _mask_url(url: URL) -> str:
        if url.password:
            url = url.with_password('***')
        for key, val in url.query.items():
            if RE_SECRET_WORDS.match(key):
                url = url.update_query({key: '***'})
        return str(url)

    async def __call__(
        self,
        scope: dict,
        receive: Callable[..., Awaitable],
        send: Callable[..., Awaitable],
    ) -> None:
        span_token: Optional[Token] = None

        span_name = 'asgi'
        headers: Dict[str, str] = {}
        url: Optional[URL] = None
        host: Optional[str] = None
        method: Optional[str] = None
        path: Optional[str] = None
        if scope['type'] == 'http':
            span_name = 'http::in::%s' % scope['method'].lower()
            headers = {h[0].decode(): h[1].decode() for h in scope['headers']}
            host = ':'.join([str(s) for s in scope['server']])
            path = scope['raw_path'].decode()
            if scope['query_string']:
                path += '?' + scope['query_string'].decode()
            url = URL('%s://%s%s' % (scope['scheme'], host, path))
        try:
            span: HttpSpan = self.uvicorn.app.logger.span_from_headers(  # type: ignore
                headers, cls=ServerHttpSpan
            )
            span.name = span_name
            span_token = ctx_span_set(span)
            with span:
                span.kind = HttpSpan.KIND_SERVER
                if host is not None:
                    span.tag(HttpSpan.TAG_HTTP_HOST, host)
                if path is not None:
                    span.tag(HttpSpan.TAG_HTTP_PATH, path)
                if method is not None:
                    span.tag(HttpSpan.TAG_HTTP_METHOD, method)
                if url is not None:
                    span.tag(HttpSpan.TAG_HTTP_URL, self._mask_url(url))
                try:
                    await self.app(
                        scope,
                        partial(self.receive_wraper, receive),
                        partial(self.send_wraper, send),
                    )
                except Exception as err:
                    self.uvicorn.app.log_err(err)
                    span.error(err)
        except Exception as err:
            self.uvicorn.app.log_err(err)
            raise
        finally:
            if span_token:
                ctx_span_reset(span_token)


class Uvicorn(Component):
    cfg: UvicornConfig

    def __init__(self, cfg: UvicornConfig, fapp: FastAPI):
        self.cfg = cfg
        self.fapp = fapp
        self._srv: Optional[Server] = None
        self._loop_fut: Optional[asyncio.Future] = None
        self._sockets = None

    async def prepare(self) -> None:
        cfg = Config(AppWrapper(self, self.fapp), **self.cfg.dict())
        self._srv = Server(cfg)

    async def start(self) -> None:
        if self._srv is None:
            raise UserWarning

        config = self._srv.config
        if not config.loaded:
            config.load()
        self._srv.lifespan = config.lifespan_class(config)
        self._srv.install_signal_handlers()
        self._sockets = None
        await self._srv.startup(sockets=self._sockets)
        if self._srv.should_exit:
            return
        self.fut = asyncio.ensure_future(self._srv.main_loop())

    async def stop(self) -> None:
        if self._srv is None:
            raise UserWarning
        await self._srv.shutdown(sockets=self._sockets)
        self.fut.cancel()

    async def health(self) -> None:
        pass
