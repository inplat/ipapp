import logging
import os
import sys

sys.path.append(os.getcwd())
import ipapp.autoreload
from aiohttp import web

from ipapp import BaseApplication, BaseConfig, Span, main
from ipapp.http import HttpSpan
from ipapp.http.server import Server, ServerConfig, ServerHandler


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

        self.logger.add_before_handle_cb(self.handle_span)

    @staticmethod
    def handle_span(span: Span) -> None:
        if isinstance(span, HttpSpan):
            if 'secret' in span.tags:
                span.tag('secret', '***')


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ipapp.autoreload.start()
    main(sys.argv, '0.0.1', App, Config)
