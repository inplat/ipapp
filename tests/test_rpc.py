import pytest

from ipapp.rpc import Executor, InvalidArguments, method


async def test_exec_params_by_name():
    class Api:
        @method()
        async def sum(self, a: int, b: int, c: int = 0) -> int:
            return a + b + c

    ex = Executor(Api())
    res = await ex.exec('sum', kwargs={'a': 3, 'b': 4})
    assert res == 7


async def test_exec_params_by_pos():
    class Api:
        @method()
        async def sum(self, a: int, b: int, c: int = 0) -> int:
            return a + b + c

    ex = Executor(Api())
    res = await ex.exec('sum', args=[3, 4])
    assert res == 7


async def test_validation():
    class Api:
        @method()
        async def sum(self, a: int, b: int, c: int = 0) -> int:
            return a + b + c

    ex = Executor(Api())
    with pytest.raises(InvalidArguments):
        await ex.exec('sum', kwargs={'a': 'd', 'b': 4})


async def test_type_casting():
    class Api:
        @method()
        async def sum(self, a: int, b: str) -> None:
            assert isinstance(a, int)
            assert isinstance(b, str)

    ex = Executor(Api())
    await ex.exec('sum', args=['123', 321])


async def test_jsonschema_validators():
    class Api:
        @method(validators={'a': {'type': 'string', 'format': 'date'}})
        async def sum(self, a: str) -> None:
            assert isinstance(a, str)

    ex = Executor(Api())
    await ex.exec('sum', ['2020-01-01'])

    with pytest.raises(InvalidArguments):
        await ex.exec('sum', ['2020-01-01-11'])


async def test_errors_method_decorator():
    with pytest.raises(UserWarning):

        class Api1:
            @method(errors=[1])
            async def test(self):
                pass

    with pytest.raises(UserWarning):

        class Api2:
            @method(summary=1)
            async def test(self):
                pass

    with pytest.raises(UserWarning):

        class Api3:
            @method(description=1)
            async def test(self):
                pass

    with pytest.raises(UserWarning):

        class Api4:
            @method(deprecated=1)
            async def test(self):
                pass

    with pytest.raises(UserWarning):

        class Api5:
            @method(request_model=1)
            async def test(self):
                pass

    with pytest.raises(UserWarning):

        class Api:
            @method(response_model=1)
            async def test(self):
                pass

    with pytest.raises(UserWarning):

        class Api6:
            @method(request_ref=1)
            async def test(self):
                pass

    with pytest.raises(UserWarning):

        class Api7:
            @method(response_ref=1)
            async def test(self):
                pass
