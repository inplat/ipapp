from functools import wraps

import pytest

from ipapp.rpc import Executor, InvalidArguments, RpcRegistry, method


async def test_exec_params_by_name_legacy():
    class Api:
        @method()
        async def sum(self, a: int, b: int, c: int = 0) -> int:
            return a + b + c

    ex = Executor(Api())
    res = await ex.exec('sum', kwargs={'a': 3, 'b': 4})
    assert res == 7


async def test_exec_params_by_name():
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int, c: int = 0) -> int:
        return a + b + c

    ex = Executor(reg)
    res = await ex.exec('sum', kwargs={'a': 3, 'b': 4})
    assert res == 7


async def test_exec_params_by_name_as_staticmethod():
    reg = RpcRegistry()

    class Api:
        @staticmethod
        @reg.method()
        async def sum(a: int, b: int, c: int = 0) -> int:
            return a + b + c

    ex = Executor(reg)
    res = await ex.exec('sum', kwargs={'a': 3, 'b': 4})
    assert res == 7


async def test_exec_params_by_name_legacy_as_staticmenthod():
    reg = RpcRegistry()

    class Api:
        @staticmethod
        @reg.method()
        async def sum(a: int, b: int, c: int = 0) -> int:
            return a + b + c

    ex = Executor(Api())
    res = await ex.exec('sum', kwargs={'a': 3, 'b': 4})
    assert res == 7


async def test_exec_params_by_pos():
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int, c: int = 0) -> int:
        return a + b + c

    ex = Executor(reg)
    res = await ex.exec('sum', args=[3, 4])
    assert res == 7


async def test_validation():
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: int, c: int = 0) -> int:
        return a + b + c

    ex = Executor(reg)
    with pytest.raises(InvalidArguments):
        await ex.exec('sum', kwargs={'a': 'd', 'b': 4})


async def test_type_casting():
    reg = RpcRegistry()

    @reg.method()
    async def sum(a: int, b: str) -> None:
        assert isinstance(a, int)
        assert isinstance(b, str)

    ex = Executor(reg)
    await ex.exec('sum', args=['123', 321])


async def test_jsonschema_validators():
    reg = RpcRegistry()

    @reg.method(validators={'a': {'type': 'string', 'format': 'date'}})
    async def sum(a: str) -> None:
        assert isinstance(a, str)

    ex = Executor(reg)
    await ex.exec('sum', ['2020-01-01'])

    with pytest.raises(InvalidArguments):
        await ex.exec('sum', ['2020-01-01-11'])


async def test_errors_method_decorator():
    reg = RpcRegistry()

    with pytest.raises(UserWarning):

        @reg.method(errors=[1])
        async def test1():
            pass

    with pytest.raises(UserWarning):

        @reg.method(summary=1)
        async def test2():
            pass

    with pytest.raises(UserWarning):

        @reg.method(description=1)
        async def test3():
            pass

    with pytest.raises(UserWarning):

        @reg.method(deprecated=1)
        async def test4():
            pass

    with pytest.raises(UserWarning):

        @reg.method(request_model=1)
        async def test5():
            pass

    with pytest.raises(UserWarning):

        @reg.method(response_model=1)
        async def test6():
            pass

    with pytest.raises(UserWarning):

        @reg.method(request_ref=1)
        async def test7():
            pass

    with pytest.raises(UserWarning):

        @reg.method(response_ref=1)
        async def test8():
            pass


async def test_duplicate_methods():
    reg = RpcRegistry()

    @reg.method()
    async def sum() -> None:
        pass

    @reg.method(name='sum')
    async def sum2() -> None:
        pass

    with pytest.raises(UserWarning):
        Executor(reg)


async def test_decorator():
    reg = RpcRegistry()

    def dec(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            assert kwargs['a'] == 3
            return await func(*args, **kwargs)

        return wrapper

    @reg.method()
    @dec
    async def sum(a: int, b: int, c: int = 0) -> int:
        return a + b + c

    ex = Executor(reg)

    res = await ex.exec('sum', kwargs={'a': 3, 'b': 4})
    assert res == 7
