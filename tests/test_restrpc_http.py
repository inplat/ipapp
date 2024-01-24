import asyncio
import base64
import os
from datetime import date
from typing import Any, Awaitable, Dict, List, Optional

import pytest
from aiohttp import ClientSession, ClientTimeout
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from ipapp import BaseApplication, BaseConfig
from ipapp.db.pg import Postgres, PostgresConfig
from ipapp.http.server import Server, ServerConfig
from ipapp.misc import BASE64_MARKER
from ipapp.rpc import RpcRegistry
from ipapp.rpc.error import InvalidArguments
from ipapp.rpc.restrpc import RestRpcError
from ipapp.rpc.restrpc.http import (
    RestRpcHttpClient,
    RestRpcHttpClientConfig,
    RestRpcHttpHandler,
    RestRpcHttpHandlerConfig,
    del_response_cookie,
    set_reponse_header,
    set_response_cookie,
)


class RunAppCtx:
    def __init__(self, app):  # type: ignore
        self.app = app

    async def __aenter__(self):  # type: ignore
        await self.app.start()
        return self.app

    async def __aexit__(self, exc_type, exc_val, exc_tb):  # type: ignore
        await self.app.stop()


def runapp(
    port,
    handler,
    postgres_url: Optional[str] = None,
    shield: Optional[bool] = False,
):  # type: ignore
    class Cfg(BaseConfig):
        srv: ServerConfig

    class App(BaseApplication):
        def __init__(self, cfg: Cfg):
            super().__init__(cfg)
            self.add('srv', Server(cfg.srv, handler))
            self.add(
                'clt',
                RestRpcHttpClient(
                    RestRpcHttpClientConfig(
                        url='http://%s:%s/' % (cfg.srv.host, cfg.srv.port)
                    )
                ),
            )
            if postgres_url:
                self.add(
                    'db',
                    Postgres(
                        PostgresConfig(
                            url=postgres_url, log_result=True, log_query=True
                        )
                    ),
                    stop_after=['srv'],
                )

        @property
        def clt(self):  # type: ignore
            return self.get('clt')

        @property
        def db(self) -> Postgres:
            return self.get('db')  # type: ignore

    app = App(Cfg(**{'srv': {'port': port, 'shield': shield}}))

    return RunAppCtx(app)


