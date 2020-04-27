import io
import os
from enum import Enum
from ipaddress import IPv4Address
from tempfile import NamedTemporaryFile
from typing import Any, List, Optional

from pydantic import UUID4, BaseModel, Field
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


def test_from_env(capsys) -> None:
    os.environ["APP_ZIPKIN_LEVEL"] = "INFO"
    os.environ["APP_ZIPKIN_URL"] = "http://jaeger:9411"
    os.environ["APP_PROMETHEUS_URL"] = "http://prometheus:9213"
    os.environ["APP_DB1_URL"] = "postgres://user:pass@localhost:8001/db"
    os.environ["APP_DATABASE2_URL"] = "postgres://user:pass@localhost:9002/db"

    config: Config = Config.from_env(prefix="app_")
    validate(config)

    capsys.readouterr()  # hide stdout


def test_from_dict() -> None:
    dictionary = {
        "zipkin": {"level": "INFO", "url": "http://jaeger:9411"},
        "prometheus": {"url": "http://prometheus:9213"},
        "db1": {"url": "postgres://user:pass@localhost:8001/db"},
        "db2": {"url": "postgres://user:pass@localhost:9002/db"},
    }

    config: Config = Config.from_dict(dictionary)
    validate(config)


def test_to_dict(capsys) -> None:
    config: Config = Config.from_env(prefix="app_")
    dictionary = config.to_dict()
    assert dictionary == {
        "db": {"url": "postgres://user:pass@localhost:5432/db"},
        "db1": {"url": "postgres://user:pass@localhost:8001/db"},
        "db2": {"url": "postgres://user:pass@localhost:9002/db"},
        "prometheus": {"level": "DEBUG", "url": "http://prometheus:9213"},
        "zipkin": {"level": "INFO", "url": "http://jaeger:9411"},
    }
    capsys.readouterr()  # hide stdout


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


def test_to_json_stream(capsys) -> None:
    config: Config = Config.from_env(prefix="app_")
    stream = io.StringIO()
    config.to_json(stream)

    with raises(ValueError):
        config.to_json(b"")  # type: ignore

    stream.seek(0)
    temp_conf = Config.from_json(stream)
    validate(temp_conf)

    capsys.readouterr()  # hide stdout


def test_to_json_file(capsys) -> None:
    config: Config = Config.from_env(prefix="app_")
    try:
        temp = NamedTemporaryFile()
        config.to_json(temp.name)
        temp_conf = Config.from_json(temp.name)
        validate(temp_conf)
    finally:
        temp.close()

    capsys.readouterr()  # hide stdout


def test_from_yaml_stream(capsys) -> None:
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

    capsys.readouterr()  # hide stdout


def test_from_yaml_file(capsys) -> None:
    config: Config = Config.from_yaml("tests/config.yaml")
    validate(config)

    with raises(ValueError):
        Config.from_yaml(b"")  # type: ignore

    capsys.readouterr()  # hide stdout


def test_to_yaml_stream(capsys) -> None:
    config: Config = Config.from_env(prefix="app_")
    stream = io.StringIO()
    config.to_yaml(stream)

    with raises(ValueError):
        config.to_yaml(b"")  # type: ignore

    stream.seek(0)
    temp_conf = Config.from_yaml(stream)
    validate(temp_conf)

    capsys.readouterr()  # hide stdout


def test_to_yaml_file(capsys) -> None:
    config: Config = Config.from_env(prefix="app_")
    try:
        temp = NamedTemporaryFile()
        config.to_yaml(temp.name)
        temp_conf = Config.from_yaml(temp.name)
        validate(temp_conf)
    finally:
        temp.close()

    capsys.readouterr()  # hide stdout


def test_deprecated_field(capsys: Any) -> None:
    os.environ["APP_DB1_URL"] = "postgres://user:pass@localhost:8001/db"
    Config.from_env(prefix="app_")

    captured = capsys.readouterr()
    assert captured.err == "WARNING: db1 field is deprecated\n"


