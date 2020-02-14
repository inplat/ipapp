import logging
import sys
from typing import Any, Dict, List, Union

from ipapp import BaseApplication, BaseConfig, main
from ipapp.db.oracle import Oracle, OracleConfig
from ipapp.error import GracefulExit
from ipapp.http.server import ServerConfig
from ipapp.logger.adapters.prometheus import (
    PrometheusAdapter,
    PrometheusConfig,
)
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig

JsonType = Union[None, int, float, str, bool, List[Any], Dict[str, Any]]


class Config(BaseConfig):
    http: ServerConfig
    db: OracleConfig
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

        self.add('db', Oracle(cfg.db))

    async def start(self) -> None:
        await super().start()
        async with self.db.connection() as conn:
            async with self.db.connection() as conn2:
                print(conn._conn)
                print(
                    await conn.query_one(
                        "select sleep(:1) as result from dual", 2
                    )
                )
                print(
                    await conn2.query_all(
                        'SELECT 1 as item FROM dual '
                        'UNION '
                        'SELECT 2 as item FROM dual'
                    )
                )
        raise GracefulExit

    @property
    def db(self) -> Oracle:
        return self.get('db')  # type: ignore


if __name__ == '__main__':
    """
    APP_DB_DSN=localhost:9006/OraDoc.localdomain \
    APP_DB_USER=hr \
    APP_DB_PASSWORD=hr \
    APP_DB_LOG_QUERY=On \
    APP_DB_LOG_RESULT=On \
    APP_LOG_ZIPKIN_ENABLED=1 \
    APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
    LD_LIBRARY_PATH=/opt/oracle/instantclient_19_5 \
    python -m examples.oracle
    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0.0.1', App, Config)
