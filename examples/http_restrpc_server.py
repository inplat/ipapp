import logging
import sys

from pydantic import Field
from pydantic.main import BaseModel

from ipapp import BaseApplication, BaseConfig, main
from ipapp.http.server import Server, ServerConfig
from ipapp.logger.adapters.prometheus import (
    PrometheusAdapter,
    PrometheusConfig,
)
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.rpc import RpcRegistry
from ipapp.rpc.restrpc import RestRpcError
from ipapp.rpc.restrpc.http import RestRpcHttpHandler, RestRpcHttpHandlerConfig

api = RpcRegistry(
    title="Api", description="Api example description", version="1.2.3"
)


class Config(BaseConfig):
    rpc: ServerConfig
    rpc_handler: RestRpcHttpHandlerConfig
    log_zipkin: ZipkinConfig
    log_prometheus: PrometheusConfig
    log_sentry: SentryConfig
    log_requests: RequestsConfig


class MyError(RestRpcError):
    code = 10100
    message = "My error"


class User(BaseModel):
    id: int = Field(
        ...,
        example="123123",
        description="Идентификатор пользователя",
    )
    name: str = Field(
        ...,
        example="Иван",
        description="Имя пользователя",
    )


class Anystr(BaseModel):
    anystr: str = Field(
        ...,
        example="string example",
        description="Строка",
    )


@api.method(
    deprecated=True,
    errors=[MyError],
    summary="method summary",
    description="method description",
)
async def test(
    anystr: str = Field(
        ...,
        example="string example",
        description="Строка",
    )
) -> Anystr:
    return Anystr(anystr=anystr)


@api.method()
async def test_model_in_params(user: User) -> dict:
    return {"result": "ok"}


@api.method()
async def sum(a: int, b: int) -> dict:
    return {"sum": a + b}


@api.method(errors=[MyError])
async def err(a: str) -> str:
    raise MyError


@api.method(errors=[MyError])
async def find(
    id: int = Field(
        ...,
        example="123123",
        description="Идентификатор пользователя",
    )
) -> User:
    return User(id=id, name="User%d" % id)


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add(
            "srv", Server(cfg.rpc, RestRpcHttpHandler(api, cfg.rpc_handler))
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
    APP_LOG_ZIPKIN_ENABLED=1 \
    APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9411/api/v2/spans \
    APP_LOG_ZIPKIN_NAME=rpc-server \
    python3 -m examples.http_restrpc_server

    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, "0.0.1", App, Config)
