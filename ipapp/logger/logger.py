import asyncio
from typing import Any, Coroutine, List, Mapping, Optional, Type

import ipapp.app

from .adapters import (AbcAdapter, AbcConfig, PrometheusAdapter,
                       PrometheusConfig, RequestsAdapter, RequestsConfig,
                       SentryAdapter, SentryConfig, ZipkinAdapter,
                       ZipkinConfig)
from .span import Span


class Logger:
    ADAPTER_ZIPKIN = ZipkinAdapter.name
    ADAPTER_PROMETHEUS = PrometheusAdapter.name
    ADAPTER_SENTRY = SentryAdapter.name
    ADAPTER_REQUESTS = RequestsAdapter.name

    def __init__(self, app: 'ipapp.app.Application') -> None:
        self.app = app
        self._configs: List[Coroutine[Any, Any, None]] = []
        self.adapters: List[AbcAdapter] = []
        self.default_sampled = True
        self.default_debug = False
        self._started = False

    async def start(self) -> None:
        self._started = True
        await asyncio.gather(*self._configs, loop=self.app.loop)

    async def stop(self) -> None:
        if not self._started:  # pragma: no cover
            raise UserWarning

        await asyncio.gather(
            *[adapter.stop() for adapter in self.adapters], loop=self.app.loop
        )

    @staticmethod
    def span_new(
        name: Optional[str] = None,
        kind: Optional[str] = None,
        cls: Type[Span] = Span,
    ) -> 'Span':
        return cls.new(name=name, kind=kind)

    @staticmethod
    def span_from_headers(
        headers: Mapping[str, str], cls: Type[Span] = Span
    ) -> 'Span':
        return cls.from_headers(headers)

    def add(
        self, cfg: AbcConfig, *, adapter_cls: Optional[Type[AbcAdapter]] = None
    ) -> AbcAdapter:
        if self._started:  # pragma: no cover
            raise UserWarning
        adapter: AbcAdapter
        if isinstance(cfg, PrometheusConfig):
            adapter = PrometheusAdapter()
        elif isinstance(cfg, ZipkinConfig):
            adapter = ZipkinAdapter()
        elif isinstance(cfg, SentryConfig):
            adapter = SentryAdapter()
        elif isinstance(cfg, RequestsConfig):
            adapter = RequestsAdapter()
        else:
            if adapter_cls is not None:
                adapter = adapter_cls()
            else:
                raise UserWarning('Invalid configuration class')
        self._configs.append(adapter.start(self, cfg))
        self.adapters.append(adapter)
        return adapter

    def handle_span(self, span: Span) -> None:
        for adapter in self.adapters:
            try:
                adapter.handle(span)
            except Exception as err:  # pragma: no cover
                self.app.log_err(err)
