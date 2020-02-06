import logging
import sys

from iprpc import method

from ipapp import BaseApplication, BaseConfig, main
from ipapp.db.pg import Postgres, PostgresConfig
from ipapp.http.server import Server, ServerConfig
from ipapp.rpc.http.server import RpcHandler, RpcHandlerConfig


class Config(BaseConfig):
    http: ServerConfig
    db: PostgresConfig


class Api:
    @method()
    async def test(self) -> str:
        return 'ok'


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add(
            'srv', Server(cfg.http, RpcHandler(Api(), RpcHandlerConfig()))
        )
        self.add('db', Postgres(cfg.db))


if __name__ == "__main__":
    """
    APP_DB_URL=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp python3 -m examples.http_rpc_server

    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0.0.1', App, Config)
