import logging
import sys
from typing import Optional

from aiohttp import web
from iprpc import method

from ipapp import BaseApplication, BaseConfig, main
from ipapp.ctx import app
from ipapp.http.client import Client
from ipapp.http.server import Server, ServerConfig, ServerHandler
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.task.db import TaskManager, TaskManagerConfig

app: 'App'  # type: ignore


class Config(BaseConfig):
    http: ServerConfig
    tm: TaskManagerConfig
    log_requests: RequestsConfig


class HttpHandler(ServerHandler):
    async def prepare(self) -> None:
        self.server.add_route('GET', '/', self.home)

    async def home(self, request: web.Request) -> web.Response:
        app: 'App'
        await app.tm.schedule(Api.test, {})
        return web.Response(text='OK')


class Api:
    @method()
    async def test(self) -> str:
        # resp = await app.clt.request()
        print('EXEC')
        return 'OK'


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add('srv', Server(cfg.http, HttpHandler()))
        self.add('tm', TaskManager(Api(), cfg.tm))
        self.add('clt', Client())
        if cfg.log_requests.enabled:
            self.logger.add(RequestsAdapter(cfg.log_requests))

    @property
    def clt(self) -> Client:
        cmp: Optional[Client] = self.get('clt')  # type: ignore
        if cmp is None:
            raise AttributeError
        return cmp

    @property
    def tm(self) -> TaskManager:
        cmp: Optional[TaskManager] = self.get('tm')  # type: ignore
        if cmp is None:
            raise AttributeError
        return cmp


if __name__ == "__main__":
    """
    Usage:

    APP_LOG_REQUESTS_DSN=postgres://own@127.0.0.1:10209/main APP_LOG_REQUESTS_ENABLED=1 APP_TM_DB_URL=postgres://own@127.0.0.1:10209/main APP_TM_DB_SCHEMA=promo python -m examples.tm

    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0', App, Config)
