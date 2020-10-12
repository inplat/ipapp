import logging
import sys
from datetime import datetime
from typing import Optional

from ipapp import BaseApplication, BaseConfig, main
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.task.db import TaskManager, TaskManagerConfig, TaskRegistry

app: 'App'

reg = TaskRegistry()


class Config(BaseConfig):
    tm: TaskManagerConfig
    log_requests: RequestsConfig
    log_zipkin: ZipkinConfig


@reg.task(crontab='0 7 * * *')
async def periodic_7_oclock() -> None:
    print('TICK', "seven o'clock")


@reg.task(crontab='@hourly')
async def periodic_hourly() -> None:
    print('TICK', "hourly")


@reg.task(crontab='* * * * * * *', crontab_date_attr='stamp')
async def periodic_every_second(stamp: datetime) -> None:
    print('TICK', stamp.strftime('%d.%m.%Y %H:%M:%S.%f%z'))


@reg.task(
    crontab='*/10 * * * * * *',
    crontab_do_not_miss=True,
    crontab_date_attr='date',
)
async def periodic_every_10_second(date: datetime) -> None:
    print(
        'EXEC periodic_every_10_second_strict',
        date.strftime('%d.%m.%Y %H:%M:%S%z'),
    )


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add('tm', TaskManager(reg, cfg.tm))
        if cfg.log_requests.enabled:
            self.logger.add(RequestsAdapter(cfg.log_requests))
        if cfg.log_zipkin.enabled:
            self.logger.add(ZipkinAdapter(cfg.log_zipkin))

    @property
    def tm(self) -> TaskManager:
        cmp: Optional[TaskManager] = self.get('tm')  # type: ignore
        if cmp is None:
            raise AttributeError
        return cmp


if __name__ == "__main__":
    """
    Usage:

    APP_HTTP_REUSE_PORT=1 \
    APP_LOG_REQUESTS_DSN=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp \
    APP_LOG_REQUESTS_ENABLED=0 \
    APP_LOG_ZIPKIN_ENABLED=1 \
    APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
    APP_LOG_ZIPKIN_NAME=server \
    APP_DB_URL=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp \
    APP_TM_DB_URL=postgres://ipapp:secretpwd@127.0.0.1:9001/ipapp \
    APP_TM_DB_SCHEMA=main \
    APP_TM_CREATE_DATABASE_OBJECTS=1 \
    python -m examples.tm_cron

    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '0', App, Config)
