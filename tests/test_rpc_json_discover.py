from enum import Enum
from typing import List

from pydantic import Field
from pydantic.main import BaseModel

from ipapp.rpc import method
from ipapp.rpc.jsonrpc.openrpc.discover import discover


def test_discover_api_desc_default():
    class Api:
        pass

    spec = discover(Api()).dict()
    assert spec == {
        'openrpc': '1.2.4',
        'info': {
            'title': '',
            'description': None,
            'termsOfService': None,
            'version': '0',
            'contact': None,
            'license': None,
        },
        'servers': None,
        'methods': [],
        'components': None,
        'externalDocs': None,
    }


def test_discover_api_desc_docstr():
    class Api:
        """
        Summyry desc

        Long multiline
        description
        """

    spec = discover(Api()).dict()
    assert spec == {
        'openrpc': '1.2.4',
        'info': {
            'title': 'Summyry desc',
            'description': 'Long multiline\ndescription',
            'termsOfService': None,
            'version': '0',
            'contact': None,
            'license': None,
        },
        'servers': None,
        'methods': [],
        'components': None,
        'externalDocs': None,
    }


def test_discover_api_version():
    class Api:
        __version__ = '1.0'

    spec = discover(Api()).dict()
    assert spec == {
        'openrpc': '1.2.4',
        'info': {
            'title': '',
            'description': None,
            'termsOfService': None,
            'version': '1.0',
            'contact': None,
            'license': None,
        },
        'servers': None,
        'methods': [],
        'components': None,
        'externalDocs': None,
    }


async def test_method_descr_decorator():
    class Api:
        @method(
            name="Meth",
            summary='Summary method',
            description="Description method",
            deprecated=True,
        )
        async def meth(self, a):
            pass

    spec = discover(Api()).dict(exclude_unset=True)
    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'Meth',
                'summary': 'Summary method',
                'description': 'Description method',
                'deprecated': True,
                'params': [
                    {'name': 'a', 'required': True, 'schema_': {'title': 'A'}}
                ],
                'result': {
                    'name': 'result',
                    'required': True,
                    'schema_': {'title': 'Result'},
                },
            }
        ],
    }


async def test_method_params_descr_by_field():
    class Api:
        @method()
        async def meth(
            self,
            a: int = Field(
                123, title='Some title', description='Long description'
            ),
        ):
            pass

    spec = discover(Api()).dict(exclude_unset=True)

    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'meth',
                'params': [
                    {
                        'name': 'a',
                        'required': False,
                        'schema_': {
                            'title': 'Some title',
                            'type': 'integer',
                            'description': 'Long description',
                            'default': 123,
                        },
                    }
                ],
                'result': {
                    'name': 'result',
                    'required': True,
                    'schema_': {'title': 'Result'},
                },
            }
        ],
    }


async def test_base_model():
    class ContactType(Enum):
        PHONE = 'phone'
        EMAIL = 'email'

    class Contact(BaseModel):
        type: ContactType
        value: str

    class User(BaseModel):

        id: int
        contacts: List[Contact]
        name: str = ''

    class Api:
        @method()
        async def meth(self, user: User) -> User:
            """
            :param user: пользователь
            :return: новый пользователь
            """
            return user

    spec = discover(Api()).dict(exclude_unset=True)
    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'meth',
                'params': [
                    {
                        'name': 'user',
                        'summary': 'пользователь',
                        'required': True,
                        'schema_': {'ref': '#/components/schemas/User'},
                    }
                ],
                'result': {
                    'name': 'result',
                    'summary': 'новый пользователь',
                    'required': True,
                    'schema_': {'ref': '#/components/schemas/User'},
                },
            }
        ],
        'components': {
            'schemas': {
                'User': {
                    'title': 'User',
                    'required': ['id', 'contacts'],
                    'type': 'object',
                    'properties': {
                        'id': {'title': 'Id', 'type': 'integer'},
                        'contacts': {
                            'title': 'Contacts',
                            'type': 'array',
                            'items': {'$ref': '#/components/schemas/Contact'},
                        },
                        'name': {
                            'title': 'Name',
                            'type': 'string',
                            'default': '',
                        },
                    },
                },
                'Contact': {
                    'title': 'Contact',
                    'required': ['type', 'value'],
                    'type': 'object',
                    'properties': {
                        'type': {'title': 'Type', 'enum': ['phone', 'email']},
                        'value': {'title': 'Value', 'type': 'string'},
                    },
                },
            }
        },
    }


