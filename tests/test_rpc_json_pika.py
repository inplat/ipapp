from ipapp import BaseApplication, BaseConfig
from ipapp.mq.pika import Pika, PikaConfig
from ipapp.rpc import method
from ipapp.rpc.jsonrpc.error import JsonRpcError
from ipapp.rpc.jsonrpc.mq.pika import (
    RpcClientChannel,
    RpcClientChannelConfig,
    RpcServerChannel,
    RpcServerChannelConfig,
)


class RunAppCtx:
    def __init__(self, app):
        self.app = app

    async def __aenter__(self) -> BaseApplication:
        await self.app.start()
        return self.app

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.app.stop()


def runapp(rabbitmq_url: str, handler: object):
    class Cfg(BaseConfig):
        amqp: PikaConfig
        amqp_srv: RpcServerChannelConfig
        amqp_clt: RpcClientChannelConfig

    class App(BaseApplication):
        def __init__(self, cfg: Cfg):
            super().__init__(cfg)
            self.add(
                'amqp',
                Pika(
                    cfg.amqp,
                    [
                        lambda: RpcServerChannel(handler, cfg.amqp_srv),
                        lambda: RpcClientChannel(cfg.amqp_clt),
                    ],
                ),
            )

        @property
        def amqp(self) -> Pika:
            return self.get('amqp')

        @property
        def clt(self) -> RpcClientChannel:
            return self.amqp.channel('rpc_client')

    app = App(
        Cfg(**{'amqp': {'url': rabbitmq_url}, 'amqp_srv': {}, 'amqp_clt': {}})
    )

    return RunAppCtx(app)


async def test_rpc(rabbitmq_url):
    class Api:
        @method()
        def method1(self, val: str) -> str:
            return 'ok %s' % val

    async with runapp(rabbitmq_url, Api()) as app:
        with app.logger.span_new():
            val = 123
            res = await app.clt.exec('method1', {'val': val}, timeout=2)
            assert res == 'ok %s' % val


async def test_rpc_error(rabbitmq_url):
    class MyError(JsonRpcError):
        jsonrpc_error_code = 111

    class Api:
        @method()
        def method1(self) -> str:
            raise MyError()

    async with runapp(rabbitmq_url, Api()) as app:
        with app.logger.span_new():
            try:
                await app.clt.exec('method1', {}, timeout=2)
            except JsonRpcError as err:
                assert err.jsonrpc_error_code == 111


async def test_rpc_batch(rabbitmq_url):
    class Api:
        @method()
        def method1(self, val: str) -> str:
            return 'ok %s' % val

    async with runapp(rabbitmq_url, Api()) as app:
        with app.logger.span_new():
            res1, res2 = await app.clt.exec_batch(
                app.clt.exec('method1', {'val': 123}),
                app.clt.exec('method1', {'val': 234}),
            )
            assert res1 == 'ok 123'
