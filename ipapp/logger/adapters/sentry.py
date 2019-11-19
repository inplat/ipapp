from typing import Optional

import sentry_sdk
from sentry_sdk.api import capture_exception
from sentry_sdk.client import Client
from sentry_sdk.hub import Hub

import ipapp.logger  # noqa

from ..span import Span
from ._abc import AbcAdapter, AbcConfig


class SentryConfig(AbcConfig):
    dsn: str


class SentryAdapter(AbcAdapter):
    name = 'prometheus'
    cfg: SentryConfig
    logger: 'ipapp.logger.Logger'

    def __init__(self) -> None:
        self.client: Optional[Client] = None

    async def start(
        self, logger: 'ipapp.logger.Logger', cfg: AbcConfig
    ) -> None:
        if not isinstance(cfg, SentryConfig):
            raise UserWarning
        self.cfg = cfg
        self.logger = logger

        self.client = Client(dsn=self.cfg.dsn)
        Hub.current.bind_client(self.client)

    def handle(self, span: Span) -> None:
        if span.get_error() is not None:
            capture_exception(span.get_error())

    async def stop(self) -> None:
        if self.logger is not None and self.client is not None:
            await self.logger.app.loop.run_in_executor(None, self.client.close)
