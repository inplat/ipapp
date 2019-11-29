import io
import os
from tempfile import NamedTemporaryFile
from typing import Any

from pydantic import BaseModel, Field
from pytest import raises

from ipapp.config import BaseConfig


class Logger(BaseModel):
    level: str = "DEBUG"


class ZipkinLogger(Logger):
    url: str = "http://localhost:9411"


class PrometheusLogger(Logger):
    url: str = "http://localhost:9213"


class Postgres(BaseModel):
    url: str = "postgres://user:pass@localhost:5432/db"


class Config(BaseConfig):
    db: Postgres = Postgres()
    db1: Postgres = Field(..., deprecated=True)
    db2: Postgres = Field(..., env_prefix="database2_")
    zipkin: ZipkinLogger
    prometheus: PrometheusLogger


def validate(config: Any) -> None:
    assert config.zipkin.level == "INFO"
    assert config.zipkin.url == "http://jaeger:9411"
    assert config.prometheus.level == "DEBUG"
    assert config.prometheus.url == "http://prometheus:9213"
    assert config.db.url == "postgres://user:pass@localhost:5432/db"
    assert config.db1.url == "postgres://user:pass@localhost:8001/db"
    assert config.db2.url == "postgres://user:pass@localhost:9002/db"


def test_from_env() -> None:
    os.environ["APP_ZIPKIN_LEVEL"] = "INFO"
    os.environ["APP_ZIPKIN_URL"] = "http://jaeger:9411"
    os.environ["APP_PROMETHEUS_URL"] = "http://prometheus:9213"
    os.environ["APP_DB1_URL"] = "postgres://user:pass@localhost:8001/db"
    os.environ["APP_DATABASE2_URL"] = "postgres://user:pass@localhost:9002/db"

    config: Config = Config.from_env(prefix="app_")
    validate(config)


def test_from_dict() -> None:
    dictionary = {
        "zipkin": {"level": "INFO", "url": "http://jaeger:9411"},
        "prometheus": {"url": "http://prometheus:9213"},
        "db1": {"url": "postgres://user:pass@localhost:8001/db"},
        "db2": {"url": "postgres://user:pass@localhost:9002/db"},
    }

    config: Config = Config.from_dict(dictionary)
    validate(config)


def test_to_dict() -> None:
    config: Config = Config.from_env(prefix="app_")
    dictionary = config.to_dict()
    assert dictionary == {
        "db": {"url": "postgres://user:pass@localhost:5432/db"},
        "db1": {"url": "postgres://user:pass@localhost:8001/db"},
        "db2": {"url": "postgres://user:pass@localhost:9002/db"},
        "prometheus": {"level": "DEBUG", "url": "http://prometheus:9213"},
        "zipkin": {"level": "INFO", "url": "http://jaeger:9411"},
    }


def test_from_json_stream() -> None:
    stream = io.StringIO(
        """
    {
        "zipkin": {
            "level": "INFO",
            "url": "http://jaeger:9411"
        },
        "prometheus": {
            "url": "http://prometheus:9213"
        },
        "db1": {
            "url": "postgres://user:pass@localhost:8001/db"
        },
        "db2": {
            "url": "postgres://user:pass@localhost:9002/db"
        }
    }
    """
    )

    config: Config = Config.from_json(stream)
    validate(config)


def test_from_json_file() -> None:
    config: Config = Config.from_json("tests/config.json")
    validate(config)

    with raises(ValueError):
        Config.from_json(b"")  # type: ignore


def test_to_json_stream() -> None:
    config: Config = Config.from_env(prefix="app_")
    stream = io.StringIO()
    config.to_json(stream)

    with raises(ValueError):
        config.to_json(b"")  # type: ignore

    stream.seek(0)
    temp_conf = Config.from_json(stream)
    validate(temp_conf)


def test_to_json_file() -> None:
    config: Config = Config.from_env(prefix="app_")
    try:
        temp = NamedTemporaryFile()
        config.to_json(temp.name)
        temp_conf = Config.from_json(temp.name)
        validate(temp_conf)
    finally:
        temp.close()


def test_from_yaml_stream() -> None:
    stream = io.StringIO(
        """
    zipkin:
        level: INFO
        url: http://jaeger:9411
    prometheus:
        url: http://prometheus:9213
    db1:
        url: postgres://user:pass@localhost:8001/db
    db2:
        url: postgres://user:pass@localhost:9002/db
    """
    )

    config: Config = Config.from_yaml(stream)
    validate(config)


def test_from_yaml_file() -> None:
    config: Config = Config.from_yaml("tests/config.yaml")
    validate(config)

    with raises(ValueError):
        Config.from_yaml(b"")  # type: ignore


def test_to_yaml_stream() -> None:
    config: Config = Config.from_env(prefix="app_")
    stream = io.StringIO()
    config.to_yaml(stream)

    with raises(ValueError):
        config.to_yaml(b"")  # type: ignore

    stream.seek(0)
    temp_conf = Config.from_yaml(stream)
    validate(temp_conf)


def test_to_yaml_file() -> None:
    config: Config = Config.from_env(prefix="app_")
    try:
        temp = NamedTemporaryFile()
        config.to_yaml(temp.name)
        temp_conf = Config.from_yaml(temp.name)
        validate(temp_conf)
    finally:
        temp.close()


def test_deprecated_field(capsys: Any) -> None:
    os.environ["APP_DB1_URL"] = "postgres://user:pass@localhost:8001/db"
    Config.from_env(prefix="app_")

    captured = capsys.readouterr()
    assert captured.err == "WARNING: db1 field is deprecated\n"
