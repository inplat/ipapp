import json
from typing import Optional

from pydantic.main import BaseModel

from ipapp import BaseApplication, BaseConfig
from ipapp.rpc import method
from ipapp.rpc.jsonrpc import JsonRpcClient, JsonRpcError, JsonRpcExecutor


def get_app():
    return BaseApplication(BaseConfig())


def get_clt(api_cls):
    app = get_app()

    async def transport(request: bytes, timeout: Optional[float]) -> bytes:
        ex = JsonRpcExecutor(api_cls(), app)
        return await ex.exec(request)

    return JsonRpcClient(transport, app)


async def test_success_by_name():
    class H:
        @method()
        async def echo(self, text: str) -> str:
            return 'echo: %s' % text

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",'
        b' "method": "echo", "params": {"text": "123"}, "id": 10}'
    )

    assert json.loads(res) == {
        "jsonrpc": "2.0",
        "id": 10,
        "result": "echo: 123",
    }


async def test_success_by_pos():
    class H:
        @method()
        async def sum(self, a: int, b: int) -> int:
            return a + b

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",' b' "method": "sum", "params": [1,2], "id": 10}'
    )

    assert json.loads(res) == {
        "jsonrpc": "2.0",
        "id": 10,
        "result": 3,
    }


async def test_success_by_name_default():
    class H:
        @method()
        async def sum(self, a: int, b: int, c: int = 3) -> int:
            return a + b + c

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",'
        b' "method": "sum", "params": {"a":1,"b":2}, "id": 10}'
    )

    assert json.loads(res) == {
        "jsonrpc": "2.0",
        "id": 10,
        "result": 6,
    }


async def test_success_by_pos_default():
    class H:
        @method()
        async def sum(self, a: int, b: int, c: int = 3) -> int:
            return a + b + c

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",' b' "method": "sum", "params": [1,2], "id": 10}'
    )

    assert json.loads(res) == {
        "jsonrpc": "2.0",
        "id": 10,
        "result": 6,
    }


async def test_err_parse_1():
    class H:
        pass

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(b'eeeee')
    assert json.loads(res) == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"message": "Parse error", "code": -32700},
    }


async def test_err_invalid_req_2():
    class H:
        pass

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(b'{"params": []}')
    assert json.loads(res) == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"message": "Invalid Request", "code": -32600},
    }


async def test_error():
    class H:
        @method()
        async def echo(self, text: str) -> str:
            raise Exception('Ex')

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",'
        b' "method": "echo", "params": {"text": "123"}, "id": 10}'
    )
    assert json.loads(res) == {
        'error': {'code': -32000, 'message': 'Ex'},
        'id': 10,
        'jsonrpc': '2.0',
    }


async def test_error_with_data():
    class H:
        @method()
        async def echo(self, text: str) -> str:
            raise Exception('Ex', 'some data')

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",'
        b' "method": "echo", "params": {"text": "123"}, "id": 10}'
    )
    assert json.loads(res) == {
        'error': {'code': -32000, 'data': 'some data', 'message': 'Ex'},
        'id': 10,
        'jsonrpc': '2.0',
    }


async def test_error_method():
    class H:
        pass

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",'
        b' "method": "echo", "params": {"text": "123"}, "id": 10}'
    )
    assert json.loads(res) == {
        'error': {'code': -32601, 'message': 'Method not found'},
        'id': 10,
        'jsonrpc': '2.0',
    }


async def test_error_params():
    class H:
        @method()
        async def echo(self, text: str, a: int) -> str:
            raise Exception('Ex')

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",' b' "method": "echo", "params": {}, "id": 10}'
    )
    assert json.loads(res) == {
        'error': {
            'code': -32602,
            'data': {'info': 'Missing 2 required argument(s):  text, a'},
            'message': 'Invalid params',
        },
        'id': 10,
        'jsonrpc': '2.0',
    }


