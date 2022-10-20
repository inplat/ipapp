import logging
import sys

from aiohttp import web
from pydantic import BaseModel

from ipapp import BaseApplication, BaseConfig, main
from ipapp.http.server import Server, ServerConfig, ServerHandler


class Messages(BaseModel):
    index: str


class Config(BaseConfig):
    http: ServerConfig
    msg: Messages


class HttpHandler(ServerHandler):
    def __init__(self, msg: str):
        self.msg = msg

    async def prepare(self) -> None:
        self.server.add_route('GET', '/', self.index)

    async def index(self, request: web.Request) -> web.Response:
        return web.Response(text=self.msg)


class App(BaseApplication):
    cfg: Config

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add(
            'srv',
            Server(
                cfg.http,
                HttpHandler(self.cfg.msg.index),
            ),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.argv.extend(['--env-file', 'from_dotenv.env'])
    main(sys.argv, '0.0.1', App, Config)
