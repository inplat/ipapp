import asyncio
from asyncio import AbstractEventLoop
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Generator,
    Optional,
)

import asyncpg
import pika
import pytest
from _pytest.config.argparsing import Parser
from _pytest.fixtures import FixtureRequest
from async_timeout import timeout as async_timeout

TIMEOUT = 60
COMPOSE_POSTGRES_URL = 'postgres://ipapp:secretpwd@127.0.0.1:58971/ipapp'
COMPOSE_RABBITMQ_URL = 'amqp://guest:guest@127.0.0.1:58972/'

pytest_plugins = ["pytester"]


def pytest_addoption(parser: Parser) -> None:
    parser.addoption(
        "--postgres-url",
        dest="postgres_url",
        help="Postgres override connection string",
        metavar="postgres://user:pwd@host:port/dbname",
    )
    parser.addoption(
        "--rabbit-url",
        dest="rabbit_url",
        help="RabbitMq override connection string",
        metavar="amqp://user:pwd@host:port/vhost",
    )


@pytest.fixture(scope='session')
def event_loop() -> Generator[AbstractEventLoop, None, None]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope='session')
def loop(event_loop: AbstractEventLoop) -> AbstractEventLoop:
    return event_loop


async def wait_service(
    url: str,
    timeout: float,
    check_fn: Callable[[str], Awaitable[Any]],
    err_template: str,
) -> None:
    last_err: Optional[Exception] = None
    try:
        with async_timeout(timeout):
            while True:
                try:
                    await check_fn(url)
                    break
                except Exception as err:
                    last_err = err
                    await asyncio.sleep(0.1)
    except asyncio.TimeoutError:
        raise TimeoutError(err_template.format(url=url, err=last_err))


@pytest.fixture
async def postgres_url(request: FixtureRequest) -> AsyncGenerator[str, None]:
    url = request.config.getoption('postgres_url')
    if not url:
        url = COMPOSE_POSTGRES_URL
    await wait_service(
        url,
        TIMEOUT,
        asyncpg.connect,
        'Failed to connect to {url}. Last error: {err}',
    )
    yield url


@pytest.fixture
async def rabbitmq_url(request: FixtureRequest) -> AsyncGenerator[str, None]:
    async def check(url: str) -> None:
        await asyncio.get_event_loop().run_in_executor(
            None, pika.BlockingConnection, pika.URLParameters(url)
        )

    url = request.config.getoption('rabbit_url')
    if not url:
        url = COMPOSE_RABBITMQ_URL
    await wait_service(
        url, TIMEOUT, check, 'Failed to connect to {url}. Last error: {err}',
    )
    yield url
