import logging
import os
import sys

from aiohttp import web

from ipapp import BaseApplication, BaseConfig, main
from ipapp.http.server import Server, ServerConfig, ServerHandler

sys.path.append(os.getcwd())  # noqa


class Config(BaseConfig):
    http: ServerConfig


class HttpHandler(ServerHandler):
    async def prepare(self) -> None:
        self.server.add_route('GET', '/', self.home)

    async def home(self, request: web.Request) -> web.Response:
        return web.Response(text='Hello, world!')


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add('srv', Server(cfg.http, HttpHandler()))


if __name__ == "__main__":
    """
    python -m examples.autoreload --autoreload
    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0.0.1', App, Config)
