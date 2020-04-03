from typing import Optional

from pydantic import Field
from sentry_sdk.api import capture_exception
from sentry_sdk.client import Client
from sentry_sdk.hub import Hub

import ipapp.logger  # noqa

from ..span import Span
from ._abc import AbcAdapter, AbcConfig, AdapterConfigurationError


class SentryConfig(AbcConfig):
    dsn: Optional[str] = Field(
        None,
        description="Строка подключения к Sentry",
        example="https://key@sentry.io/project",
    )


class SentryAdapter(AbcAdapter):
    name = 'prometheus'
    cfg: SentryConfig
    logger: 'ipapp.logger.Logger'

    def __init__(self, cfg: SentryConfig) -> None:
        self.cfg = cfg
        self.client: Optional[Client] = None

    async def start(self, logger: 'ipapp.logger.Logger') -> None:

        self.logger = logger
        if self.cfg.dsn is None:
            raise AdapterConfigurationError(
                '%s dsn is not configured' % self.__class__.__name__
            )
        self.client = Client(dsn=self.cfg.dsn)
        Hub.current.bind_client(self.client)

    def handle(self, span: Span) -> None:
        if span.get_error() is not None:
            capture_exception(span.get_error())

    async def stop(self) -> None:
        if self.logger is not None and self.client is not None:
            await self.logger.app.loop.run_in_executor(None, self.client.close)
