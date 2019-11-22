from typing import List, Mapping, Optional, Union

from pydantic import BaseModel, BaseSettings

from ipapp.config import BaseConfig


def test_cfg():
    env = {
        'APP_ZIPKIN_LEVEL': 'INFO',
        'APP_ZIPKIN_URL': 'http://zipkin',
        'APP_PROMETHEUS_URL': 'http://prometheus',
        'APP_DB_URL': 'postgres',
    }

    class Logger(BaseModel):
        level: str = 'DEBUG'

    class ZipkinLogger(Logger):
        url: str = 'http://localhost'

        class Config:
            env_prefix = 'zipkin_'

    class PrometheusLogger(Logger):
        url: str = 'http://localhost'

        class Config:
            env_prefix = 'prometheus_'

    class DbCfg(BaseSettings):
        url: Optional[str] = None

    class Config(BaseConfig):
        db: DbCfg = DbCfg()
        loggers: List[Union[ZipkinLogger, PrometheusLogger]]

        class Config:
            env_prefix = 'app_'

        def get_environ(self) -> Mapping[str, str]:
            return env

    cfg = Config()
    print(cfg)
    # assert cfg.db.url is None
    assert len(cfg.loggers) == 2
    assert cfg.loggers[0].url == 'http://zipkin'
    assert cfg.loggers[0].level == 'INFO'
    assert cfg.loggers[1].url == 'http://prometheus'
    assert cfg.loggers[1].level == 'DEBUG'
