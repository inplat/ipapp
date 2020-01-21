import logging
import random
import sys
from typing import Optional

from aiohttp import ClientResponse, web
from iprpc.executor import method
from yarl import URL

from ipapp import BaseApplication
from ipapp.cli import main
from ipapp.config import BaseConfig
from ipapp.ctx import app, span
from ipapp.db.pg import PgSpan, Postgres, PostgresConfig
from ipapp.http.client import Client
from ipapp.http.server import Server, ServerConfig, ServerHandler
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

VERSION = '0.0.0.1'
SPAN_TAG_WIDGET_ID = 'api.widget_id'

rnd = random.SystemRandom()


class ConsumerChannelConfig(PikaChannelConfig):
    queue: str = 'myqueue'


class Config(BaseConfig):
    http1: ServerConfig
    http2: ServerConfig
    http2_rpc: RpcHandlerConfig
    client_rpc: RpcClientConfig
    amqp: PikaConfig
    amqp_ch: ConsumerChannelConfig
    amqp_rpc_clt: RpcClientChannelConfig
    amqp_rpc_srv: RpcServerChannelConfig
    db: PostgresConfig

    log_zipkin: ZipkinConfig
    log_prometheus: PrometheusConfig
    log_sentry: SentryConfig
    log_requests: RequestsConfig


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
                await conn.query_one(
                    'SELECT 1 as a, NOW() as now', query_name='test_one'
                )
                await conn.query_all(
                    'SELECT 1 as a, NOW() as now', query_name='test_all'
                )
                await conn.execute(
                    'SELECT 1 as a, NOW() as now', query_name='test_exec'
                )
                ps = await conn.prepare(
                    'SELECT $1::int as i', query_name='test prepare'
                )
                await ps.query_one(1)
                await ps.query_all(2)

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


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)

        self._version = VERSION
        self._build_stamp = 1573734614

        self.add('srv', Server(cfg.http1, HttpHandler()))

        self.add('rpc', Server(cfg.http2, RpcHandler(Api(), cfg.http2_rpc)))

        self.add('rpc_client', RpcClient(cfg.client_rpc))

        self.add(
            'rmq',
            Pika(
                cfg.amqp,
                [
                    lambda: PubCh(cfg.amqp_ch),
                    lambda: ConsCh(cfg.amqp_ch),
                    lambda: ConsCh(cfg.amqp_ch),
                    lambda: RpcServerChannel(Api(), cfg.amqp_rpc_srv),
                    lambda: RpcClientChannel(cfg.amqp_rpc_clt),
                ],
            ),
        )

        self.add('inplat', InplatSiteClient(base_url='https://inplat.ru/123'))

        self.add(
            'db', Postgres(cfg.db), stop_after=['srv'],
        )

        if cfg.log_prometheus.enabled:
            self.logger.add(PrometheusAdapter(cfg.log_prometheus))
        if cfg.log_zipkin.enabled:
            self.logger.add(ZipkinAdapter(cfg.log_zipkin))
        if cfg.log_sentry.enabled:
            self.logger.add(SentryAdapter(cfg.log_sentry))
        if cfg.log_requests.enabled:
            self.logger.add(RequestsAdapter(cfg.log_requests))

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
    """
    Usage:

    APP_AMQP_URL=amqp://guest:guest@localhost:9004/ \
    APP_DB_URL=postgres://ipapp:secretpwd@localhost:9001/ipapp \
    APP_DB_LOG_QUERY=1 \
    APP_DB_LOG_RESULT=1 \
    APP_HTTP2_PORT=8081 \
    APP_LOG_REQUESTS_ENABLED=1 \
    APP_LOG_REQUESTS_DSN=postgres://ipapp:secretpwd@localhost:9001/ipapp \
    APP_LOG_ZIPKIN_ENABLED=1 \
    APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
    python -m examples.app

    """
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, VERSION, App, Config)