async def test_error_unexpected_params():
    class H:
        @method()
        async def echo(self, a: int) -> str:
            raise Exception('Ex')

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",'
        b' "method": "echo", '
        b'"params": {"a":1,"b":2}, "id": 10}'
    )
    assert json.loads(res) == {
        'error': {
            'code': -32602,
            'data': {'info': 'Got an unexpected argument: b'},
            'message': 'Invalid params',
        },
        'id': 10,
        'jsonrpc': '2.0',
    }


async def test_error_params_by_pos():
    class H:
        @method()
        async def echo(self, text: str, a: int) -> str:
            raise Exception('Ex')

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",' b' "method": "echo", "params": [], "id": 10}'
    )
    assert json.loads(res) == {
        'error': {
            'code': -32602,
            'data': {'info': 'Missing 2 required argument(s):  text, a'},
            'message': 'Invalid params',
        },
        'id': 10,
        'jsonrpc': '2.0',
    }


async def test_error_unexpected_params_by_pos():
    class H:
        @method()
        async def echo(self, a: int, b: int = 2) -> str:
            raise Exception('Ex')

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",'
        b' "method": "echo", '
        b'"params": [1,2,3], "id": 10}'
    )
    assert json.loads(res) == {
        'error': {
            'code': -32602,
            'data': {
                'info': 'Method takes 2 positional arguments but 3 were given'
            },
            'message': 'Invalid params',
        },
        'id': 10,
        'jsonrpc': '2.0',
    }


async def test_notification():
    class H:
        @method()
        async def echo(self, text: str) -> str:
            return text

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'{"jsonrpc": "2.0",' b' "method": "echo", "params": {"text": "123"}}'
    )
    assert res == b''


async def test_batch():
    class H:
        @method()
        async def echo(self, text: str) -> str:
            return 'echo: %s' % text

        @method()
        async def err(self) -> None:
            raise Exception('some error', {'pay': 'load'})

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'[{"jsonrpc": "2.0", '
        b'"method": "echo", "params": {"text": "1"}, "id": 1},'
        b'{"jsonrpc": "2.0", '
        b'"method": "err", "params": {}, "id": 3},'
        b'{"jsonrpc": "2.0", '
        b'"method": "echo", "params": {"text": "2"}, "id": 2}]'
    )
    assert json.loads(res) == [
        {"jsonrpc": "2.0", "id": 1, "result": "echo: 1"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "error": {
                "message": "some error",
                "code": -32000,
                "data": {"pay": "load"},
            },
        },
        {"jsonrpc": "2.0", "id": 2, "result": "echo: 2"},
    ]


async def test_batch_notification():
    class H:
        @method()
        async def echo(self, text: str) -> str:
            return 'echo: %s' % text

        @method()
        async def err(self) -> None:
            raise Exception('some error', {'pay': 'load'})

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'[{"jsonrpc": "2.0", "method": "echo", "params": {"text": "1"}},'
        b'{"jsonrpc": "2.0", "method": "err", "params": {}}]'
    )
    assert res == b''


async def test_batch_complicated():
    class H:
        @method()
        async def echo(self, text: str) -> str:
            return 'echo: %s' % text

        @method()
        async def err(self) -> None:
            raise Exception('some error', {'pay': 'load'})

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(
        b'[{"jsonrpc": "2.0", '
        b'"method": "echo", "params": {"text": "1"},"id":"1"},'
        b'{"jsonrpc": "2.0", '
        b'"method": "echo", "params": {"text": "2"}},'
        b'{"jsonrpc": "2.0", '
        b'"method": "echo", "params": {"text": "3"},"id":3},'
        b'{"foo": "boo"},'
        b'{"jsonrpc": "2.0", '
        b'"method": "err", "params": {}},'
        b'{"jsonrpc": "2.0", '
        b'"method": "notfound1", "params": {}},'
        b'{"jsonrpc": "2.0", '
        b'"method": "notfound2", "params": {},"id":7},'
        b'{"jsonrpc": "2.0", '
        b'"method": "echo", "params": {"text": "8"},"id":8}]'
    )

    assert json.loads(res) == [
        {"jsonrpc": "2.0", "id": "1", "result": "echo: 1"},
        {"jsonrpc": "2.0", "id": 3, "result": "echo: 3"},
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"message": "Invalid Request", "code": -32600},
        },
        {
            "jsonrpc": "2.0",
            "id": 7,
            "error": {"message": "Method not found", "code": -32601},
        },
        {"jsonrpc": "2.0", "id": 8, "result": "echo: 8"},
    ]


