import logging
import sys

from iprpc import method

from ipapp import BaseApplication, BaseConfig, main
from ipapp.logger.adapters.prometheus import (
    PrometheusAdapter,
    PrometheusConfig,
)
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.mq.pika import Pika, PikaConfig
from ipapp.rpc.mq.pika import RpcServerChannel, RpcServerChannelConfig


class Config(BaseConfig):
    amqp: PikaConfig
    amqp_rpc: RpcServerChannelConfig
    log_zipkin: ZipkinConfig
    log_prometheus: PrometheusConfig
    log_sentry: SentryConfig
    log_requests: RequestsConfig


class Api:
    @method()
    async def test(self) -> str:
        print('EXEC')
        return 'OK'


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add(
            'amqp',
            Pika(cfg.amqp, [lambda: RpcServerChannel(Api(), cfg.amqp_rpc)]),
        )
        if cfg.log_prometheus.enabled:
            self.logger.add(PrometheusAdapter(cfg.log_prometheus))
        if cfg.log_zipkin.enabled:
            self.logger.add(ZipkinAdapter(cfg.log_zipkin))
        if cfg.log_sentry.enabled:
            self.logger.add(SentryAdapter(cfg.log_sentry))
        if cfg.log_requests.enabled:
            self.logger.add(RequestsAdapter(cfg.log_requests))


if __name__ == "__main__":
    """
    Usage:

APP_AMQP_URL=amqp://guest:guest@localhost:9004/ \
APP_AMQP_RPC_QUEUE=rpcqueue \
APP_LOG_REQUESTS_ENABLED=1 \
APP_LOG_REQUESTS_DSN=postgres://ipapp:secretpwd@localhost:9001/ipapp \
APP_LOG_ZIPKIN_ENABLED=1 \
APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
APP_LOG_ZIPKIN_NAME=server \
python -m examples.amqp_rpc_server

    """
    logging.basicConfig(level=logging.INFO)

    import os

    print(
        '\n'.join(
            [
                '%s=%s' % (k, v)
                for k, v in os.environ.items()
                if k.startswith('APP_')
            ]
        )
    )

    main(sys.argv, '0', App, Config)
