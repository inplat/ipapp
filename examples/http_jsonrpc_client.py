import logging
import sys

from pydantic.main import BaseModel

from ipapp import BaseApplication, BaseConfig, main
from ipapp.error import GracefulExit
from ipapp.logger.adapters.prometheus import (
    PrometheusAdapter,
    PrometheusConfig,
)
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.rpc.jsonrpc.http.client import (
    JsonRpcHttpClient,
    JsonRpcHttpClientConfig,
)


class User(BaseModel):
    id: int
    name: str


class Config(BaseConfig):
    rpc: JsonRpcHttpClientConfig
    log_zipkin: ZipkinConfig
    log_prometheus: PrometheusConfig
    log_sentry: SentryConfig
    log_requests: RequestsConfig


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add(
            'clt', JsonRpcHttpClient(cfg.rpc),
        )
        if cfg.log_prometheus.enabled:
            self.logger.add(PrometheusAdapter(cfg.log_prometheus))
        if cfg.log_zipkin.enabled:
            self.logger.add(ZipkinAdapter(cfg.log_zipkin))
        if cfg.log_sentry.enabled:
            self.logger.add(SentryAdapter(cfg.log_sentry))
        if cfg.log_requests.enabled:
            self.logger.add(RequestsAdapter(cfg.log_requests))

    async def start(self) -> None:
        await super().start()

        res1, res2, res3 = await self.clt.exec_batch(
            self.clt.exec('sum', (1, 2)),
            self.clt.exec('sum', {'a': 3, 'b': 5}),
            self.clt.exec('sum', {'c': 3, 'b': 5}),
        )
        print('=' * 80)
        print('RESULT 1:', res1, type(res1))
        print('RESULT 2:', res2, type(res2))
        print('RESULT 3:', res3, type(res3), res3.jsonrpc_error_code)
        print('=' * 80)

        print('BaseModel single')
        res1 = await self.clt.exec('find', [1], model=User)
        print('=' * 80)
        print('RESULT 1:', res1, type(res1))

        print('=' * 80)
        print('BaseModel batch')
        res1, res2 = await self.clt.exec_batch(
            self.clt.exec('find', [1], model=User),
            self.clt.exec('find', {'id': 2}, model=User),
        )
        print('=' * 80)
        print('RESULT 1:', res1, type(res1))
        print('RESULT 2:', res1, type(res2))

        # res = await self.clt.exec('test', {})
        # print('=' * 80)
        # print('RESULT:', res)
        # print('=' * 80)
        #
        # res = await self.clt.exec('test', {}, one_way=True)
        # print('=' * 80)
        # print('RESULT ntf:', res)
        # print('=' * 80)

        raise GracefulExit

    @property
    def clt(self) -> JsonRpcHttpClient:
        clt: JsonRpcHttpClient = self.get('clt')  # type: ignore
        return clt


if __name__ == "__main__":
    """
    Usage:

APP_RPC_URL=http://0.0.0.0:8080/ \
APP_LOG_ZIPKIN_ENABLED=1 \
APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
APP_LOG_ZIPKIN_NAME=rpc-client \
python -m examples.http_jsonrpc_client --log-level CRITICAL


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
