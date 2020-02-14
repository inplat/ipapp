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
            print('--- conn.execute ---')
            print(
                await conn.execute(
                    'SELECT :1 FROM DUAL', 123, query_name='conn.execute'
                )
            )
            print('--- conn.query_one ---')
            print(
                await conn.query_one(
                    'SELECT :1 FROM DUAL', 234, query_name='conn.query_one'
                )
            )
            print('--- conn.query_all ---')
            print(
                await conn.query_all(
                    'SELECT :1 FROM DUAL', 345, query_name='conn.query_all'
                )
            )

            async with conn.cursor() as curs:
                print('--- curs.execute ---')
                print(await curs.execute('select 1 from dual'))
                print('--- curs.fetchone ---')
                print(await curs.fetchone())

                print('--- curs.callproc ---')
                ret = curs.ora_cur().var(int)
                print(await curs.callproc('ORABUS.myproc', [123, ret]))
                print(ret.getvalue())

                print('--- curs.callfunc ---')
                ret = curs.ora_cur().var(int)
                print(await curs.callfunc('ORABUS.myfunc', int, [123, ret]))
                print(ret.getvalue())

        raise GracefulExit

    @property
    def db(self) -> Oracle:
        return self.get('db')  # type: ignore


if __name__ == '__main__':
    """
    APP_DB_DSN=localhost:20402/Orabus.localdomain \
    APP_DB_USER=orabus \
    APP_DB_PASSWORD=orabuspwd \
    APP_DB_LOG_QUERY=On \
    APP_DB_LOG_RESULT=On \
    APP_LOG_ZIPKIN_ENABLED=1 \
    APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
    LD_LIBRARY_PATH=/opt/oracle/instantclient_19_5 \
    python -m examples.oracle
    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0.0.1', App, Config)
