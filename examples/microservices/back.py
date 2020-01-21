import logging
from typing import Optional

from iprpc import method

from ipapp import BaseApplication
from ipapp.ctx import app
from ipapp.http.server import Server, ServerConfig
from ipapp.logger.adapters.prometheus import (
    PrometheusAdapter,
    PrometheusConfig,
)
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.mq.pika import Pika, PikaConfig
from ipapp.rpc.http.server import RpcHandler, RpcHandlerConfig
from ipapp.rpc.mq.pika import RpcClientChannel, RpcClientChannelConfig

app: 'App'


class Api:
    @method()
    async def do_something(self, arg1: str, arg2: int = 0) -> dict:
        result = await app.rmq_pub.call('now', {'tz': 'Europe/Moscow'})
        return {'arg': arg1, 'time': result}


class App(BaseApplication):
    SRV = "srv"
    RMQ = "rmq"

    def __init__(self):
        super().__init__(None)
        self.add(
            self.SRV,
            Server(
                ServerConfig(port=59871),
                RpcHandler(Api(), RpcHandlerConfig()),
            ),
        )
        self.add(
            self.RMQ,
            Pika(
                PikaConfig(url='amqp://guest:guest@localhost:9004/'),
                [
                    lambda: RpcClientChannel(
                        RpcClientChannelConfig(queue='rpc')
                    )
                ],
            ),
        )

        self.logger.add(PrometheusAdapter(PrometheusConfig(port=9215,)))

        self.logger.add(
            ZipkinAdapter(
                ZipkinConfig(
                    name="back", addr="http://127.0.0.1:9002/api/v2/spans"
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
                    name='back',
                )
            )
        )

    @property
    def rmq(self) -> Pika:
        cmp: Optional[Pika] = self.get('rmq')  # type: ignore
        if cmp is None:
            raise AttributeError
        return cmp

    @property
    def rmq_pub(self) -> 'RpcClientChannel':
        ch: Optional['RpcClientChannel'] = self.rmq.channel(  # type: ignore
            'rpc_client'
        )
        if ch is None:
            raise AttributeError
        return ch


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    App().run()
