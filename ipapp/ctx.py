from contextvars import ContextVar
from copy import copy, deepcopy
from math import ceil, floor, trunc
from typing import Any, Dict, Iterable, List

from aiohttp.web import Request

import ipapp.app  # noqa
import ipapp.logger.span as sp  # noqa


class Proxy:

    __slots__ = ('__ctx__', '__dict__')

    def __init__(self, name: str, default: Any = None) -> None:
        object.__setattr__(self, '__ctx__', ContextVar(name, default=default))

    @property
    def __dict__(self) -> Dict[str, Any]:  # type: ignore
        return self.__ctx__.get().__dict__

    def __dir__(self) -> List[str]:
        return dir(self.__ctx__.get())

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__ctx__.get(), name)

    def __delattr__(self, name: str) -> None:
        return delattr(self.__ctx__.get(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        return setattr(self.__ctx__.get(), name, value)

    def __hash__(self) -> int:
        return hash(self.__ctx__.get())

    def __str__(self) -> str:
        return str(self.__ctx__.get())

    def __int__(self) -> int:
        return int(self.__ctx__.get())

    def __bool__(self) -> bool:
        return bool(self.__ctx__.get())

    def __bytes__(self) -> bytes:
        return bytes(self.__ctx__.get())

    def __float__(self) -> float:
        return float(self.__ctx__.get())

    def __complex__(self) -> complex:
        return complex(self.__ctx__.get())

    def __repr__(self) -> str:
        return repr(self.__ctx__.get())

    def __format__(self, format_spec: str) -> str:
        return format(self.__ctx__.get(), format_spec)

    def __neg__(self) -> Any:
        return -(self.__ctx__.get())

    def __pos__(self) -> Any:
        return +(self.__ctx__.get())

    def __abs__(self) -> Any:
        return abs(self.__ctx__.get())

    def __invert__(self) -> Any:
        return ~(self.__ctx__.get())

    def __ceil__(self) -> Any:
        return ceil(self.__ctx__.get())

    def __floor__(self) -> Any:
        return floor(self.__ctx__.get())

    def __round__(self) -> Any:
        return round(self.__ctx__.get())

    def __trunc__(self) -> Any:
        return trunc(self.__ctx__.get())

    def __index__(self) -> int:
        return self.__ctx__.get().__index__()

    def __eq__(self, other: Any) -> bool:
        return self.__ctx__.get() == other

    def __ne__(self, other: Any) -> bool:
        return self.__ctx__.get() != other

    def __lt__(self, other: Any) -> bool:
        return self.__ctx__.get() < other

    def __le__(self, other: Any) -> bool:
        return self.__ctx__.get() <= other

    def __gt__(self, other: Any) -> bool:
        return self.__ctx__.get() > other

    def __ge__(self, other: Any) -> bool:
        return self.__ctx__.get() >= other

    def __copy__(self) -> 'Proxy':
        return copy(self.__ctx__.get())

    def __deepcopy__(self, memo: dict) -> 'Proxy':
        return deepcopy(self.__ctx__.get(), memo)

    def __enter__(self) -> Any:
        return self.__ctx__.get().__enter__()

    async def __aenter__(self) -> Any:
        return await self.__ctx__.get().__aenter__()

    def __exit__(self, *args: Any, **kwargs: Any) -> None:
        return self.__ctx__.get().__exit__(*args, **kwargs)

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        return await self.__ctx__.get().__aexit__(*args, **kwargs)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.__ctx__.get().__call__(*args, **kwargs)

    def __await__(self, *args: Any, **kwargs: Any) -> Any:
        return self.__ctx__.get().__await__(*args, **kwargs)

    def __len__(self) -> int:
        return len(self.__ctx__.get())

    def __contains__(self, obj: Any) -> bool:
        return obj in self.__ctx__.get()

    def __delitem__(self, key: str) -> None:
        return self.__ctx__.get().__delitem__(key)

    def __getitem__(self, key: str) -> Any:
        return self.__ctx__.get().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        return self.__ctx__.get().__setitem__(key, value)

    def __iter__(self) -> Iterable:
        return iter(self.__ctx__.get())

    def __next__(self) -> Any:
        return next(self.__ctx__.get())

    def __reversed__(self) -> Any:
        return reversed(self.__ctx__.get())

    def __or__(self, other: Any) -> Any:
        return self.__ctx__.get() | other

    def __and__(self, other: Any) -> Any:
        return self.__ctx__.get() & other

    def __xor__(self, other: Any) -> Any:
        return self.__ctx__.get() ^ other

    def __add__(self, other: Any) -> Any:
        return self.__ctx__.get() + other

    def __sub__(self, other: Any) -> Any:
        return self.__ctx__.get() - other

    def __mul__(self, other: Any) -> Any:
        return self.__ctx__.get() * other

    def __mod__(self, other: Any) -> Any:
        return self.__ctx__.get() % other

    def __pow__(self, other: Any) -> Any:
        return self.__ctx__.get() ** other

    def __lshift__(self, other: Any) -> Any:
        return self.__ctx__.get() << other

    def __rshift__(self, other: Any) -> Any:
        return self.__ctx__.get() >> other

    def __truediv__(self, other: Any) -> Any:
        return self.__ctx__.get() / other

    def __floordiv__(self, other: Any) -> Any:
        return self.__ctx__.get() // other

    def __divmod__(self, other: Any) -> Any:
        return self.__ctx__.get().__divmod__(other)

    def __ror__(self, other: Any) -> Any:
        return other | self.__ctx__.get()

    def __rand__(self, other: Any) -> Any:
        return other & self.__ctx__.get()

    def __rxor__(self, other: Any) -> Any:
        return other ^ self.__ctx__.get()

    def __radd__(self, other: Any) -> Any:
        return other + self.__ctx__.get()

    def __rsub__(self, other: Any) -> Any:
        return other - self.__ctx__.get()

    def __rmul__(self, other: Any) -> Any:
        return other * self.__ctx__.get()

    def __rmod__(self, other: Any) -> Any:
        return other % self.__ctx__.get()

    def __rpow__(self, other: Any) -> Any:
        return other ** self.__ctx__.get()

    def __rlshift__(self, other: Any) -> Any:
        return other << self.__ctx__.get()

    def __rrshift__(self, other: Any) -> Any:
        return other >> self.__ctx__.get()

    def __rtruediv__(self, other: Any) -> Any:
        return other / self.__ctx__.get()

    def __rfloordiv__(self, other: Any) -> Any:
        return other // self.__ctx__.get()

    def __rdivmod__(self, other: Any) -> Any:
        return self.__ctx__.get().__rdivmod__(other)


app: 'ipapp.app.BaseApplication' = Proxy('app', None)  # type: ignore
span: 'sp.Span' = Proxy('span', None)  # type: ignore
span_trap: 'sp.SpanTrap' = Proxy('span_trap', None)  # type: ignore
request: Request = Proxy('request', None)  # type: ignore

ctx: 'sp.Span' = span
req: Request = request
