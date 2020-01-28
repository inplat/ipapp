import logging

from aiohttp import web

from ipapp import BaseApplication
from ipapp.ctx import span
from ipapp.http.server import (
    Server,
    ServerConfig,
    ServerHandler,
    ServerHttpSpan,
)
from ipapp.logger.adapters.prometheus import (
    PrometheusAdapter,
    PrometheusConfig,
)
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.misc import json_encode as default_json_encode
from ipapp.rpc.http.client import RpcClient, RpcClientConfig


class BackClient(RpcClient):
    async def do_something(self, arg1: str, arg2: int = 0) -> dict:
        return await self.call("do_something", {"arg1": arg1, "arg2": arg2})


class HttpHandler(ServerHandler):
    app: "App"

    async def prepare(self) -> None:
        self._setup_healthcheck()
        self.server.add_route("GET", "/", self.home)

    async def home(self, request: web.Request) -> web.Response:

        arg: str = request.query.get("arg", "")
        span.tag("data.arg", arg)

        result = await self.app.back.do_something(arg)

        return web.Response(text=default_json_encode(result))


class App(BaseApplication):
    SRV = "srv"
    BACK = "back"

    def __init__(self):
        super().__init__(None)
        self.add(self.SRV, Server(ServerConfig(port=8888), HttpHandler()))
        self.add(
            self.BACK,
            BackClient(RpcClientConfig(url="http://127.0.0.1:59871")),
        )

        self.logger.add(
            PrometheusAdapter(
                PrometheusConfig(
                    hist_labels={ServerHttpSpan.P8S_NAME: {"arg": "data.arg"}},
                    port=9214,
                )
            )
        )

        self.logger.add(
            ZipkinAdapter(
                ZipkinConfig(
                    name="front", addr="http://127.0.0.1:9002/api/v2/spans"
                )
            )
        )

        self.logger.add(
            SentryAdapter(
                SentryConfig(
                    dsn="http://0e1fcbe44a5541c2bd20ed5ead2ca033"
                    "@localhost:9000/2"
                )
            )
        )

        self.logger.add(
            RequestsAdapter(
                RequestsConfig(
                    dsn="postgres://ipapp:secretpwd@localhost:9001/ipapp",
                    name='front',
                )
            )
        )

    @property
    def back(self) -> BackClient:
        back: BackClient = self.get(self.BACK)  # type: ignore
        return back


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    App().run()
