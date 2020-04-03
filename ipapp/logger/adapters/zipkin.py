from typing import Optional

import aiozipkin as az
import aiozipkin.helpers as azh
import aiozipkin.transport as azt
from pydantic import Field

import ipapp.logger  # noqa

from ..span import Span
from ._abc import AbcAdapter, AbcConfig, AdapterConfigurationError


class ZipkinConfig(AbcConfig):
    name: str = Field("ipapp", description="Название сервиса в Zipkin")
    addr: str = Field(
        "http://localhost:9411/api/v2/spans",
        description="Адрес для подключения к Zipkin",
    )
    sample_rate: float = Field(
        0.01,
        description="Процент сохраняемых трассировок. 1.0 - 100%, 0.5 - 50%",
    )
    send_interval: float = Field(
        5, description="Интервал отправки данных в Zipkin"
    )
    default_sampled: bool = Field(
        True, description="Sampled в Span по умолчанию", deprecated=True
    )
    default_debug: bool = Field(
        False, description="Debug в Span по умолчанию", deprecated=True
    )


class ZipkinAdapter(AbcAdapter):
    name = 'zipkin'
    cfg: ZipkinConfig
    logger: 'ipapp.logger.Logger'

    def __init__(self, cfg: ZipkinConfig) -> None:
        self.cfg = cfg
        self.tracer: Optional[az.Tracer] = None

    async def start(self, logger: 'ipapp.logger.Logger') -> None:

        endpoint = az.create_endpoint(self.cfg.name)
        sampler = az.Sampler(sample_rate=self.cfg.sample_rate)
        transport = azt.Transport(
            self.cfg.addr,
            send_interval=self.cfg.send_interval,
            loop=logger.app.loop,
        )
        self.tracer = az.Tracer(transport, sampler, endpoint)

    def handle(self, span: Span) -> None:
        if self.tracer is None:
            raise AdapterConfigurationError(
                '%s is not configured' % self.__class__.__name__
            )

        tracer_span = self.tracer.to_span(
            azh.TraceContext(
                trace_id=span.trace_id,
                parent_id=span.parent_id,
                span_id=span.id,
                sampled=True,
                debug=False,
                shared=True,
            )
        )

        tracer_span.start(ts=span.start_stamp)

        for _tag_name, _tag_val in span.get_tags4adapter(self.name).items():
            tracer_span.tag(_tag_name, _tag_val)
        for _annkind, _anns in span.get_annotations4adapter(self.name).items():
            for _ann, _ts in _anns:
                if _ann is not None:
                    tracer_span.annotate(_ann, _ts)
        if span.kind:
            tracer_span.kind(span.kind)

        name = span.get_name4adapter(self.name, merge=True)
        if name:
            tracer_span.name(name)

        tracer_span.remote_endpoint(self.cfg.name)
        tracer_span.finish(ts=span.finish_stamp)

    async def stop(self) -> None:
        if self.tracer is None:
            raise AdapterConfigurationError(
                '%s is not configured' % self.__class__.__name__
            )

        await self.tracer.close()
