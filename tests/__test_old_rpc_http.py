from contextlib import asynccontextmanager

from aiohttp import ClientSession
from iprpc import BaseError, method

from ipapp import BaseApplication, BaseConfig
from ipapp.http.server import Server, ServerConfig
from ipapp.rpc.http.server import RpcHandler, RpcHandlerConfig


@asynccontextmanager
async def runapp_ctx(app):
    await app.start()
    try:
        yield app
    finally:
        await app.stop()


def runapp(port, handler):
    class Cfg(BaseConfig):
        srv: ServerConfig

    class App(BaseApplication):
        def __init__(self, cfg: Cfg):
            super().__init__(cfg)
            self.add('srv', Server(cfg.srv, handler))

    app = App(Cfg(**{'srv': {'port': port}}))

    return runapp_ctx(app)


async def test_rpc(unused_tcp_port):
    class Api:
        @method()
        def method1(self):
            return 'ok'

    async with runapp(unused_tcp_port, RpcHandler(Api(), RpcHandlerConfig())):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/' % unused_tcp_port,
                json={'method': 'method1'},
            )
            result = await resp.json()
            assert result == {'code': 0, 'message': 'OK', 'result': 'ok'}


async def test_rpc_error(unused_tcp_port):
    class MyError(BaseError):
        code = 100
        message = 'Err'

    class Api:
        @method()
        def method1(self):
            raise MyError()

    async with runapp(unused_tcp_port, RpcHandler(Api(), RpcHandlerConfig())):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/' % unused_tcp_port,
                json={'method': 'method1'},
            )
            result = await resp.json()
            assert result == {'code': 100, 'message': 'Err', 'details': None}
