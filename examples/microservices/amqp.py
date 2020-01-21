import logging

from iprpc import method

from ipapp import BaseApplication
from ipapp.ctx import app
from ipapp.db.pg import Postgres, PostgresConfig
from ipapp.http.server import ServerHttpSpan
from ipapp.logger.adapters.prometheus import (
    PrometheusAdapter,
    PrometheusConfig,
)
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.mq.pika import Pika, PikaConfig
from ipapp.rpc.mq.pika import RpcServerChannel, RpcServerChannelConfig

app: 'App'


class Api:
    @method()
    async def now(self, tz: str) -> dict:
        async with app.db.connection() as conn:
            row = await conn.query_one(
                'SELECT NOW() at time zone $1 as dt', tz
            )
        return row['dt'].isoformat()


class App(BaseApplication):
    SRV = "rmq"
    DB = "db"

    def __init__(self):
        super().__init__(None)
        self.add(
            self.SRV,
            Pika(
                PikaConfig(url='amqp://guest:guest@localhost:9004/'),
                [
                    lambda: RpcServerChannel(
                        Api(), RpcServerChannelConfig(queue='rpc')
                    )
                ],
            ),
        )
        self.add(
            self.DB,
            Postgres(
                PostgresConfig(
                    url='postgres://ipapp:secretpwd@localhost:9001/ipapp'
                )
            ),
            stop_after=[self.SRV],
        )

        self.logger.add(
            PrometheusAdapter(
                PrometheusConfig(
                    hist_labels={ServerHttpSpan.P8S_NAME: {"arg": "data.arg"}},
                    port=9216,
                )
            )
        )

        self.logger.add(
            ZipkinAdapter(
                ZipkinConfig(
                    name="amqp", addr="http://127.0.0.1:9002/api/v2/spans"
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
                    name='amqp',
                )
            )
        )

    @property
    def db(self) -> Postgres:
        cmp: Postgres = self.get(self.DB)  # type: ignore
        return cmp


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    App().run()
