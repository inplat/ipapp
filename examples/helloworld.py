import logging

from aiohttp import web

from ipapp import Application
from ipapp.http.server import Server, ServerConfig, ServerHandler


class HttpHandler(ServerHandler):
    async def prepare(self) -> None:
        self.server.add_route('GET', '/', self.home)

    async def home(self, request: web.Request) -> web.Response:
        return web.Response(text='Hello, world!')


class App(Application):
    def __init__(self) -> None:
        super().__init__()
        self.add('srv', Server(ServerConfig(port=8888), HttpHandler()))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = App()
    app.run()