async def test_clt_single():
    class Api:
        @method()
        async def sum(self, a: int, b: int) -> int:
            return a + b

    clt = get_clt(Api)

    result = await clt.exec('sum', [1, 2])
    assert result == 3


async def test_clt_batch():
    class Api:
        @method()
        async def sum(self, a: int, b: int) -> int:
            return a + b

    clt = get_clt(Api)

    result = await clt.exec_batch(
        clt.exec('sum', [1, 2]), clt.exec('sum', [3, 4])
    )
    assert result == (3, 7)


async def test_clt_single_err_params():
    class Api:
        @method()
        async def sum(self, a: int, b: int) -> int:
            return a + b

    clt = get_clt(Api)

    try:
        await clt.exec('sum', [1])
    except JsonRpcError as err:
        assert err.jsonrpc_error_code == -32602
        assert err.message == 'Invalid params'
        assert err.data == {
            'info': 'Method takes 2 positional arguments but 1 was given'
        }
    else:
        assert False


async def test_clt_single_err_method():
    class Api:
        @method()
        async def sum(self, a: int, b: int) -> int:
            return a + b

    clt = get_clt(Api)

    try:
        await clt.exec('sum2', [1, 2, 3])
    except JsonRpcError as err:
        assert err.jsonrpc_error_code == -32601
        assert err.message == 'Method not found'
    else:
        assert False


async def test_clt_batch_err():
    class Api:
        @method()
        async def sum(self, a: int, b: int) -> int:
            return a + b

    clt = get_clt(Api)

    result = await clt.exec_batch(
        clt.exec('sum2', [3, 4]),
        clt.exec('sum', [3]),
        clt.exec('sum', [3, 4, 5]),
        clt.exec('sum', ['a', 'b']),
    )
    assert isinstance(result[0], JsonRpcError)
    assert isinstance(result[1], JsonRpcError)
    assert isinstance(result[2], JsonRpcError)
    assert isinstance(result[3], JsonRpcError)

    assert result[0].jsonrpc_error_code == -32601
    assert result[1].jsonrpc_error_code == -32602
    assert result[2].jsonrpc_error_code == -32602
    assert result[3].jsonrpc_error_code == -32602


async def test_clt_single_notification():
    class Api:
        @method()
        async def sum(self, a: int, b: int) -> int:
            return a + b

    clt = get_clt(Api)

    result = await clt.exec('sum', [1, 2], one_way=True)
    assert result is None


async def test_clt_batch_notification():
    class Api:
        @method()
        async def sum(self, a: int, b: int) -> int:
            return a + b

    clt = get_clt(Api)

    result = await clt.exec_batch(
        clt.exec('sum', [1, 2], one_way=True),
        clt.exec('sum', [3, 4], one_way=True),
    )
    assert result == (None, None)


async def test_clt_batch_empty():
    class Api:
        pass

    clt = get_clt(Api)

    result = await clt.exec_batch()
    assert result == ()


async def test_legacy_format_success():
    class H:
        @method()
        async def echo(self, text: str) -> str:
            return 'echo: %s' % text

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(b'{"method": "echo", "params": {"text": "123"}}')

    assert json.loads(res) == {
        'code': 0,
        'message': 'OK',
        'result': 'echo: 123',
    }


