import datetime
import decimal
import inspect
import json
import string
import types
from asyncio import ensure_future
from contextvars import Token
from copy import deepcopy
from functools import partial
from getpass import getuser
from random import SystemRandom
from typing import Optional
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit

from aiohttp import web
from deepmerge import Merger
from yarl import URL

import ipapp.app  # noqa
import ipapp.logger  # noqa

from .ctx import app, request, span


def ctx_app_get() -> Optional['ipapp.app.Application']:
    return app.__ctx__.get()  # type: ignore


def ctx_app_set(ctx: 'ipapp.app.Application') -> Token:
    return app.__ctx__.set(ctx)  # type: ignore


def ctx_app_reset(token: Token) -> None:
    app.__ctx__.reset(token)  # type: ignore


def ctx_request_get() -> Optional[web.Request]:
    return request.__ctx__.get()  # type: ignore


def ctx_request_set(request: web.Request) -> Token:
    return request.__ctx__.set(request)  # type: ignore


def ctx_request_reset(token: Token) -> None:
    request.__ctx__.reset(token)  # type: ignore


def ctx_span_get() -> Optional['ipapp.logger.Span']:
    return span.__ctx__.get()  # type: ignore


def ctx_span_set(ctx: 'ipapp.logger.Span') -> Token:
    return span.__ctx__.set(ctx)  # type: ignore


def ctx_span_reset(token: Token) -> None:
    span.__ctx__.reset(token)  # type: ignore


def async_call(loop, func, *args, delay=None, **kwargs):
    """
    :type loop: asyncio.AbstractEventLoop
    :type func:
    :type args:
    :type delay: int
    :type delay: timedelta
    :type kwargs:
    :return:
    """
    res = {'fut': None}
    if isinstance(delay, datetime.timedelta):
        delay = delay.total_seconds()

    def _call(func, *args, **kwargs):
        fut = ensure_future(func(*args, **kwargs), loop=loop)
        res['fut'] = fut

    if delay:
        loop.call_later(delay, partial(_call, func, *args, **kwargs))
    else:
        loop.call_soon(partial(_call, func, *args, **kwargs))

    return res


def mask_url_pwd(route: Optional[str]) -> Optional[str]:
    if route is None:
        return None
    parsed = urlsplit(route)
    if '@' not in parsed.netloc:
        return route
    userinfo, _, location = parsed.netloc.partition('@')
    username, _, password = userinfo.partition(':')
    if not password:
        return route
    userinfo = ':'.join([username, '******'])
    netloc = '@'.join([userinfo, location])
    parsed = parsed._replace(netloc=netloc)
    return urlunsplit(parsed)


def get_func_params(method, called_params):
    """
    :type method: function
    :type called_params: dict
    :return:
    """
    insp = inspect.getfullargspec(method)
    if not isinstance(called_params, dict):
        raise UserWarning()
    _called_params = called_params.copy()
    params = {}
    arg_count = len(insp.args)
    arg_def_count = len(insp.defaults) if insp.defaults is not None else 0
    for i in range(arg_count):
        arg = insp.args[i]
        if i == 0 and isinstance(method, types.MethodType):
            continue  # skip self argument
        if arg in _called_params:
            params[arg] = _called_params.pop(arg)
        elif i - arg_count + arg_def_count >= 0:
            params[arg] = insp.defaults[i - arg_count + arg_def_count]
        else:
            raise TypeError('Argument "%s" not given' % arg)
    for kwarg in insp.kwonlyargs:
        if kwarg in _called_params:
            params[kwarg] = _called_params.pop(kwarg)
        elif kwarg in insp.kwonlydefaults:
            params[kwarg] = insp.kwonlydefaults[kwarg]
        else:
            raise TypeError('Argument "%s" not given' % kwarg)
    if insp.varkw is None:
        if len(_called_params) > 0:
            raise TypeError(
                'Got unexpected parameter(s): %s'
                '' % (", ".join(_called_params))
            )
    else:
        params.update(_called_params)
    return params


def _json_encoder(obj):
    if isinstance(obj, URL):
        return str(obj)
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, datetime.datetime):
        return obj.strftime('%Y-%m-%d %H:%M:%S.%f%z')
    if isinstance(obj, datetime.date):
        return obj.strftime('%Y-%m-%d')
    if isinstance(obj, datetime.time):
        return obj.strftime('%H:%M:%S.%f%z')
    if isinstance(obj, datetime.timedelta):
        return obj.total_seconds()
    if isinstance(obj, bytes):
        try:
            return obj.decode('UTF8')
        except Exception:
            return str(obj)
    return repr(obj)


def json_encode(data):
    return json.dumps(data, default=_json_encoder)


def parse_dsn(dsn, default_port=5432, protocol='http://'):
    """
    Разбирает строку подключения к БД и возвращает список из (host, port,
    username, password, dbname)
    :param dsn: Строка подключения. Например: username@localhost:5432/dname
    :type: str
    :param default_port: Порт по-умолчанию
    :type default_port: int
    :params protocol
    :type protocol str
    :return: [host, port, username, password, dbname]
    :rtype: list
    """
    parsed = urlparse(protocol + dsn)
    return [
        parsed.hostname or 'localhost',
        parsed.port or default_port,
        unquote(parsed.username) if parsed.username is not None else getuser(),
        unquote(parsed.password) if parsed.password is not None else None,
        parsed.path.lstrip('/'),
    ]


def rndstr(size=6, chars=string.ascii_uppercase + string.digits):
    cryptogen = SystemRandom()
    return ''.join(cryptogen.choice(chars) for _ in range(size))


dict_merger = Merger([(dict, "merge")], ["override"], ["override"])


def dict_merge(*args: dict) -> dict:
    if len(args) == 0:
        return {}

    first = deepcopy(args[0])
    for i in range(1, len(args)):
        dict_merger.merge(first, args[i])

    return first
