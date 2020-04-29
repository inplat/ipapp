import json
import os

from ipapp.rpc import method
from ipapp.rpc.jsonrpc.openrpc import OpenRPC
from ipapp.rpc.jsonrpc.openrpc.discover import discover


def test_openrpc_examples():
    path = os.path.join(os.path.dirname(__file__), 'openapi_examples')
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                js = json.load(open(entry.path))
                try:
                    OpenRPC(**js)
                except Exception:
                    print(entry.name)
                    raise


def test_openrpc():
    class Api:
        """
        Test service

        Test service long
        description
        """

        __version__ = '1.0.1'

        @method()
        async def sum(self, a: int, b: int) -> int:
            """
            Sum

            sum:
              - a
              - b

            :param a: a var
            :param b: b var
            :return: a+b
            """
            return a + b

        @method()
        async def echo(self, a):
            return a

    orpc = discover(Api())

    expected = {
        "openrpc": "1.2.4",
        "info": {
            "title": "Test service",
            "description": "Test service long\ndescription",
            "version": "1.0.1",
        },
        "methods": [
            {
                "name": "echo",
                "params": [
                    {"name": "a", "required": True, "schema": {"title": "A"}}
                ],
                "result": {
                    "name": "result",
                    "required": True,
                    "schema": {"title": "Result"},
                },
            },
            {
                "name": "sum",
                "summary": "Sum",
                "description": "sum:\n  - a\n  - b",
                "params": [
                    {
                        "name": "a",
                        "summary": "a var",
                        "required": True,
                        "schema": {"title": "A", "type": "integer"},
                    },
                    {
                        "name": "b",
                        "summary": "b var",
                        "required": True,
                        "schema": {"title": "B", "type": "integer"},
                    },
                ],
                "result": {
                    "summary": "a+b",
                    "name": "result",
                    "required": True,
                    "schema": {"title": "Result", "type": "integer"},
                },
            },
        ],
    }

    result_json = orpc.json(exclude_unset=True, by_alias=True, indent=4)
    result = json.loads(result_json)
    assert expected == result
