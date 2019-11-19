from typing import Optional

import sentry_sdk
from sentry_sdk import capture_exception

import ipapp.logger  # noqa

from ..span import Span
from ._abc import AbcAdapter, AbcConfig


class SentryConfig(AbcConfig):
    dsn: str


class SentryAdapter(AbcAdapter):
    name = 'prometheus'
    cfg: SentryConfig
    logger: 'ipapp.logger.Logger'

    def __init__(self):
        self.client: Optional[sentry_sdk.Client] = None

    async def start(self, logger: 'ipapp.logger.Logger',
                    cfg: SentryConfig):
        self.cfg = cfg
        self.logger = logger

        self.client = sentry_sdk.Client(dsn=self.cfg.dsn)
        sentry_sdk.Hub.current.bind_client(self.client)

    def handle(self, span: Span):
        if span.get_error() is not None:
            capture_exception(span.get_error())

    async def stop(self):
        await self.logger.app.loop.run_in_executor(None, self.client.close)
