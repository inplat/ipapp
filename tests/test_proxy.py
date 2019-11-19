from asyncio import Future, sleep
from copy import copy, deepcopy
from math import ceil, floor, trunc

from pytest import raises

from ipapp.ctx import Proxy


class Example(dict):
    def __missing__(self, key):
        return {}

    def __call__(self):
        return self['key']

    def __enter__(self):
        self['key'] += 5
        return self

    async def __aenter__(self):
        self['akey'] += 50
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self['key'] -= 5

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self['akey'] -= 50


cls_obj = Example({'key': 5, 'akey': 50})
cls_ctx = Proxy('cls')
cls_ctx.__ctx__.set(cls_obj)

str_obj = 'hi'
str_ctx = Proxy('str')
str_ctx.__ctx__.set(str_obj)

int_obj = 60
int_ctx = Proxy('int')
int_ctx.__ctx__.set(int_obj)

iter_obj = iter([1, 2])
iter_ctx = Proxy('iter')
iter_ctx.__ctx__.set(iter_obj)

async_obj: Future = sleep(1)
async_ctx = Proxy('async')
async_ctx.__ctx__.set(async_obj)

float_obj = 60.5
float_ctx = Proxy('float')
float_ctx.__ctx__.set(float_obj)

proxy_obj = 100
proxy_ctx = Proxy('proxy')


def test_set_get_reset():
    token = proxy_ctx.__ctx__.set(proxy_obj)
    value = proxy_ctx.__ctx__.get()
    assert value == proxy_obj

    proxy_ctx.__ctx__.reset(token)
    value = proxy_ctx.__ctx__.get()
    assert value is None


def test_dict():
    assert cls_ctx.__dict__ == cls_obj.__dict__


def test_dir():
    assert dir(int_ctx) == dir(int_obj)


def test_attr():
    setattr(cls_ctx, 'key', 'value')
    assert getattr(cls_obj, 'key') == 'value'

    delattr(cls_ctx, 'key')
    assert getattr(cls_obj, 'key', None) is None


def test_oct():
    assert oct(int_ctx) == oct(int_obj)


def test_hex():
    assert hex(int_ctx) == hex(int_obj)


def test_hash():
    assert hash(int_ctx) == hash(int_obj)


def test_str():
    assert str(int_ctx) == str(int_obj)


def test_int():
    assert int(int_ctx) == int(int_obj)


def test_bool():
    assert bool(int_ctx) == bool(int_obj)


def test_bytes():
    assert bytes(int_ctx) == bytes(int_obj)


def test_float():
    assert float(int_ctx) == float(int_obj)


def test_complex():
    assert complex(int_ctx) == complex(int_obj)


def test_repr():
    assert repr(int_ctx) == repr(int_obj)


def test_format():
    assert format(str_ctx, ':<10') == format(str_obj, ':<10')


def test_neg():
    assert -int_ctx == -int_obj


def test_pos():
    assert +int_ctx == +int_obj


def test_abs():
    assert abs(int_ctx) == abs(int_obj)


def test_invert():
    assert ~int_ctx == ~int_obj


def test_ceil():
    assert ceil(float_ctx) == ceil(float_obj)


def test_floor():
    assert floor(float_ctx) == floor(float_obj)


def test_round():
    assert round(float_ctx) == round(float_obj)


def test_trunc():
    assert trunc(float_ctx) == trunc(float_obj)


def test_index():
    assert str_ctx[0] == str_obj[0]


def test_eq():
    assert int_ctx == int_obj


def test_ne():
    assert int_ctx != 70


def test_lt():
    assert int_ctx < 70


def test_le():
    assert int_ctx <= 70


def test_gt():
    assert int_ctx > 50


def test_ge():
    assert int_ctx >= 50


def test_copy():
    assert copy(cls_ctx) == copy(cls_obj)


def test_deepcopy():
    assert deepcopy(cls_ctx) == deepcopy(cls_obj)


def test_context():
    with cls_ctx as e:
        assert e['key'] == 10
    assert cls_ctx['key'] == 5


async def test_acontext():
    async with cls_ctx as e:
        assert e['akey'] == 100
    assert cls_ctx['akey'] == 50


def test_call():
    assert cls_ctx() == cls_obj()


async def test_await():
    await async_ctx


def test_len():
    assert len(str_ctx) == len(str_obj)


def test_item():
    cls_ctx['key'] = 'value'
    assert 'key' in cls_ctx and 'key' in cls_obj

    del cls_ctx['key']
    assert 'key' not in cls_ctx and 'key' not in cls_obj

    assert cls_ctx['foo'] == {}


def test_iter():
    iterator = iter(str_ctx)
    assert next(iterator) == 'h'
    assert next(iterator) == 'i'
    with raises(StopIteration):
        assert next(iterator)


def test_next():
    assert next(iter_ctx) == 1
    assert next(iter_ctx) == 2
    with raises(StopIteration):
        assert next(iter_ctx)


def test_reversed():
    iterator = reversed(str_ctx)
    assert next(iterator) == 'i'
    assert next(iterator) == 'h'
    with raises(StopIteration):
        assert next(iterator)


def test_or():
    assert int_ctx & 13 == int_obj & 13


def test_and():
    assert int_ctx | 13 == int_obj | 13


def test_xor():
    assert int_ctx ^ 13 == int_obj ^ 13


def test_add():
    assert int_ctx + 2 == int_obj + 2


def test_sub():
    assert int_ctx - 2 == int_obj - 2


def test_mul():
    assert int_ctx * 2 == int_obj * 2


def test_mod():
    assert int_ctx % 2 == int_obj % 2


def test_pow():
    assert int_ctx ** 2 == int_obj ** 2


def test_lshift():
    assert int_ctx << 2 == int_obj << 2


def test_rshift():
    assert int_ctx >> 2 == int_obj >> 2


def test_truediv():
    assert int_ctx / 2.6 == int_obj / 2.6


def test_floordiv():
    assert int_ctx // 2.6 == int_obj // 2.6


def test_divmod():
    assert divmod(int_ctx, 2) == divmod(int_obj, 2)


def test_ror():
    assert 2 | int_ctx == 2 | int_obj


def test_rand():
    assert 2 & int_ctx == 2 & int_obj


def test_rxor():
    assert 2 ^ int_ctx == 2 ^ int_obj


def test_radd():
    assert 2 + int_ctx == 2 + int_obj


def test_rsub():
    assert 2 - int_ctx == 2 - int_obj


def test_rmul():
    assert 2 * int_ctx == 2 * int_obj


def test_rmod():
    assert 2 % int_ctx == 2 % int_obj


def test_rpow():
    assert 2 ** int_ctx == 2 ** int_obj


def test_rlshift():
    assert 2 << int_ctx == 2 << int_obj


def test_rrshift():
    assert 2 >> int_ctx == 2 >> int_obj


def test_rtruediv():
    assert 2.6 / int_ctx == 2.6 / int_obj


def test_rfloordiv():
    assert 2.6 // int_ctx == 2.6 // int_obj


def test_rdivmod():
    assert divmod(2, int_ctx) == divmod(2, int_obj)
