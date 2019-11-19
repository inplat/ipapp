from typing import Optional

import aiozipkin as az
import aiozipkin.span as azs
import aiozipkin.tracer as azt

import ipapp.logger  # noqa

from ..span import Span
from ._abc import AbcAdapter, AbcConfig, AdapterConfigurationError


class ZipkinConfig(AbcConfig):
    name: str
    addr: str = 'http://127.0.0.1:9411/api/v2/spans'
    sample_rate: float = 0.01
    send_interval: float = 5
    default_sampled: bool = True
    default_debug: bool = False


class ZipkinAdapter(AbcAdapter):
    name = 'zipkin'
    cfg: ZipkinConfig
    logger: 'ipapp.logger.Logger'

    def __init__(self):
        self.tracer: Optional[az.Tracer] = None

    async def start(self, logger: 'ipapp.logger.Logger',
                    cfg: ZipkinConfig):
        self.cfg = cfg
        self.logger = logger

        endpoint = az.create_endpoint(cfg.name)
        sampler = az.Sampler(sample_rate=cfg.sample_rate)
        transport = azt.Transport(cfg.addr, send_interval=cfg.send_interval,
                                  loop=logger.app.loop)
        self.tracer = az.Tracer(transport, sampler, endpoint)

    def handle(self, span: Span):
        if self.tracer is None:
            raise AdapterConfigurationError(
                '%s is not configured' % self.__class__.__name__)

        tracer_span = self.tracer.to_span(
            azs.TraceContext(
                trace_id=span.trace_id,
                parent_id=span.parent_id,
                span_id=span.id,
                sampled=True,
                debug=False,
                shared=True

            ))

        tracer_span.start(ts=span.start_stamp)

        for _tag_name, _tag_val in span.get_tags4adapter(self.name).items():
            tracer_span.tag(_tag_name, _tag_val)
        for _annkind, _anns in span.get_annotations4adapter(self.name).items():
            for _ann, _ts in _anns:
                if _ann is not None:
                    tracer_span.annotate(_ann, _ts)
        if span.kind:
            tracer_span.kind(span.kind)

        name = span.get_name4adapter(self.name)
        if name:
            tracer_span.name(name)
        tracer_span.remote_endpoint(self.cfg.name)
        tracer_span.finish(ts=span.finish_stamp)

    async def stop(self):
        if self.tracer is None:
            raise AdapterConfigurationError(
                '%s is not configured' % self.__class__.__name__)

        await self.tracer.close()
