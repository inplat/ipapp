import asyncio
import logging
import random
from typing import Optional

from aiohttp import web
from yarl import URL

from ipapp import Application
from ipapp.http import (ServerHandler, Server, ServerHttpSpan, Client)
from ipapp.logger import (PrometheusConfig, ZipkinConfig, SentryConfig,
                          RequestsConfig)
from ipapp.logger import ctx_span_get

SPAN_TAG_WIDGET_ID = 'api.widget_id'


class InplatSiteClient(Client):

    def __init__(self, base_url: str):
        self.base_url = URL(base_url)

    async def get_home_page(self):
        return await self.request('GET',
                                  self.base_url.with_query(
                                      {'passwd': 'some secret'}))


class HttpHandler(ServerHandler):
    app: 'App'

    async def prepare(self) -> None:
        self._setup_healthcheck('/health')
        self.server.add_route('GET', '/inplat', self.inplat_handler)
        self.server.add_route('GET', '/err', self.bad_handler)
        self.server.add_route('GET', '/view/{id}', self.view_handler)
        self.server.add_route('GET', '/', self.home_handler)

    async def error_handler(self, request: web.Request,
                            err: Exception) -> web.Response:
        span = ctx_span_get()
        span.error(err)
        return web.Response(text='%r' % err, status=500)

    async def inplat_handler(self, request: web.Request) -> web.Response:
        resp = await self.app.inplat.get_home_page()
        html = await resp.text()
        return web.Response(text=html)

    async def home_handler(self, request: web.Request) -> web.Response:
        span = ctx_span_get()
        span.tag(SPAN_TAG_WIDGET_ID, request.query.get('widget_id'))
        span.name = 'call something'

        with span.new_child('sleep', span.KIND_CLIENT):
            await asyncio.sleep(random.random())

        return web.Response(text='OK')

    async def view_handler(self, request: web.Request) -> web.Response:
        return web.Response(text='view %s' % request.match_info['id'])

    async def bad_handler(self, request: web.Request) -> web.Response:
        1 / 0


class App(Application):

    def __init__(self):
        super().__init__()

        self._version = '0.0.0.1'
        self._build_stamp = 1573734614

        self.add(
            'srv',
            Server(HttpHandler(), host='127.0.0.1', port=8888, )
        )

        self.add(
            'inplat',
            InplatSiteClient(base_url='https://inplat.ru/123')
        )

        self.logger.add(
            PrometheusConfig(
                hist_labels={
                    ServerHttpSpan.P8S_NAME: {
                        'widget_id': SPAN_TAG_WIDGET_ID}})
        )

        self.logger.add(
            ZipkinConfig(name='testapp',
                         addr='http://127.0.0.1:9002/api/v2/spans')
        )

        self.logger.add(
            SentryConfig(
                dsn="http://0e1fcbe44a5541c2bd20ed5ead2ca033@localhost:9000/2")
        )

        self.logger.add(
            RequestsConfig(
                dsn='postgres://ipapp:secretpwd@localhost:9001/ipapp')
        )

    @property
    def srv(self) -> Server:
        cmp: Optional[Server] = self.get('srv')
        if cmp is None:
            raise AttributeError
        return cmp

    @property
    def inplat(self) -> InplatSiteClient:
        cmp: Optional[InplatSiteClient] = self.get('inplat')
        if cmp is None:
            raise AttributeError
        return cmp


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    app = App()
    app.run()
