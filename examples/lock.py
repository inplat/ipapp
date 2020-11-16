import asyncio
import logging
import sys
from functools import wraps
from typing import Any, Callable, Optional

from aiohttp import web

from ipapp import BaseApplication, BaseConfig, main
from ipapp.ctx import app
from ipapp.http.server import Server, ServerConfig, ServerHandler
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.utils.lock import Lock, LockConfig

app: 'App'  # type: ignore


class Config(BaseConfig):
    http: ServerConfig
    lock: LockConfig
    log_zipkin: ZipkinConfig


def lock_by_id(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        request: web.Request = args[1]
        id = request.match_info.get('id')
        async with app.lock(id):  # type: ignore
            return await func(*args, **kwargs)

    return wrapper


class HttpHandler(ServerHandler):
    async def prepare(self) -> None:
        self.server.add_route('GET', '/{id}', self.home)

    @lock_by_id
    async def home(self, request: web.Request) -> web.Response:
        id = request.match_info.get('id')
        await asyncio.sleep(1)
        return web.Response(text='OK:%s\n' % id)


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add('srv', Server(cfg.http, HttpHandler()))
        self.add('lock', Lock(cfg.lock))
        if cfg.log_zipkin.enabled:
            self.logger.add(ZipkinAdapter(cfg.log_zipkin))

    @property
    def lock(self) -> Lock:
        cmp: Optional[Lock] = self.get('lock')  # type: ignore
        if cmp is None:
            raise AttributeError
        return cmp


if __name__ == "__main__":
    """
    Usage:

    APP_LOG_ZIPKIN_ENABLED=1 \
    APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
    APP_LOG_ZIPKIN_NAME=server \
    APP_LOCK_URL=redis://127.0.0.1:9008/0 \
    python -m examples.lock

    APP_LOG_ZIPKIN_ENABLED=1 \
    APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
    APP_LOG_ZIPKIN_NAME=server \
    APP_LOCK_URL=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp \
    python -m examples.lock

    curl http://localhost:8080/1 & curl http://localhost:8080/2 & curl http://localhost:8080/2 &
    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0', App, Config)