async def test_conflict_base_model_name():
    def get_cls():
        class A(BaseModel):
            id: str

        return A

    a_cls = get_cls()
    b_cls = get_cls()
    c_cls = get_cls()

    assert a_cls is not b_cls
    assert c_cls is not b_cls
    assert c_cls is not a_cls

    class Api:
        @method()
        async def test(self, a: a_cls, b: b_cls, c: c_cls):
            pass

    spec = discover(Api()).dict(exclude_unset=True)
    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'test',
                'params': [
                    {
                        'name': 'a',
                        'required': True,
                        'schema_': {'ref': '#/components/schemas/A'},
                    },
                    {
                        'name': 'b',
                        'required': True,
                        'schema_': {
                            'ref': '#/components/schemas/TestsTestRpcJsonDiscoverA'
                        },
                    },
                    {
                        'name': 'c',
                        'required': True,
                        'schema_': {
                            'ref': '#/components/schemas/TestsTestRpcJsonDiscoverA1'
                        },
                    },
                ],
                'result': {
                    'name': 'result',
                    'required': True,
                    'schema_': {'title': 'Result'},
                },
            }
        ],
        'components': {
            'schemas': {
                'A': {
                    'title': 'A',
                    'required': ['id'],
                    'type': 'object',
                    'properties': {'id': {'title': 'Id', 'type': 'string'}},
                },
                'TestsTestRpcJsonDiscoverA': {
                    'title': 'A',
                    'required': ['id'],
                    'type': 'object',
                    'properties': {'id': {'title': 'Id', 'type': 'string'}},
                },
                'TestsTestRpcJsonDiscoverA1': {
                    'title': 'A',
                    'required': ['id'],
                    'type': 'object',
                    'properties': {'id': {'title': 'Id', 'type': 'string'}},
                },
            }
        },
    }


async def test_base_model_decorator():
    class SomeRequest(BaseModel):
        id: int
        name: str

    class SomeResponse(BaseModel):
        status: str

    class Api:
        @method(request_model=SomeRequest, response_model=SomeResponse)
        async def some(self, id, name):
            pass

    spec = discover((Api())).dict(exclude_unset=True)
    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'some',
                'params': [
                    {
                        'name': 'id',
                        'required': True,
                        'schema_': {'title': 'Id', 'type': 'integer'},
                    },
                    {
                        'name': 'name',
                        'required': True,
                        'schema_': {'title': 'Name', 'type': 'string'},
                    },
                ],
                'result': {
                    'name': 'result',
                    'required': True,
                    'schema_': {
                        'ref': '#/components/schemas/SomeResponseResult'
                    },
                },
            }
        ],
        'components': {
            'schemas': {
                'SomeResponseResult': {
                    'title': 'SomeResponseResult',
                    'required': ['status'],
                    'type': 'object',
                    'properties': {
                        'status': {'title': 'Status', 'type': 'string'}
                    },
                }
            }
        },
    }


async def test_examples():
    class Api:
        @method(
            examples=[
                {
                    'name': 'somebasic 1',
                    'description': 'some descr',
                    'summary': 'some summary',
                    'params': [
                        {'value': 1, 'name': ''},
                        {'value': 2, 'name': ''},
                    ],
                    'result': {'value': 3, 'name': ''},
                },
                {
                    'name': 'somebasic 2',
                    'description': 'some descr',
                    'summary': 'some summary',
                    'params': [
                        {'value': 3, 'name': ''},
                        {'value': 4, 'name': ''},
                    ],
                    'result': {'value': 7, 'name': ''},
                },
            ]
        )
        async def sum(self, a, b):
            return a + b

    spec = discover(Api()).dict(exclude_unset=True, by_alias=True)

    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'sum',
                'params': [
                    {'name': 'a', 'required': True, 'schema': {'title': 'A'}},
                    {'name': 'b', 'required': True, 'schema': {'title': 'B'}},
                ],
                'result': {
                    'name': 'result',
                    'required': True,
                    'schema': {'title': 'Result'},
                },
                'examples': [
                    {
                        'name': 'somebasic 1',
                        'description': 'some descr',
                        'summary': 'some summary',
                        'params': [
                            {'name': '', 'value': 1},
                            {'name': '', 'value': 2},
                        ],
                        'result': {'name': '', 'value': 3},
                    },
                    {
                        'name': 'somebasic 2',
                        'description': 'some descr',
                        'summary': 'some summary',
                        'params': [
                            {'name': '', 'value': 3},
                            {'name': '', 'value': 4},
                        ],
                        'result': {'name': '', 'value': 7},
                    },
                ],
            }
        ],
    }
