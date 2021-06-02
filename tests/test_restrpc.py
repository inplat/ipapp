import json
from typing import Dict, Optional

import pytest

from ipapp import BaseApplication, BaseConfig
from ipapp.rpc import RpcRegistry
from ipapp.rpc.restrpc import RestRpcClient, RestRpcError, RestRpcExecutor


def get_app() -> BaseApplication:
    return BaseApplication(BaseConfig())


def get_clt(reg: RpcRegistry) -> RestRpcClient:
    app = get_app()

    async def transport(
        request: bytes, method_name: str, timeout: Optional[float]
    ) -> bytes:
        ex = RestRpcExecutor(reg, app)
        res, status = await ex.exec(request, method_name)
        return res

    return RestRpcClient(transport, app)


async def test_success_by_name() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def echo(text: str) -> Dict[str, str]:
        return {'echo': text}

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b' {"text": "123"}', method_name='echo')

    assert json.loads(res) == {'echo': '123'}
    assert status == 200


async def test_success_by_pos() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int) -> Dict[str, int]:
        return {'sum': a + b}

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{"a": 1,"b": 2}', method_name='sum')

    assert status == 200
    assert json.loads(res) == {"sum": 3}


async def test_success_by_name_default() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int, c: int = 3) -> Dict[str, int]:
        return {'sum': a + b + c}

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{"a": 1,"b": 2}', method_name='sum')

    assert json.loads(res) == {"sum": 6}
    assert status == 200


async def test_success_by_pos_default() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int, c: int = 3) -> Dict[str, int]:
        return {'sum': a + b + c}

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{"a": 1,"b": 2}', method_name='sum')

    assert json.loads(res) == {"sum": 6}
    assert status == 200


async def test_err_parse_1() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int, c: int = 3) -> Dict[str, int]:
        return {'sum': a + b + c}

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'eeeee', method_name='sum')

    assert json.loads(res) == {
        'error': {'code': 400, 'message': 'Bad Request'}
    }
    assert status == 400


async def test_err_invalid_req_2() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int, c: int = 3) -> Dict[str, int]:
        return {'sum': a + b + c}

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{"a": 1}', method_name='sum')

    assert json.loads(res) == {
        'error': {
            'code': 400,
            'data': {'info': 'Missing 1 required argument(s):  b'},
            'message': 'Invalid params',
        }
    }
    assert status == 400


async def test_err_invalid_req_3() -> None:  # TODO: fix typing error
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int, c: int = 3) -> Dict[str, int]:
        return {'sum': a + b + c}

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{"a": string,"b": 2}', method_name='sum')

    assert json.loads(res) == {
        'error': {'code': 400, 'message': 'Bad Request'}
    }
    assert status == 400


async def test_error() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def echo(text: str) -> str:
        raise Exception('Ex')

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{"text": "123"}', method_name='echo')
    assert json.loads(res) == {'error': {'code': 500, 'message': 'Ex'}}
    assert status == 500


async def test_error_with_data() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def echo(text: str) -> str:
        raise Exception('Ex', 'some data')

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{"text": "123"}', method_name='echo')
    assert json.loads(res) == {
        'error': {'code': 500, 'data': 'some data', 'message': 'Ex'}
    }
    assert status == 500


async def test_error_method() -> None:
    reg = RpcRegistry()

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{"text": "123"}', method_name='echo')
    assert json.loads(res) == {
        'error': {'code': 404, 'message': 'Method not found'}
    }
    assert status == 404


async def test_error_params() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def echo(text: str, a: int) -> str:
        raise Exception('Ex')

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'', method_name='echo')
    assert json.loads(res) == {
        'error': {'code': 400, 'message': 'Bad Request'}
    }
    assert status == 400


async def test_error_unexpected_params() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def echo(a: int) -> str:
        raise Exception('Ex')

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{"a":1,"b":2}', method_name='echo')
    assert json.loads(res) == {
        'error': {
            'code': 400,
            'data': {'info': 'Got an unexpected argument: b'},
            'message': 'Invalid params',
        }
    }
    assert status == 400


async def test_error_params_by_pos() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def echo(text: str, a: int) -> str:
        raise Exception('Ex')

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{}', method_name='echo')
    assert json.loads(res) == {
        'error': {
            'code': 400,
            'data': 'Missing required params in request',
            'message': 'Invalid params',
        }
    }
    assert status == 400


async def test_error_unexpected_params_by_pos() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def echo(a: int, b: int = 2) -> str:
        raise Exception('Ex')

    ex = RestRpcExecutor(reg, get_app())
    res, status = await ex.exec(b'{"a": 1,"b": 2, "d": 5}', method_name='echo')
    assert json.loads(res) == {
        'error': {
            'code': 400,
            'data': {'info': 'Got an unexpected argument: d'},
            'message': 'Invalid params',
        }
    }
    assert status == 400


async def test_clt() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int) -> Dict[str, int]:
        return {'sum': a + b}

    clt = get_clt(reg)

    result = await clt.exec('sum', {'a': 1, 'b': 2})
    assert result == {'sum': 3}


async def test_clt_err_params() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int) -> Dict[str, int]:
        return {'sum': a + b}

    clt = get_clt(reg)

    with pytest.raises(RestRpcError) as exc_info:
        await clt.exec('sum', {'a': 1})
    assert exc_info.value.code == 400
    assert exc_info.value.message == 'Invalid params'
    assert exc_info.value.data == {
        'info': 'Missing 1 required argument(s):  b'
    }


async def test_clt_err_method() -> None:
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int) -> Dict[str, int]:
        return {'sum': a + b}

    clt = get_clt(reg)

    with pytest.raises(RestRpcError) as exc_info:
        await clt.exec('sum2', {'a': 1, 'b': 2})
    assert exc_info.value.code == 404
    assert exc_info.value.message == 'Method not found'
