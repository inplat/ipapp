import logging
import sys
import time
from datetime import datetime
from typing import Optional

from aiohttp import web

from ipapp import BaseApplication, BaseConfig, main
from ipapp.ctx import app
from ipapp.db.pg import Postgres, PostgresConfig
from ipapp.http.client import Client
from ipapp.http.server import Server, ServerConfig, ServerHandler
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.task.db import TaskManager, TaskManagerConfig, task

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
    @task(crontab='0 7 * * *')
    async def periodic_7_oclock(self) -> None:
        print('TICK', "seven o'clock")

    @task(crontab='@hourly')
    async def periodic_hourly(self) -> None:
        print('TICK', "hourly")

    @task(crontab='* * * * * * *', crontab_date_attr='stamp')
    async def periodic_every_second(self, stamp: datetime) -> None:
        print('TICK', stamp.strftime('%d.%m.%Y %H:%M:%S.%f%z'))

    @task(
        crontab='*/10 * * * * * *',
        crontab_do_not_miss=True,
        crontab_date_attr='date',
    )
    async def periodic_every_10_second(self, date: datetime) -> None:
        print(
            'EXEC periodic_every_10_second_strict',
            date.strftime('%d.%m.%Y %H:%M:%S%z'),
        )


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

    APP_HTTP_REUSE_PORT=1 \
    APP_LOG_REQUESTS_DSN=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp \
    APP_LOG_REQUESTS_ENABLED=0 \
    APP_LOG_ZIPKIN_ENABLED=1 \
    APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
    APP_LOG_ZIPKIN_NAME=server \
    APP_DB_URL=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp \
    APP_TM_DB_URL=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp \
    APP_TM_DB_SCHEMA=main \
    APP_TM_CREATE_DATABASE_OBJECTS=1 \
    python -m examples.tm_cron

    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0', App, Config)
