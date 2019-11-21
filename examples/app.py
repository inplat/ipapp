import logging
import random
from typing import Optional

from aiohttp import ClientResponse, web
from yarl import URL

from ipapp import Application
from ipapp.ctx import span
from ipapp.db.pg import PgSpan, Postgres, PostgresConfig
from ipapp.http.client import Client
from ipapp.http.server import Server, ServerHandler, ServerHttpSpan
from ipapp.logger.adapters.prometheus import (
    PrometheusAdapter,
    PrometheusConfig,
)
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.mq.pika import Deliver, Pika, PikaChannel, PikaConfig, Properties

SPAN_TAG_WIDGET_ID = 'api.widget_id'

rnd = random.SystemRandom()


class InplatSiteClient(Client):
    def __init__(self, base_url: str):
        self.base_url = URL(base_url)

    async def get_home_page(self) -> ClientResponse:
        return await self.request(
            'GET', self.base_url.with_query({'passwd': 'some secret'})
        )


class HttpHandler(ServerHandler):
    app: 'App'

    async def prepare(self) -> None:
        self._setup_healthcheck('/health')
        self.server.add_route('GET', '/inplat', self.inplat_handler)
        self.server.add_route('GET', '/err', self.bad_handler)
        self.server.add_route('GET', '/view/{id}', self.view_handler)
        self.server.add_route('GET', '/', self.home_handler)

    async def error_handler(
        self, request: web.Request, err: Exception
    ) -> web.Response:
        span.error(err)
        self.app.log_err(err)
        return web.Response(text='%r' % err, status=500)

    async def inplat_handler(self, request: web.Request) -> web.Response:
        resp = await self.app.inplat.get_home_page()
        html = await resp.text()
        return web.Response(text=html)

    async def home_handler(self, request: web.Request) -> web.Response:
        span.tag(SPAN_TAG_WIDGET_ID, request.query.get('widget_id'))
        span.name = 'call something'

        async with self.app.db.connection() as conn:
            async with conn.xact():
                st = await conn.prepare(
                    'SELECT $1::int as i', query_name='test prepare'
                )

                print(await st.query_all(1))

                with self.app.logger.capture_span(cls=PgSpan) as trap2:
                    with self.app.logger.capture_span(cls=PgSpan) as trap1:
                        print(await st.query_all(2))
                        if trap1.span:
                            trap1.span.tag('mytag', 'tag1')
                    trap2.span.tag('mytag2', 'tag2')
                    trap2.span.name = 'db::custom'

                res = await conn.query_all('SELECT $1::int as i', 12)
                print(res)

                await self.app.rmq_pub.publish(
                    'myexchange', '', b'hello world', mandatory=True
                )

        return web.Response(text='OK')

    async def view_handler(self, request: web.Request) -> web.Response:
        return web.Response(text='view %s' % request.match_info['id'])

    async def bad_handler(self, request: web.Request) -> web.Response:
        return web.Response(text=str(1 / 0))


class PubCh(PikaChannel):
    name = 'pub'


class ConsCh(PikaChannel):
    name = 'cons'

    async def prepare(self) -> None:
        await self.exchange_declare('myexchange', durable=False)
        await self.queue_declare('myqueue', durable=False)
        await self.queue_bind('myqueue', 'myexchange', '')
        await self.qos(prefetch_count=1)

    async def start(self) -> None:
        print('START', self.name)
        await self.consume('myqueue', self.message)
        print('START 2', self.name)

    async def message(
        self, body: bytes, deliver: Deliver, proprties: Properties
    ) -> None:
        print('message')
        print('-', body)
        print('-', deliver)
        print('-', proprties)
        await self.ack(delivery_tag=deliver.delivery_tag)
        await self.cancel()

    async def stop(self) -> None:
        print('STOP', self.name)

    # async def start(self):
    #     await super().start()
    #     await self.consume(self._cb, 'test')
    #
    # async def _cb(
    #     self,
    #     body: bytes,
    #     method: pika.spec.Basic.Deliver,
    #     properties: pika.spec.BasicProperties,
    # ):
    #     print('*** MSG ***')
    #     print(self.channel)
    #     print(method)
    #     print(properties)
    #     print(body)
    #     self.ack(method.delivery_tag)


class App(Application):
    def __init__(self) -> None:
        super().__init__()

        self._version = '0.0.0.1'
        self._build_stamp = 1573734614

        self.add('srv', Server(HttpHandler(), host='127.0.0.1', port=8888,))

        self.add(
            'rmq',
            Pika(
                PikaConfig(url='amqp://guest:guest@localhost:9004/'),
                [PubCh, ConsCh, ConsCh],
            ),
        )

        self.add('inplat', InplatSiteClient(base_url='https://inplat.ru/123'))

        self.add(
            'db',
            Postgres(
                PostgresConfig(
                    url='postgres://ipapp:secretpwd@localhost:9001'
                    '/ipapp?application_name=ipapp',
                    pool_min_size=1,
                )
            ),
            stop_after=['srv'],
        )

        self.logger.add(
            PrometheusAdapter(
                PrometheusConfig(
                    hist_labels={
                        ServerHttpSpan.P8S_NAME: {
                            'widget_id': SPAN_TAG_WIDGET_ID
                        }
                    }
                )
            )
        )

        self.logger.add(
            ZipkinAdapter(
                ZipkinConfig(
                    name='testapp', addr='http://127.0.0.1:9002/api/v2/spans'
                )
            )
        )

        self.logger.add(
            SentryAdapter(
                SentryConfig(
                    dsn="http://0e1fcbe44a5541c2bd20ed5ead2ca033"
                    "@localhost:9000/2"
                )
            )
        )

        self.logger.add(
            RequestsAdapter(
                RequestsConfig(
                    dsn='postgres://ipapp:secretpwd@localhost:9001/ipapp'
                )
            )
        )

    @property
    def srv(self) -> Server:
        cmp: Optional[Server] = self.get('srv')  # type: ignore
        if cmp is None:
            raise AttributeError
        return cmp

    @property
    def inplat(self) -> InplatSiteClient:
        cmp: Optional[InplatSiteClient] = self.get('inplat')  # type: ignore
        if cmp is None:
            raise AttributeError
        return cmp

    @property
    def db(self) -> Postgres:
        cmp: Optional[Postgres] = self.get('db')  # type: ignore
        if cmp is None:
            raise AttributeError
        return cmp

    @property
    def rmq(self) -> Pika:
        cmp: Optional[Pika] = self.get('rmq')  # type: ignore
        if cmp is None:
            raise AttributeError
        return cmp

    @property
    def rmq_pub(self) -> 'PubCh':
        ch: Optional['PubCh'] = self.rmq.channel('pub')  # type: ignore
        if ch is None:
            raise AttributeError
        return ch


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = App()
    app.run()
