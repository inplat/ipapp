import json
import string
from contextvars import Token
from copy import deepcopy
from getpass import getuser
from random import SystemRandom
from typing import Any, List, Optional, Tuple
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit

from aiohttp import web
from deepmerge import Merger
from pydantic.json import pydantic_encoder

import ipapp.app  # noqa
import ipapp.logger.span  # noqa

from .ctx import app, request, span, span_trap


def ctx_app_get() -> Optional['ipapp.app.BaseApplication']:
    return app.__ctx__.get()  # type: ignore


def ctx_app_set(ctx: 'ipapp.app.BaseApplication') -> Token:
    return app.__ctx__.set(ctx)  # type: ignore


def ctx_app_reset(token: Token) -> None:
    app.__ctx__.reset(token)  # type: ignore


def ctx_request_get() -> Optional[web.Request]:
    return request.__ctx__.get()  # type: ignore


def ctx_request_set(ctx: web.Request) -> Token:
    return request.__ctx__.set(ctx)  # type: ignore


def ctx_request_reset(token: Token) -> None:
    request.__ctx__.reset(token)  # type: ignore


def ctx_span_get() -> Optional['ipapp.logger.span.Span']:
    return span.__ctx__.get()  # type: ignore


def ctx_span_set(ctx: 'ipapp.logger.span.Span') -> Token:
    return span.__ctx__.set(ctx)  # type: ignore


def ctx_span_reset(token: Token) -> None:
    span.__ctx__.reset(token)  # type: ignore


def ctx_span_trap_get() -> Optional[List['ipapp.logger.span.SpanTrap']]:
    return span_trap.__ctx__.get()  # type: ignore


def ctx_span_trap_set(ctx: List['ipapp.logger.span.SpanTrap']) -> Token:
    return span_trap.__ctx__.set(ctx)  # type: ignore


def ctx_span_trap_reset(token: Token) -> None:
    span_trap.__ctx__.reset(token)  # type: ignore


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


def json_encode(data: Any) -> str:
    return json.dumps(data, default=pydantic_encoder)


def parse_dsn(
    dsn: str, default_port: int = 5432, protocol: str = 'http://'
) -> Tuple[str, int, Optional[str], Optional[str], str]:
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
    return (
        parsed.hostname or 'localhost',
        parsed.port or default_port,
        unquote(parsed.username) if parsed.username is not None else getuser(),
        unquote(parsed.password) if parsed.password is not None else None,
        parsed.path.lstrip('/'),
    )


def rndstr(
    size: int = 6, chars: str = string.ascii_uppercase + string.digits
) -> str:
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


def decode_bytes(b: bytes, encoding: Optional[str] = None) -> str:
    if encoding is not None:
        try:
            return b.decode(encoding)
        except Exception:  # nosec
            pass
    try:
        return b.decode()
    except Exception:
        return str(b)
