import logging
import sys
import time
from typing import Optional

from aiohttp import web
from iprpc import method

from ipapp import BaseApplication, BaseConfig, main
from ipapp.ctx import app
from ipapp.db.pg import Postgres, PostgresConfig
from ipapp.http.client import Client
from ipapp.http.server import Server, ServerConfig, ServerHandler
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.task.db import TaskManager, TaskManagerConfig

app: 'App'  # type: ignore


class Config(BaseConfig):
    http: ServerConfig
    db: PostgresConfig
    tm: TaskManagerConfig
    log_requests: RequestsConfig
    log_zipkin: ZipkinConfig


class HttpHandler(ServerHandler):
    async def prepare(self) -> None:
        self.server.add_route('GET', '/', self.home)

    async def home(self, request: web.Request) -> web.Response:
        await app.tm.schedule(Api.test, {}, eta=time.time())  # type: ignore
        return web.Response(text='OK')


class Api:
    @method()
    async def test(self) -> str:
        async with app.db.connection() as conn:  # type: ignore
            async with conn.xact():
                await conn.query_one('SELECT 1')
                await conn.query_one('SELECT 2')
        # resp = await app.clt.request()
        print('EXEC')
        return 'OK'


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add('srv', Server(cfg.http, HttpHandler()))
        self.add('tm', TaskManager(Api(), cfg.tm))
        self.add('db', Postgres(cfg.db), stop_after=['srv', 'tm'])
        self.add('clt', Client(), stop_after=['srv', 'tm'])
        if cfg.log_requests.enabled:
            self.logger.add(RequestsAdapter(cfg.log_requests))
        if cfg.log_zipkin.enabled:
            self.logger.add(ZipkinAdapter(cfg.log_zipkin))

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

    @property
    def db(self) -> Postgres:
        cmp: Optional[Postgres] = self.get('db')  # type: ignore
        if cmp is None:
            raise AttributeError
        return cmp


if __name__ == "__main__":
    """
    Usage:

    APP_LOG_REQUESTS_DSN=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp \
    APP_LOG_REQUESTS_ENABLED=1 \
    APP_LOG_ZIPKIN_ENABLED=1 \
    APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
    APP_LOG_ZIPKIN_NAME=server \
    APP_DB_URL=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp \
    APP_TM_DB_URL=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp \
    APP_TM_DB_SCHEMA=main \
    python -m examples.tm

    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0', App, Config)