async def test_rpc(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()

    @reg.method()
    def method1(a: Any):  # type: ignore
        return {'status': 'ok'}

    async with runapp(
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/method1/' % unused_tcp_port,
                json={'a': 'anything'},  # with / in the end of path
            )

            result = await resp.json()
            assert resp.status == 200
            assert resp.reason == 'OK'
            assert resp.headers.get('Content-Type', None) == 'application/json'
            assert result == {'status': 'ok'}

            resp_2 = await sess.request(
                'POST',
                'http://127.0.0.1:%s/method1' % unused_tcp_port,
                json={'a': 'anything'},  # without / in the end of path
            )

            result_2 = await resp_2.json()
            assert result_2 == {'status': 'ok'}

            resp_3 = await sess.request(
                'POST',
                'http://127.0.0.1:%s/nonexistentmethod' % unused_tcp_port,
                json={'a': 'anything'},
            )
            assert resp_3.status == 404
            assert resp_3.reason == 'Not Found'

            resp_4 = await sess.request(
                'POST',
                'http://127.0.0.1:%s/method1' % unused_tcp_port,
                json={},  # empty params
            )
            assert resp_4.status == 400
            assert resp_4.reason == 'Bad Request'
            res_4 = await resp_4.json()
            assert res_4 == {
                'error': {
                    'message': 'Invalid params',
                    'code': 400,
                    'data': 'Missing required params in request',
                }
            }

            resp_5 = await sess.request(
                'POST',
                'http://127.0.0.1:%s/method1' % unused_tcp_port,
                json={'wrong_param': 'anything'},  # wrong params
            )
            assert resp_5.status == 400
            assert resp_5.reason == 'Bad Request'
            res_5 = await resp_5.json()
            assert res_5 == {
                'error': {
                    'message': 'Invalid params',
                    'code': 400,
                    'data': {
                        'info': 'Got an unexpected argument: wrong_param'
                    },
                }
            }

            resp_6 = await sess.request(
                'POST',
                'http://127.0.0.1:%s/method1'
                % unused_tcp_port,  # without params
            )
            assert resp_6.status == 400
            assert resp_6.reason == 'Bad Request'
            res_6 = await resp_6.json()
            assert res_6 == {'error': {'code': 400, 'message': 'Bad Request'}}


async def test_rpc_error(unused_tcp_port):  # type: ignore
    class MyError(RestRpcError):
        code = 1000
        message = 'Err'

    class MyError409(RestRpcError):
        code = 409
        message = 'Err'

    reg = RpcRegistry()

    @reg.method()
    def method1(a: Any):  # type: ignore
        raise MyError(data={'a': 1})

    @reg.method()
    def method2(a: Any):  # type: ignore
        raise MyError409(data={'b': 2})

    async with runapp(
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/method1/' % unused_tcp_port,
                json={'a': 'anything'},
            )
            result = await resp.json()
            assert resp.status == 200
            assert resp.reason == 'OK'
            assert result == {
                'error': {'code': 1000, 'message': 'Err', 'data': {'a': 1}},
            }
            resp_2 = await sess.request(
                'POST',
                'http://127.0.0.1:%s/method2/' % unused_tcp_port,
                json={'a': 'anything'},
            )
            result_2 = await resp_2.json()
            assert result_2 == {
                'error': {'code': 409, 'message': 'Err', 'data': {'b': 2}},
            }
            assert resp_2.status == 409
            assert resp_2.reason == 'Conflict'


async def test_rpc_client(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()

    @reg.method()
    def method1(a: Any):  # type: ignore
        return {'method1': 'ok'}

    async with runapp(
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ) as app:
        result = await app.clt.exec('method1', {'a': 'anything'})
        assert result == {'method1': 'ok'}


async def test_rpc_client_info_field(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()

    @reg.method()
    def sum(a: int, b: int = 3) -> Dict[str, int]:
        return {'sum': a + b}

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

    class TestRpcClientInfoField(RestRpcHttpClient):
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
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ) as app:
        clt = TestRpcClientInfoField(
            RestRpcHttpClientConfig(
                url='http://%s:%s/' % (app.cfg.srv.host, app.cfg.srv.port)
            )
        )
        app.add('clt_if', clt)
        await clt.prepare()
        await clt.start()
        result = await clt.sum(a=10)
        assert result == {'sum': 15}


async def test_rpc_client_info_field_missed_argument(unused_tcp_port):  # type: ignore
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

    class TestRpcClientInfoField(RestRpcHttpClient):
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
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ) as app:
        clt = TestRpcClientInfoField(
            RestRpcHttpClientConfig(
                url='http://%s:%s/' % (app.cfg.srv.host, app.cfg.srv.port)
            )
        )
        app.add('clt_if', clt)
        await clt.prepare()
        await clt.start()
        with pytest.raises(InvalidArguments):
            await clt.sum()


async def test_rpc_client_timeout(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()

    @reg.method()
    async def method1(a: Any):  # type: ignore
        await asyncio.sleep(10)
        return {'method1': 'sleep(10)'}

    @reg.method()
    async def method2(a: Any):  # type: ignore
        await asyncio.sleep(0.2)
        return {'method2': 'sleep(0.2)'}

    async with runapp(
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ) as app:
        with pytest.raises(asyncio.TimeoutError):
            await app.clt.exec('method1', {'a': 'anything'}, timeout=0.2)

        await app.clt.exec(
            'method2', {'a': 'anything'}, timeout=0
        )  # no timeout


async def test_rpc_client_custom_error(unused_tcp_port):  # type: ignore
    class MyErrr(RestRpcError):
        code = 1000
        message = "My err {some_var} {some_else}"

    reg = RpcRegistry()

    @reg.method()
    async def method(a: Any):  # type: ignore
        raise MyErrr(some_var=123, data={'a': 1})

    async with runapp(
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ) as app:
        try:
            await app.clt.exec('method', {'a': 'anything'})
        except RestRpcError as err:
            assert err.code == 1000
            assert err.message == "My err 123 "
            assert err.data == {'a': 1}
        else:
            assert False


async def test_rpc_response_header(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()

    @reg.method()
    def method1(a: Any):  # type: ignore
        set_reponse_header('A', 'B')
        set_reponse_header('C', 'D')
        set_response_cookie('E', 'F')
        del_response_cookie('G')
        return {'method1': 'ok'}

    async with runapp(
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/method1' % unused_tcp_port,
                json={'a': 'anything'},
            )

            assert resp.headers['A'] == 'B'
            assert resp.headers['C'] == 'D'
            assert resp.cookies['E'].value == 'F'
            assert resp.cookies['G'].value == ''

            resp_2 = await sess.request(
                'OPTIONS', 'http://127.0.0.1:%s/method1' % unused_tcp_port
            )

            assert (
                resp_2.headers['Access-Control-Allow-Methods']
                == 'OPTIONS, POST'
            )
            assert resp_2.status == 200
            assert resp_2.reason == 'OK'


async def test_rpc_client_arg_as_bytes(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()
    some_data = os.urandom(100)

    @reg.method()
    def compare_bytes(b_data: bytes) -> Dict[str, bool]:
        return {'compare_bytes': some_data == b_data}

    class TestRpcClientBytesArg(RestRpcHttpClient):
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
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ) as app:
        clt = TestRpcClientBytesArg(
            RestRpcHttpClientConfig(
                url='http://%s:%s/' % (app.cfg.srv.host, app.cfg.srv.port)
            )
        )
        app.add('clt_ba', clt)
        await clt.prepare()
        await clt.start()
        result = await clt.compare_bytes(b_data=some_data)
        assert result == {'compare_bytes': True}


async def test_rpc_client_model_with_bytes(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()
    some_data = os.urandom(100)

    class SomeModel(BaseModel):
        some_int: int
        some_bytes: bytes

    class SomeModel_2(BaseModel):
        compare_model_bytes: bool

    @reg.method()
    def compare_model_bytes(model: SomeModel) -> SomeModel_2:
        compare = some_data == model.some_bytes
        return SomeModel_2(compare_model_bytes=compare)

    class TestRpcClientBytesArg(RestRpcHttpClient):
        def compare_model_bytes(
            self,
            model: SomeModel,
            timeout: Optional[float] = None,
        ) -> Awaitable[bool]:
            return self.exec(
                "compare_model_bytes",
                {'model': model},
                timeout=timeout,
                model=SomeModel_2,
            )

    async with runapp(
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ) as app:
        clt = TestRpcClientBytesArg(
            RestRpcHttpClientConfig(
                url='http://%s:%s/' % (app.cfg.srv.host, app.cfg.srv.port)
            )
        )
        app.add('clt_ba', clt)
        await clt.prepare()
        await clt.start()
        result = await clt.compare_model_bytes(
            SomeModel(some_int=5, some_bytes=some_data)
        )
        assert result == {'compare_model_bytes': True}


async def test_rpc_bytes_in_response(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()
    some_data = os.urandom(100)

    @reg.method()
    def get_some_data(a: Any):  # type: ignore
        return {'some_data': some_data}

    async with runapp(
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/get_some_data' % unused_tcp_port,
                json={'a': 'anything'},
            )

            result = await resp.json()
            expected = {
                'some_data': f'{BASE64_MARKER}{base64.b64encode(some_data).decode()}'
            }
            assert result == expected


def runapp_with_any_subpath(port, handler):  # type: ignore
    class Cfg(BaseConfig):
        srv: ServerConfig

    class App(BaseApplication):
        def __init__(self, cfg: Cfg):
            super().__init__(cfg)
            self.add('srv', Server(cfg.srv, handler))
            self.add(
                'clt',
                RestRpcHttpClient(
                    RestRpcHttpClientConfig(
                        url='http://%s:%s/api/v1'
                        % (cfg.srv.host, cfg.srv.port)
                    )
                ),
            )

        @property
        def clt(self):  # type: ignore
            return self.get('clt')

    app = App(Cfg(**{'srv': {'port': port}}))
    return RunAppCtx(app)


async def test_rpc_with_any_subpath(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()

    @reg.method()
    def method1(a: Any):  # type: ignore
        return {'status': 'ok'}

    async with runapp_with_any_subpath(
        unused_tcp_port,
        RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig(path='/api/v1/')),
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/api/v1/method1/' % unused_tcp_port,
                json={'a': 'anything'},
            )

            result = await resp.json()
            assert resp.status == 200
            assert resp.reason == 'OK'
            assert resp.headers.get('Content-Type', None) == 'application/json'
            assert result == {'status': 'ok'}

            resp_2 = await sess.request(
                'POST',
                'http://127.0.0.1:%s/api/v1/method1' % unused_tcp_port,
                json={'a': 'anything'},
            )

            result_2 = await resp_2.json()
            assert result_2 == {'status': 'ok'}

            resp_3 = await sess.request(
                'POST',
                'http://127.0.0.1:%s/api/v1/nonexistentmethod'
                % unused_tcp_port,
                json={'a': 'anything'},
            )
            assert resp_3.status == 404
            assert resp_3.reason == 'Not Found'

            resp_4 = await sess.request(
                'POST',
                'http://127.0.0.1:%s/api/v0/method1' % unused_tcp_port,
                json={'a': 'anything'},
            )
            assert resp_4.status == 404
            assert resp_4.reason == 'Not Found'


async def test_rpc_client_with_any_subpath(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()

    @reg.method()
    def method1(a: List[int], date: date):  # type: ignore
        return {'status': 'ok', 'a': a, 'date': date}

    async with runapp_with_any_subpath(
        unused_tcp_port,
        RestRpcHttpHandler(
            reg,
            RestRpcHttpHandlerConfig(
                path='/api/v1',
                cors_enabled=False,
            ),
        ),
    ) as app:
        result = await app.clt.exec(
            'method1', {'a': [1, 2, 3], 'date': "2020-09-09"}
        )
        assert result == {'status': 'ok', 'a': [1, 2, 3], 'date': '2020-09-09'}


TEST_EXAMLE: List[Dict[str, Any]] = [
    {
        "name": "TEST_EXAMLE",
        "description": "TEST_EXAMLE TEST_EXAMLE",
        "params": [{"name": "a", "value": "anystr"}],
        "result": [{"name": "status", "value": True}],
    },
    {
        "name": "TEST_EXAMLE 2",
        "description": "TEST_EXAMLE TEST_EXAMLE 2",
        "params": [{"name": "a", "value": "anystr 2"}],
        "result": [{"name": "status", "value": False}],
    },
]


async def test_openapi(unused_tcp_port):  # type: ignore
    reg = RpcRegistry()

    class MethodResponse(BaseModel):
        status: bool

    @reg.method(examples=TEST_EXAMLE)
    def method(a: str) -> MethodResponse:
        '''Docstring for method.'''
        return MethodResponse(status=True)

    async with runapp(
        unused_tcp_port, RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig())
    ):
        async with ClientSession() as sess:
            resp = await sess.request(
                'POST',
                'http://127.0.0.1:%s/method/' % unused_tcp_port,
                json={'a': 'anything'},
            )

            result = await resp.json()
            assert resp.status == 200
            assert resp.reason == 'OK'
            assert resp.headers.get('Content-Type', None) == 'application/json'
            assert result == {'status': True}

            openapi_json = await sess.request(
                'GET',
                'http://127.0.0.1:%s/openapi.json' % unused_tcp_port,
            )

            result_openapi_json = await openapi_json.text()
            assert openapi_json.status == 200
            assert (
                openapi_json.headers.get('Content-Type', None)
                == 'application/json'
            )
            assert all(
                [
                    _ in result_openapi_json
                    for _ in (
                        'Method0ExampleResponse',
                        'Method0ExampleRequest',
                        'Method1ExampleResponse',
                        'Method1ExampleRequest',
                        'MethodResponse',
                        'MethodRequest',
                        'a',
                        'anystr',
                        'anystr 2',
                        'Docstring for method',
                    )
                ]
            )

            swagger_url = await sess.request(
                'GET',
                'http://127.0.0.1:%s/swagger' % unused_tcp_port,
            )
            assert swagger_url.status == 200

            redoc_url = await sess.request(
                'GET',
                'http://127.0.0.1:%s/redoc' % unused_tcp_port,
            )
            assert redoc_url.status == 200

            openapi_yaml = await sess.request(
                'GET',
                'http://127.0.0.1:%s/openapi.yaml' % unused_tcp_port,
            )

            result_openapi_yaml = await openapi_yaml.text()
            assert openapi_yaml.status == 200
            assert (
                openapi_yaml.headers.get('Content-Type', None)
                == 'application/yaml'
            )
            assert all(
                [
                    _ in result_openapi_yaml
                    for _ in (
                        'Method0ExampleResponse',
                        'Method0ExampleRequest',
                        'Method1ExampleResponse',
                        'Method1ExampleRequest',
                        'MethodResponse',
                        'MethodRequest',
                        'a',
                        'anystr',
                        'anystr 2',
                        'Docstring for method',
                    )
                ]
            )


@pytest.mark.parametrize("shield, result", [(True, True), (False, False)])
async def test_server_graceful_shutdown(
    unused_tcp_port,
    postgres_url,
    shield,
    result,
):
    reg = RpcRegistry()
    futs = []

    req = {'a': 'anything'}
    url = 'http://127.0.0.1:%s/method/' % unused_tcp_port

    @reg.method()
    async def method(a: Any):
        fut = asyncio.Future()
        futs.append(fut)
        await asyncio.sleep(app.sleep)
        await app.db.execute('SELECT 1')
        fut.set_result(a)
        return

    async def send_request_timeout(timeout=None):
        async with ClientSession() as client:
            try:
                await client.post(
                    url,
                    json=req,
                    raise_for_status=False,
                    timeout=ClientTimeout(total=timeout),
                )
            except asyncio.TimeoutError:
                pass

    async with runapp(
        unused_tcp_port,
        RestRpcHttpHandler(reg, RestRpcHttpHandlerConfig()),
        postgres_url=postgres_url,
        shield=shield,
    ) as app:
        app.sleep = 4
        await send_request_timeout(timeout=1)

        app.sleep = 0
        await send_request_timeout()

    assert len(futs)
    assert all([fut.done() for fut in futs]) == result
