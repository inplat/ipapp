from typing import List
from typing import Optional, Dict

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import BaseModel

from ipapp import Application
from ipapp.logger import Span
from ipapp.logger.adapters import (
    ZipkinConfig,
    ZipkinAdapter,
    AdapterConfigurationError,
)


class EndpointModel(BaseModel):
    serviceName: str


class Annotation(BaseModel):
    value: str
    timestamp: int


class SpanModel(BaseModel):
    traceId: str
    name: str
    parentId: Optional[str]
    id: str
    timestamp: int
    duration: int
    debug: bool
    shared: bool
    tags: Dict[str, str]
    annotations: List[Annotation]
    localEndpoint: EndpointModel
    remoteEndpoint: EndpointModel


class ZipkinServer:

    def __init__(self):
        self.app = web.Application()
        self.app.router.add_post('/api/v2/spans', self.tracer_handle)
        self.server: Optional[TestServer] = None
        self.addr: Optional[str] = None
        self.spans: List[SpanModel] = []
        self.err: Optional[Exception] = None

    async def __aenter__(self):
        self.server = TestServer(self.app, port=None)
        await self.server.start_server(loop=self.app.loop)
        self.addr = 'http://127.0.0.1:%d/api/v2/spans' % self.server.port
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.server.close()
        if self.err is not None:
            raise self.err

    async def tracer_handle(self, request):
        try:
            for item in await request.json():
                self.spans.append(SpanModel(**item))
            return web.Response(text='', status=201)
        except Exception as err:
            self.err = err


async def test_success():
    async with ZipkinServer() as zs:
        cfg = ZipkinConfig(name='123', addr=zs.addr)
        app = Application()
        lgr = app.logger
        lgr.add(cfg)

        adapter = lgr.adapters[0]
        assert isinstance(adapter, ZipkinAdapter)

        await lgr.start()

        with lgr.span_new(name='t1', kind=Span.KIND_SERVER) as sp:
            sp.tag('tag', 'abc')
            sp.annotate('k1', 'val1', ts=123456)

            with pytest.raises(Exception):
                with sp.new_child('t2', Span.KIND_CLIENT):
                    raise Exception()

        await lgr.stop()

    assert len(zs.spans) == 2

    span: SpanModel = zs.spans[1]
    span2: SpanModel = zs.spans[0]
    assert span.name == 't1'
    assert span.tags == {'tag': 'abc'}
    assert span.annotations == [
        Annotation(value='val1', timestamp=123456000000)]
    assert span.duration > 0
    assert span.timestamp > 0
    assert not span.debug
    assert span.shared

    assert span2.name == 't2'
    assert span2.tags == {'error': 'true', 'error.class': 'Exception',
                          'error.message': ''}
    assert len(span2.annotations) == 1
    assert 'Traceback ' in span2.annotations[0].value


async def test_errors():
    app = Application()
    lgr = app.logger
    adapter = ZipkinAdapter()
    with pytest.raises(AdapterConfigurationError):
        adapter.handle(lgr.span_new())

    with pytest.raises(AdapterConfigurationError):
        await adapter.stop()
