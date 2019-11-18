from copy import copy, deepcopy
from contextvars import ContextVar
from math import floor, trunc, ceil

from aiohttp.web import Request

import ipapp.app  # noqa
import ipapp.logger  # noqa


class Proxy:

    __slots__ = ('__ctx__', '__dict__')

    def __init__(self, name, default=None):
        object.__setattr__(self, '__ctx__', ContextVar(name, default=default))

    @property
    def __dict__(self):
        return self.__ctx__.get().__dict__

    def __dir__(self):
        return dir(self.__ctx__.get())

    def __getattr__(self, name):
        return getattr(self.__ctx__.get(), name)

    def __delattr__(self, name):
        return delattr(self.__ctx__.get(), name)

    def __setattr__(self, name, value):
        return setattr(self.__ctx__.get(), name, value)

    def __hash__(self):
        return hash(self.__ctx__.get())

    def __str__(self):
        return str(self.__ctx__.get())

    def __int__(self):
        return int(self.__ctx__.get())

    def __bool__(self):
        return bool(self.__ctx__.get())

    def __bytes__(self):
        return bytes(self.__ctx__.get())

    def __float__(self):
        return float(self.__ctx__.get())

    def __complex__(self):
        return complex(self.__ctx__.get())

    def __repr__(self):
        return repr(self.__ctx__.get())

    def __format__(self, format_spec):
        return format(self.__ctx__.get(), format_spec)

    def __neg__(self):
        return -(self.__ctx__.get())

    def __pos__(self):
        return +(self.__ctx__.get())

    def __abs__(self):
        return abs(self.__ctx__.get())

    def __invert__(self):
        return ~(self.__ctx__.get())

    def __ceil__(self):
        return ceil(self.__ctx__.get())

    def __floor__(self):
        return floor(self.__ctx__.get())

    def __round__(self):
        return round(self.__ctx__.get())

    def __trunc__(self):
        return trunc(self.__ctx__.get())

    def __index__(self):
        return self.__ctx__.get().__index__()

    def __eq__(self, other):
        return self.__ctx__.get() == other

    def __ne__(self, other):
        return self.__ctx__.get() != other

    def __lt__(self, other):
        return self.__ctx__.get() < other

    def __le__(self, other):
        return self.__ctx__.get() <= other

    def __gt__(self, other):
        return self.__ctx__.get() > other

    def __ge__(self, other):
        return self.__ctx__.get() >= other

    def __copy__(self):
        return copy(self.__ctx__.get())

    def __deepcopy__(self, memo):
        return deepcopy(self.__ctx__.get(), memo)

    def __enter__(self):
        return self.__ctx__.get().__enter__()

    async def __aenter__(self):
        return await self.__ctx__.get().__aenter__()

    def __exit__(self, *args, **kwargs):
        return self.__ctx__.get().__exit__(*args, **kwargs)

    async def __aexit__(self, *args, **kwargs):
        return await self.__ctx__.get().__aexit__(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self.__ctx__.get().__call__(*args, **kwargs)

    def __await__(self, *args, **kwargs):
        return self.__ctx__.get().__await__(*args, **kwargs)

    def __len__(self):
        return len(self.__ctx__.get())

    def __contains__(self, obj):
        return obj in self.__ctx__.get()

    def __delitem__(self, key):
        return self.__ctx__.get().__delitem__(key)

    def __getitem__(self, key):
        return self.__ctx__.get().__getitem__(key)

    def __setitem__(self, key, value):
        return self.__ctx__.get().__setitem__(key, value)

    def __iter__(self):
        return iter(self.__ctx__.get())

    def __next__(self):
        return next(self.__ctx__.get())

    def __reversed__(self):
        return reversed(self.__ctx__.get())

    def __or__(self, other):
        return self.__ctx__.get() | other

    def __and__(self, other):
        return self.__ctx__.get() & other

    def __xor__(self, other):
        return self.__ctx__.get() ^ other

    def __add__(self, other):
        return self.__ctx__.get() + other

    def __sub__(self, other):
        return self.__ctx__.get() - other

    def __mul__(self, other):
        return self.__ctx__.get() * other

    def __mod__(self, other):
        return self.__ctx__.get() % other

    def __pow__(self, other):
        return self.__ctx__.get() ** other

    def __lshift__(self, other):
        return self.__ctx__.get() << other

    def __rshift__(self, other):
        return self.__ctx__.get() >> other

    def __truediv__(self, other):
        return self.__ctx__.get() / other

    def __floordiv__(self, other):
        return self.__ctx__.get() // other

    def __divmod__(self, other):
        return self.__ctx__.get().__divmod__(other)

    def __ror__(self, other):
        return other | self.__ctx__.get()

    def __rand__(self, other):
        return other & self.__ctx__.get()

    def __rxor__(self, other):
        return other ^ self.__ctx__.get()

    def __radd__(self, other):
        return other + self.__ctx__.get()

    def __rsub__(self, other):
        return other - self.__ctx__.get()

    def __rmul__(self, other):
        return other * self.__ctx__.get()

    def __rmod__(self, other):
        return other % self.__ctx__.get()

    def __rpow__(self, other):
        return other ** self.__ctx__.get()

    def __rlshift__(self, other):
        return other << self.__ctx__.get()

    def __rrshift__(self, other):
        return other >> self.__ctx__.get()

    def __rtruediv__(self, other):
        return other / self.__ctx__.get()

    def __rfloordiv__(self, other):
        return other // self.__ctx__.get()

    def __rdivmod__(self, other):
        return self.__ctx__.get().__rdivmod__(other)


app: 'ipapp.app.Application' = Proxy('app', None)  # type: ignore
span: 'ipapp.logger.Span' = Proxy('span', None)  # type: ignore
request: Request = Proxy('request', None)  # type: ignore

ctx: 'ipapp.logger.Span' = span
req: Request = request
