import logging
import random
from typing import Optional

from aiohttp import ClientResponse, web
from iprpc.executor import method
from yarl import URL

from ipapp import Application
from ipapp.ctx import app  # noqa
from ipapp.ctx import span
from ipapp.db.pg import PgSpan, Postgres, PostgresConfig
from ipapp.http.client import Client
from ipapp.http.server import (
    Server,
    ServerConfig,
    ServerHandler,
    ServerHttpSpan,
)
from ipapp.logger.adapters.prometheus import (
    PrometheusAdapter,
    PrometheusConfig,
)
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig
from ipapp.logger.adapters.sentry import SentryAdapter, SentryConfig
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.mq.pika import (
    Deliver,
    Pika,
    PikaChannel,
    PikaChannelConfig,
    PikaConfig,
    Properties,
)
from ipapp.rpc.http.client import RpcClient, RpcClientConfig
from ipapp.rpc.http.server import RpcHandler, RpcHandlerConfig
from ipapp.rpc.mq.pika import (
    RpcClientChannel,
    RpcClientChannelConfig,
    RpcServerChannel,
    RpcServerChannelConfig,
)

SPAN_TAG_WIDGET_ID = 'api.widget_id'

rnd = random.SystemRandom()


class ConsumerChannelConfig(PikaChannelConfig):
    queue: str = 'myqueue'


class InplatSiteClient(Client):
    def __init__(self, base_url: str):
        self.base_url = URL(base_url)

    async def get_home_page(self) -> ClientResponse:
        return await self.request(
            'GET', self.base_url.with_query({'passwd': 'some secret'})
        )

    async def req(self, url: URL, method: str, body: bytes) -> ClientResponse:
        return await self.request(method, url, body=body)


class Api:
    @method()
    async def test(self, val1: str, val2: bool, val3: int = 1) -> str:
        app: App
        async with app.db.connection():
            pass
        return 'val1=%r val2=%r val3=%r' % (val1, val2, val3)


class HttpHandler(ServerHandler):
    app: 'App'

    async def prepare(self) -> None:
        self._setup_healthcheck('/health')
        self.server.add_route('GET', '/inplat', self.inplat_handler)
        self.server.add_route('GET', '/proxy', self.proxy_handler)
        self.server.add_route('GET', '/err', self.bad_handler)
        self.server.add_route('GET', '/view/{id}', self.view_handler)
        self.server.add_route('GET', '/rpc/amqp', self.rpc_amqp_handler)
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

    async def proxy_handler(self, request: web.Request) -> web.Response:
        await self.app.inplat.req(
            URL('http://127.0.0.1:%d/' % self.app.srv.port), 'GET', b''
        )
        return web.HTTPOk()

    async def home_handler(self, request: web.Request) -> web.Response:
        span.tag(SPAN_TAG_WIDGET_ID, request.query.get('widget_id'))

        async with self.app.db.connection() as conn:
            async with conn.xact():
                await conn.prepare(
                    'SELECT $1::int as i', query_name='test prepare'
                )

                with self.app.logger.capture_span(cls=PgSpan) as trap2:
                    with self.app.logger.capture_span(cls=PgSpan) as trap1:
                        await conn.query_all('SELECT $1::int as i', 12)
                        if trap1.span:
                            trap1.span.tag('mytag', 'tag1')
                    trap2.span.tag('mytag2', 'tag2')
                    trap2.span.name = 'db::custom'

                await self.app.rmq_pub.publish(
                    'myexchange', '', b'hello world', mandatory=True
                )

        return web.Response(text='OK')

    async def rpc_amqp_handler(self, request: web.Request) -> web.Response:
        res = await self.app.rmq_rpc_client.call(
            'test', {'val1': '1', 'val2': False}, timeout=3.0
        )

        return web.Response(text='%r' % res)

    async def view_handler(self, request: web.Request) -> web.Response:
        return web.Response(text='view %s' % request.match_info['id'])

    async def bad_handler(self, request: web.Request) -> web.Response:
        return web.Response(text=str(1 / 0))


class PubCh(PikaChannel):
    name = 'pub'


class ConsCh(PikaChannel):
    name = 'cons'
    cfg: ConsumerChannelConfig

    async def prepare(self) -> None:
        await self.exchange_declare('myexchange', durable=False)
        await self.queue_declare(self.cfg.queue, durable=False)
        await self.queue_bind(self.cfg.queue, 'myexchange', '')
        await self.qos(prefetch_count=1)

    async def start(self) -> None:
        await self.consume('myqueue', self.message)

    async def message(
        self, body: bytes, deliver: Deliver, proprties: Properties
    ) -> None:
        await self.ack(delivery_tag=deliver.delivery_tag)

        app: App = self.amqp.app  # type: ignore
        await app.rpc_client.call('test', {'val1': '1', 'val2': False})

    async def stop(self) -> None:
        await self.cancel()


class App(Application):
    def __init__(self) -> None:
        super().__init__()

        self._version = '0.0.0.1'
        self._build_stamp = 1573734614

        self.add(
            'srv',
            Server(ServerConfig(host='127.0.0.1', port=8888), HttpHandler()),
        )

        self.add(
            'rpc',
            Server(
                ServerConfig(host='127.0.0.1', port=8889),
                RpcHandler(Api(), RpcHandlerConfig(debug=True)),
            ),
        )

        self.add(
            'rpc_client',
            RpcClient(RpcClientConfig(url='http://127.0.0.1:8889/')),
        )

        self.add(
            'rmq',
            Pika(
                PikaConfig(url='amqp://guest:guest@localhost:9004/'),
                [
                    lambda: PubCh(ConsumerChannelConfig()),
                    lambda: ConsCh(ConsumerChannelConfig()),
                    lambda: ConsCh(ConsumerChannelConfig()),
                    lambda: RpcServerChannel(
                        Api(), RpcServerChannelConfig(queue='rpc')
                    ),
                    lambda: RpcClientChannel(
                        RpcClientChannelConfig(queue='rpc')
                    ),
                ],
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
    def rpc_client(self) -> RpcClient:
        cmp: Optional[RpcClient] = self.get('rpc_client')  # type: ignore
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

    @property
    def rmq_rpc_client(self) -> 'RpcClientChannel':
        ch: Optional['RpcClientChannel'] = self.rmq.channel(  # type: ignore
            'rpc_client'
        )
        if ch is None:
            raise AttributeError
        return ch


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    App().run()