async def test_legacy_format_error():
    class H:
        @method()
        async def echo(self, text: str) -> str:
            return 'echo: %s' % text

    ex = JsonRpcExecutor(H(), get_app())
    res = await ex.exec(b'{"method": "echo", "params": {}}')

    assert json.loads(res) == {
        'code': -32602,
        'details': None,
        'message': 'Invalid params',
    }


async def test_discover():
    class Address(BaseModel):
        inline: str
        index: str

    class User(BaseModel):
        address: Address
        name: str

    class Err111(JsonRpcError):
        jsonrpc_error_code = 111
        message = "My Error 111"

    class Err222(JsonRpcError):
        jsonrpc_error_code = 222
        message = "My Error 222"

    class H:
        """
        Api for tests

        Log description
        of api
        """

        __version__ = '1.0'

        @method(deprecated=True)
        async def echo(self, text: str) -> str:
            return 'echo: %s' % text

        @method()
        async def get_user(self, id: int) -> User:
            return User(
                name='test',
                address={'inline': 'Moscow, Kremlin, 1', 'index': '123456'},
            )

        @method(errors=[Err111, Err222])
        async def add_user(self, user: User) -> int:
            """
            Add user

            Метод добавляет нового
            пользователи и возвращает его
            идентификатор

            :param user: объект пользователя
            :return: идентифиатор пользователя
            """
            return 1

    ex = JsonRpcExecutor(H(), get_app(), discover_enabled=True)
    res = await ex.exec(
        b'{"jsonrpc": "2.0",' b' "method": "rpc.discover", "id": 10}'
    )

    assert json.loads(res) == {
        'result': {
            'openrpc': '1.2.4',
            'info': {
                'version': '1.0',
                'title': 'Api for tests',
                'description': 'Log description\nof api',
            },
            'methods': [
                {
                    'description': 'Метод добавляет нового\n'
                    'пользователи и возвращает его\n'
                    'идентификатор',
                    'result': {
                        'required': True,
                        'schema': {'title': 'Result', 'type': 'integer'},
                        'name': 'result',
                        "summary": "идентифиатор пользователя",
                    },
                    'summary': 'Add user',
                    'name': 'add_user',
                    'errors': [
                        {'code': 111, 'data': None, 'message': 'My Error 111'},
                        {'code': 222, 'data': None, 'message': 'My Error 222'},
                    ],
                    'params': [
                        {
                            'required': True,
                            'summary': 'объект пользователя',
                            'schema': {'$ref': '#/components/schemas/User'},
                            'name': 'user',
                        }
                    ],
                },
                {
                    'result': {
                        'required': True,
                        'schema': {'title': 'Result', 'type': 'string'},
                        'name': 'result',
                    },
                    'deprecated': True,
                    'params': [
                        {
                            'required': True,
                            'schema': {'title': 'Text', 'type': 'string'},
                            'name': 'text',
                        }
                    ],
                    'name': 'echo',
                },
                {
                    'result': {
                        'required': True,
                        'schema': {'$ref': '#/components/schemas/User'},
                        'name': 'result',
                    },
                    'params': [
                        {
                            'required': True,
                            'schema': {'title': 'Id', 'type': 'integer'},
                            'name': 'id',
                        }
                    ],
                    'name': 'get_user',
                },
            ],
            'components': {
                'schemas': {
                    'User': {
                        'required': ['address', 'name'],
                        'properties': {
                            'address': {
                                '$ref': '#/components/schemas/Address'
                            },
                            'name': {'title': 'Name', 'type': 'string'},
                        },
                        'title': 'User',
                        'type': 'object',
                    },
                    'Address': {
                        'required': ['inline', 'index'],
                        'properties': {
                            'inline': {'title': 'Inline', 'type': 'string'},
                            'index': {'title': 'Index', 'type': 'string'},
                        },
                        'title': 'Address',
                        'type': 'object',
                    },
                }
            },
        },
        'id': 10,
        'jsonrpc': '2.0',
    }