def test_to_env_schema() -> None:
    class Level(str, Enum):
        INFO = "INFO"
        WARN = "WARN"
        CRIT = "CRIT"

    class ServerConfig(BaseModel):
        host: IPv4Address = IPv4Address("127.0.0.1")
        port: int = Field(
            8080, ge=1, le=65535, description="TCP/IP port", example="9090"
        )
        path: str = Field("/", deprecated=True)
        timeout: float = 60.0

    class BaseLoggerConfig(BaseModel):
        enabled: bool = False
        level: Level = Level.INFO

    class LoggerConfig(BaseLoggerConfig):
        enabled: bool = True
        tags: List[str] = ["dev"]

    class Config(BaseConfig):
        uuid: UUID4
        name: str = Field(..., regex=r"\w+")
        description: Optional[str] = None
        server: ServerConfig
        logger: LoggerConfig

    config = Config.to_env_schema(prefix="app_")

    assert config == {
        "APP_DESCRIPTION": {
            "default": "",
            "deprecated": False,
            "example": "",
            "ge": None,
            "gt": None,
            "le": None,
            "lt": None,
            "regex": None,
            "required": False,
            "type": "string",
        },
        "APP_LOGGER_ENABLED": {
            "default": 1,
            "deprecated": False,
            "example": 1,
            "ge": None,
            "gt": None,
            "le": None,
            "lt": None,
            "regex": None,
            "required": False,
            "type": "boolean",
        },
        "APP_LOGGER_LEVEL": {
            "default": "INFO",
            "deprecated": False,
            "enum": ["INFO", "WARN", "CRIT"],
            "example": "INFO",
            "ge": None,
            "gt": None,
            "le": None,
            "lt": None,
            "regex": None,
            "required": False,
            "type": "string",
        },
        "APP_NAME": {
            "deprecated": False,
            "example": "",
            "ge": None,
            "gt": None,
            "le": None,
            "lt": None,
            "regex": r"\w+",
            "required": True,
            "type": "string",
        },
        "APP_SERVER_HOST": {
            "default": "127.0.0.1",
            "deprecated": False,
            "example": "127.0.0.1",
            "format": "ipv4",
            "ge": None,
            "gt": None,
            "le": None,
            "lt": None,
            "regex": None,
            "required": False,
            "type": "string",
        },
        "APP_SERVER_PATH": {
            "default": "/",
            "deprecated": True,
            "example": "/",
            "ge": None,
            "gt": None,
            "le": None,
            "lt": None,
            "regex": None,
            "required": False,
            "type": "string",
        },
        "APP_SERVER_PORT": {
            "default": 8080,
            "deprecated": False,
            "description": "TCP/IP port",
            "example": "9090",
            "ge": 1,
            "gt": None,
            "le": 65535,
            "lt": None,
            "regex": None,
            "required": False,
            "type": "integer",
        },
        "APP_SERVER_TIMEOUT": {
            "default": 60.0,
            "deprecated": False,
            "example": 60.0,
            "ge": None,
            "gt": None,
            "le": None,
            "lt": None,
            "regex": None,
            "required": False,
            "type": "number",
        },
        "APP_UUID": {
            "deprecated": False,
            "example": "6eca48d9-3abf-46f8-876a-96cb29e0862b",
            "format": "uuid",
            "ge": None,
            "gt": None,
            "le": None,
            "lt": None,
            "regex": None,
            "required": True,
            "type": "string",
        },
    }


def test_to_env(capsys) -> None:
    config = Config.from_env(prefix="app_")
    assert dict(config.to_env()) == {
        "DB_URL": "postgres://user:pass@localhost:5432/db",
        "DB1_URL": "postgres://user:pass@localhost:8001/db",
        "DB2_URL": "postgres://user:pass@localhost:9002/db",
        "ZIPKIN_LEVEL": "INFO",
        "ZIPKIN_URL": "http://jaeger:9411",
        "PROMETHEUS_LEVEL": "DEBUG",
        "PROMETHEUS_URL": "http://prometheus:9213",
    }

    capsys.readouterr()  # hide stdout
