import logging
import sys

from fastapi.applications import FastAPI

from ipapp import BaseApplication, BaseConfig, main
from ipapp.asgi.uvicorn import Uvicorn, UvicornConfig
from ipapp.ctx import span
from ipapp.logger.adapters.prometheus import (
    PrometheusAdapter,
    PrometheusConfig,
)
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig

fapp = FastAPI()


@fapp.get("/")
async def read_root() -> dict:
    span.annotate('k1', 'hello world')

    return {"Hello": "World"}


@fapp.get("/items/{item_id}")
def read_item(item_id: int, q: str = None) -> dict:
    return {"item_id": item_id, "q": q}


class Config(BaseConfig):
    asgi: UvicornConfig
    log_zipkin: ZipkinConfig
    log_prometheus: PrometheusConfig
    log_sentry: SentryConfig
    log_requests: RequestsConfig


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)

        if cfg.log_prometheus.enabled:
            self.logger.add(PrometheusAdapter(cfg.log_prometheus))
        if cfg.log_zipkin.enabled:
            self.logger.add(ZipkinAdapter(cfg.log_zipkin))
        if cfg.log_sentry.enabled:
            self.logger.add(SentryAdapter(cfg.log_sentry))
        if cfg.log_requests.enabled:
            self.logger.add(RequestsAdapter(cfg.log_requests))

        self.add('srv', Uvicorn(cfg.asgi, fapp))


if __name__ == "__main__":
    """
    Usage:

APP_LOG_ZIPKIN_ENABLED=1 \
APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
APP_LOG_ZIPKIN_NAME=client \
python -m examples.fastapi --log-level CRITICAL
    """

    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0.0.1', App, Config)
