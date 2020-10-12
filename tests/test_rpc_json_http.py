import asyncio

import pytest
from aiohttp import ClientSession

from ipapp import BaseApplication, BaseConfig
from ipapp.http.server import Server, ServerConfig
from ipapp.rpc import RpcRegistry
from ipapp.rpc.jsonrpc import JsonRpcError
from ipapp.rpc.jsonrpc.http import (
    JsonRpcHttpClient,
    JsonRpcHttpClientConfig,
    JsonRpcHttpHandler,
    JsonRpcHttpHandlerConfig,
)


class RunAppCtx:
    def __init__(self, app):
        self.app = app

    async def __aenter__(self):
        await self.app.start()
        return self.app

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.app.stop()


def runapp(port, handler):
    class Cfg(BaseConfig):
        srv: ServerConfig

    class App(BaseApplication):
        def __init__(self, cfg: Cfg):
            super().__init__(cfg)
            self.add('srv', Server(cfg.srv, handler))
            self.add(
                'clt',
                JsonRpcHttpClient(
                    JsonRpcHttpClientConfig(
                        url='http://%s:%s/' % (cfg.srv.host, cfg.srv.port)
                    )
                ),
            )

        @property
        def clt(self):
            return self.get('clt')

    app = App(Cfg(**{'srv': {'port': port}}))

    return RunAppCtx(app)


async def test_rpc(loop, unused_tcp_port):
    reg = RpcRegistry()

    @reg.method()
    def method1():
        return 'ok'

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/' % unused_tcp_port,
                json={'method': 'method1', 'jsonrpc': '2.0', 'id': 1},
            )

            result = await resp.json()
            assert result == {'id': 1, 'jsonrpc': '2.0', 'result': 'ok'}


async def test_rpc_error(loop, unused_tcp_port):
    class MyError(JsonRpcError):
        jsonrpc_error_code = 100
        message = 'Err'

    reg = RpcRegistry()

    @reg.method()
    def method1():
        raise MyError(data={'a': 1})

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/' % unused_tcp_port,
                json={'method': 'method1', 'jsonrpc': '2.0', 'id': 1},
            )
            result = await resp.json()
            assert result == {
                'id': 1,
                'jsonrpc': '2.0',
                'error': {'code': 100, 'message': 'Err', 'data': {'a': 1}},
            }


async def test_batch(loop, unused_tcp_port):
    reg = RpcRegistry()

    @reg.method()
    def method1():
        return 'ok'

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/' % unused_tcp_port,
                json=[
                    {'method': 'method1', 'jsonrpc': '2.0', 'id': 1},
                    {'method': 'method1', 'jsonrpc': '2.0', 'id': 2},
                ],
            )
            result = await resp.json()
            assert result == [
                {'id': 1, 'jsonrpc': '2.0', 'result': 'ok'},
                {'id': 2, 'jsonrpc': '2.0', 'result': 'ok'},
            ]


async def test_batch_complicated(loop, unused_tcp_port):
    reg = RpcRegistry()

    @reg.method()
    def sum(a: int, b: int):
        return a + b

    @reg.method()
    def subtract(a: int, b: int):
        return a - b

    @reg.method()
    def notify(msg: str):
        return 'ok'

    @reg.method()
    def get_data():
        return [1, 2, 3]

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/' % unused_tcp_port,
                json=[
                    {
                        "jsonrpc": "2.0",
                        "method": "sum",
                        "params": {"a": 1, "b": 2},
                        "id": "1",
                    },
                    {
                        "jsonrpc": "2.0",
                        "method": "notify",
                        "params": {"msg": "hello"},
                    },
                    {
                        "jsonrpc": "2.0",
                        "method": "subtract",
                        "params": {"a": 1, "b": 2},
                        "id": "2",
                    },
                    {"foo": "boo"},
                    {
                        "jsonrpc": "2.0",
                        "method": "foo.get",
                        "params": {"name": "myself"},
                        "id": "5",
                    },
                    {"jsonrpc": "2.0", "method": "get_data", "id": "9"},
                ],
            )
            result = await resp.json()

            assert result == [
                {'jsonrpc': '2.0', 'id': '1', 'result': 3},
                {'jsonrpc': '2.0', 'id': '2', 'result': -1},
                {
                    'jsonrpc': '2.0',
                    'id': None,
                    'error': {'message': 'Invalid Request', 'code': -32600},
                },
                {
                    'jsonrpc': '2.0',
                    'id': '5',
                    'error': {'message': 'Method not found', 'code': -32601},
                },
                {'jsonrpc': '2.0', 'id': '9', 'result': [1, 2, 3]},
            ]


async def test_rpc_client(loop, unused_tcp_port):
    reg = RpcRegistry()

    @reg.method()
    def method1():
        return 'ok'

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ) as app:
        result = await app.clt.exec('method1')
        assert result == 'ok'


async def test_rpc_client_batch(loop, unused_tcp_port):
    reg = RpcRegistry()

    @reg.method()
    def method1(a: int):
        return 'ok%s' % a

    @reg.method()
    def method2(a: int):
        return 'ok%s' % a

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ) as app:
        res1, res2 = await app.clt.exec_batch(
            app.clt.exec('method1', {'a': 1}), app.clt.exec('method2', (2,))
        )
        assert res1 == 'ok1'
        assert res2 == 'ok2'


async def test_rpc_client_timeout(loop, unused_tcp_port):
    reg = RpcRegistry()

    @reg.method()
    async def method1():
        await asyncio.sleep(10)

    @reg.method()
    async def method2():
        await asyncio.sleep(0.2)

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ) as app:
        with pytest.raises(asyncio.TimeoutError):
            await app.clt.exec('method1', timeout=0.2)

        await app.clt.exec('method2', timeout=0)  # no timeout


async def test_rpc_client_custom_error(loop, unused_tcp_port):
    class MyErrr(JsonRpcError):
        jsonrpc_error_code = 100
        message = "My err {some_var} {some_else}"

    reg = RpcRegistry()

    @reg.method()
    async def method():
        raise MyErrr(some_var=123, data={'a': 1})

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ) as app:
        try:
            await app.clt.exec('method')
        except JsonRpcError as err:
            assert err.jsonrpc_error_code == 100
            assert err.message == "My err 123 "
            assert err.data == {'a': 1}
        else:
            assert False


async def test_rpc_as_list(loop, unused_tcp_port):
    reg = RpcRegistry()

    @reg.method()
    def method1():
        return 'ok'

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/' % unused_tcp_port,
                json={'method': 'method1', 'jsonrpc': '2.0', 'id': 1},
            )

            result = await resp.json()
            assert result == {'id': 1, 'jsonrpc': '2.0', 'result': 'ok'}
