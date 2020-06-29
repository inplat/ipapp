import logging
import sys

from aiohttp import web

from ipapp import BaseApplication, BaseConfig, main
from ipapp.http.server import Server, ServerConfig, ServerHandler
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig


class Config(BaseConfig):
    http: ServerConfig
    log_zipkin: ZipkinConfig


class HttpHandler(ServerHandler):
    async def prepare(self) -> None:
        self.server.add_route('GET', '/', self.home)

    async def home(self, request: web.Request) -> web.Response:
        return web.Response(text='Hello, world!')


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add('srv', Server(cfg.http, HttpHandler()))

        if cfg.log_zipkin.enabled:
            self.logger.add(ZipkinAdapter(cfg.log_zipkin))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0.0.1', App, Config)
