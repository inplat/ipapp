from typing import List, Optional

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from ipapp import BaseApplication, BaseConfig
from ipapp.logger import Span
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig


class SentryServer:
    def __init__(self):
        self.app = web.Application()
        self.app.middlewares.append(self.handler)

        self.server: Optional[TestServer] = None
        self.addr: Optional[str] = None
        self.err: Optional[Exception] = None
        self.key = '0e1fcbe44a5541c2bd20ed5ead2ca033'
        self.errors: List[dict] = []

    async def __aenter__(self):
        self.server = TestServer(self.app, port=None)
        await self.server.start_server(loop=self.app.loop)
        self.addr = 'http://%s@127.0.0.1:%d/1' % (self.key, self.server.port)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.server.close()
        if self.err is not None:
            raise self.err

    @web.middleware
    async def handler(self, request: web.Request, handler):
        if request.method == 'POST' and request.path == '/api/1/store/':
            self.errors.append(await request.json())

        return web.HTTPOk()


async def test_success():
    async with SentryServer() as ss:
        cfg = SentryConfig(dsn=ss.addr)
        adapter = SentryAdapter(cfg)
        app = BaseApplication(BaseConfig())
        app.logger.add(adapter)
        lgr = app.logger
        await lgr.start()

        with lgr.span_new(name='t1', kind=Span.KIND_SERVER) as span:
            span.tag('tag', 'abc')
            span.annotate('k1', 'val1', ts=123456)

            with pytest.raises(Exception):
                with span.new_child('t2', Span.KIND_CLIENT):
                    raise Exception('bla bla')

        await lgr.stop()

        assert len(ss.errors) == 1
        err = ss.errors[0]

        assert err['level'] == 'error', str(err)
        assert 'exception' in err, str(err)
        assert 'event_id' in err, str(err)
        assert 'timestamp' in err, str(err)
        assert 'breadcrumbs' in err, str(err)
        assert 'contexts' in err, str(err)
        assert 'platform' in err, str(err)

        assert err['exception']['values'][0]['type'] == 'Exception', str(err)
        assert err['exception']['values'][0]['value'] == 'bla bla', str(err)
