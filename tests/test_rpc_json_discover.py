from enum import Enum
from typing import List

from pydantic import Field
from pydantic.main import BaseModel

from ipapp.rpc import RpcRegistry
from ipapp.rpc.jsonrpc.openrpc.discover import discover
from ipapp.rpc.jsonrpc.openrpc.models import ParamStructure


def test_discover_api_desc_default():
    reg = RpcRegistry()

    spec = discover(reg).dict()
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
    reg = RpcRegistry(
        title='Summyry desc', description='Long multiline\ndescription'
    )

    spec = discover(reg).dict()
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


def test_discover_api_desc_docstr_legacy():
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
    reg = RpcRegistry(version='1.0')

    spec = discover(reg).dict()
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
    reg = RpcRegistry()

    @reg.method(
        name="Meth",
        summary='Summary method',
        description="Description method",
        deprecated=True,
    )
    async def meth(a):
        pass

    spec = discover(reg).dict(exclude_unset=True)

    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'Meth',
                'paramStructure': ParamStructure.BY_NAME,
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


async def test_method_descr_decorator_api_as_list():
    reg = RpcRegistry()

    @reg.method(
        name="Meth",
        summary='Summary method',
        description="Description method",
        deprecated=True,
    )
    async def meth(a):
        pass

    spec = discover(reg).dict(exclude_unset=True)

    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'Meth',
                'paramStructure': ParamStructure.BY_NAME,
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
    reg = RpcRegistry()

    @reg.method()
    async def meth(
        a: int = Field(
            123, title='Some title', description='Long description'
        ),
    ):
        pass

    spec = discover(reg).dict(exclude_unset=True)

    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'meth',
                'paramStructure': ParamStructure.BY_NAME,
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

    reg = RpcRegistry()

    @reg.method()
    async def meth(user: User) -> User:
        """
        :param user: пользователь
        :return: новый пользователь
        """
        return user

    spec = discover(reg).dict(exclude_unset=True)

    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'meth',
                'paramStructure': ParamStructure.BY_NAME,
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
                        'type': {"ref": "#/components/schemas/ContactType"},
                        'value': {'title': 'Value', 'type': 'string'},
                    },
                },
                "ContactType": {
                    "title": "ContactType",
                    "enum": ["phone", "email"],
                    "description": "An enumeration.",
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

    reg = RpcRegistry()

    @reg.method()
    async def test(a: a_cls, b: b_cls, c: c_cls):
        pass

    spec = discover(reg).dict(exclude_unset=True)
    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'test',
                'paramStructure': ParamStructure.BY_NAME,
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

    reg = RpcRegistry()

    @reg.method(request_model=SomeRequest, response_model=SomeResponse)
    async def some(id, name):
        pass

    spec = discover((reg)).dict(exclude_unset=True)
    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'some',
                'paramStructure': ParamStructure.BY_NAME,
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
    reg = RpcRegistry()

    @reg.method(
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
    async def sum(a, b):
        return a + b

    spec = discover(reg).dict(exclude_unset=True, by_alias=True)

    assert spec == {
        'openrpc': '1.2.4',
        'info': {'title': '', 'version': '0'},
        'methods': [
            {
                'name': 'sum',
                'paramStructure': ParamStructure.BY_NAME,
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
