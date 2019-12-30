import asyncio
from typing import Any, Callable, Coroutine, List, Mapping, Optional, Type

import ipapp.app

from .adapters import (
    ADAPTER_PROMETHEUS,
    ADAPTER_REQUESTS,
    ADAPTER_SENTRY,
    ADAPTER_ZIPKIN,
    AbcAdapter,
)
from .span import Span, SpanTrap


class Logger:
    ADAPTER_ZIPKIN = ADAPTER_ZIPKIN
    ADAPTER_PROMETHEUS = ADAPTER_PROMETHEUS
    ADAPTER_SENTRY = ADAPTER_SENTRY
    ADAPTER_REQUESTS = ADAPTER_REQUESTS

    def __init__(self, app: 'ipapp.app.BaseApplication') -> None:
        self.app = app
        self._configs: List[Coroutine[Any, Any, None]] = []
        self.adapters: List[AbcAdapter] = []
        self.default_sampled = True
        self.default_debug = False
        self._started = False
        self._before_handle_callbacks: List[Callable[[Span], None]] = []

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

    def add(self, adapter: AbcAdapter) -> AbcAdapter:
        if self._started:  # pragma: no cover
            raise UserWarning
        if not isinstance(adapter, AbcAdapter):
            raise UserWarning('Invalid adapter')
        # adapter: AbcAdapter
        # if isinstance(cfg, PrometheusConfig):
        #     adapter = PrometheusAdapter()
        # elif isinstance(cfg, ZipkinConfig):
        #     adapter = ZipkinAdapter()
        # elif isinstance(cfg, SentryConfig):
        #     adapter = SentryAdapter()
        # elif isinstance(cfg, RequestsConfig):
        #     adapter = RequestsAdapter()
        # else:
        #     if adapter_cls is not None:
        #         adapter = adapter_cls()
        #     else:
        #         raise UserWarning('Invalid configuration class')
        self._configs.append(adapter.start(self))
        self.adapters.append(adapter)
        return adapter

    def add_before_handle_cb(self, fn: Callable[[Span], None]) -> None:
        self._before_handle_callbacks.append(fn)

    def handle_span(self, span: Span) -> None:
        for cb in self._before_handle_callbacks:
            cb(span)
        for adapter in self.adapters:
            try:
                adapter.handle(span)
            except Exception as err:  # pragma: no cover
                self.app.log_err(err)

    @staticmethod
    def capture_span(cls: Type[Span]) -> SpanTrap:
        return SpanTrap(cls)
