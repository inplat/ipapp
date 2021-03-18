import asyncio
import base64
import os
from typing import Any, Awaitable, Optional

import pytest
from aiohttp import ClientSession
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from ipapp import BaseApplication, BaseConfig
from ipapp.http.server import Server, ServerConfig
from ipapp.rpc import RpcRegistry
from ipapp.rpc.error import InvalidArguments
from ipapp.rpc.jsonrpc import JsonRpcError
from ipapp.rpc.jsonrpc.http import (
    JsonRpcHttpClient,
    JsonRpcHttpClientConfig,
    JsonRpcHttpHandler,
    JsonRpcHttpHandlerConfig,
    del_response_cookie,
    set_reponse_header,
    set_response_cookie,
)
from ipapp.rpc.main import BASE64_MARKER


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


async def test_rpc_client_info_field(loop, unused_tcp_port):
    reg = RpcRegistry()

    @reg.method()
    def sum(a: int, b: int = 3) -> int:
        return a + b

    def AField(default: Any) -> Any:
        return FieldInfo(
            default,
            description="A Field",
        )

    def BField(default: Any) -> Any:
        return FieldInfo(
            default,
            description="B Field",
        )

    class TestRpcClientInfoField(JsonRpcHttpClient):
        def sum(
            self,
            a: int = AField(...),
            b: Optional[int] = BField(5),
            timeout: Optional[float] = None,
        ) -> Awaitable[int]:
            return self.exec(
                "sum",
                {'a': a, 'b': b},
                timeout=timeout,
            )

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ) as app:
        clt = TestRpcClientInfoField(
            JsonRpcHttpClientConfig(
                url='http://%s:%s/' % (app.cfg.srv.host, app.cfg.srv.port)
            )
        )
        app.add('clt_if', clt)
        await clt.prepare()
        await clt.start()
        result = await clt.sum(10)
        assert result == 15


async def test_rpc_client_info_field_missed_argument(loop, unused_tcp_port):
    reg = RpcRegistry()

    @reg.method()
    def sum(a: int, b: int = 3) -> int:
        return a + b

    def AField(default: Any) -> Any:
        return FieldInfo(
            default,
            description="A Field",
        )

    def BField(default: Any) -> Any:
        return FieldInfo(
            default,
            description="B Field",
        )

    class TestRpcClientInfoField(JsonRpcHttpClient):
        def sum(
            self,
            a: int = AField(...),
            b: Optional[int] = BField(5),
            timeout: Optional[float] = None,
        ) -> Awaitable[int]:
            return self.exec(
                "sum",
                {'a': a, 'b': b},
                timeout=timeout,
            )

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ) as app:
        clt = TestRpcClientInfoField(
            JsonRpcHttpClientConfig(
                url='http://%s:%s/' % (app.cfg.srv.host, app.cfg.srv.port)
            )
        )
        app.add('clt_if', clt)
        await clt.prepare()
        await clt.start()
        with pytest.raises(InvalidArguments):
            await clt.sum()


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


async def test_rpc_response_header(loop, unused_tcp_port):
    reg = RpcRegistry()

    @reg.method()
    def method1():
        set_reponse_header('A', 'B')
        set_reponse_header('C', 'D')
        set_response_cookie('E', 'F')
        del_response_cookie('G')
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

            assert resp.headers['A'] == 'B'
            assert resp.headers['C'] == 'D'
            assert resp.cookies['E'].value == 'F'
            assert resp.cookies['G'].value == ''


async def test_rpc_client_arg_as_bytes(loop, unused_tcp_port):
    reg = RpcRegistry()
    some_data = os.urandom(100)

    @reg.method()
    def compare_bytes(b_data: bytes) -> bool:
        return some_data == b_data

    class TestRpcClientBytesArg(JsonRpcHttpClient):
        def compare_bytes(
            self,
            b_data: bytes,
            timeout: Optional[float] = None,
        ) -> Awaitable[bool]:
            return self.exec(
                "compare_bytes",
                {'b_data': b_data},
                timeout=timeout,
            )

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ) as app:
        clt = TestRpcClientBytesArg(
            JsonRpcHttpClientConfig(
                url='http://%s:%s/' % (app.cfg.srv.host, app.cfg.srv.port)
            )
        )
        app.add('clt_ba', clt)
        await clt.prepare()
        await clt.start()
        result = await clt.compare_bytes(some_data)
        assert result is True


async def test_rpc_client_model_with_bytes(loop, unused_tcp_port):
    reg = RpcRegistry()
    some_data = os.urandom(100)

    class SomeModel(BaseModel):
        some_int: int
        some_bytes: bytes

    @reg.method()
    def compare_model_bytes(model: SomeModel) -> bool:
        return some_data == model.some_bytes

    class TestRpcClientBytesArg(JsonRpcHttpClient):
        def compare_model_bytes(
            self,
            model: SomeModel,
            timeout: Optional[float] = None,
        ) -> Awaitable[bool]:
            return self.exec(
                "compare_model_bytes",
                {'model': model},
                timeout=timeout,
            )

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ) as app:
        clt = TestRpcClientBytesArg(
            JsonRpcHttpClientConfig(
                url='http://%s:%s/' % (app.cfg.srv.host, app.cfg.srv.port)
            )
        )
        app.add('clt_ba', clt)
        await clt.prepare()
        await clt.start()
        result = await clt.compare_model_bytes(
            SomeModel(some_int=5, some_bytes=some_data)
        )
        assert result is True


async def test_rpc_bytes_in_response(loop, unused_tcp_port):
    reg = RpcRegistry()
    some_data = os.urandom(100)

    @reg.method()
    def get_some_data():
        return some_data

    async with runapp(
        unused_tcp_port, JsonRpcHttpHandler(reg, JsonRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/' % unused_tcp_port,
                json={'method': 'get_some_data', 'jsonrpc': '2.0', 'id': 1},
            )

            result = await resp.json()
            expected = f'{BASE64_MARKER}{base64.b64encode(some_data).decode()}'
            assert result == {'id': 1, 'jsonrpc': '2.0', 'result': expected}
