from aiohttp.web import Request

import ipapp.app  # noqa
import ipapp.logger  # noqa

from .proxy import Proxy


app: 'ipapp.app.Application' = Proxy('app', None)  # type: ignore
span: 'ipapp.logger.Span' = Proxy('span', None)  # type: ignore
request: Request = Proxy('request', None)  # type: ignore

ctx: 'ipapp.logger.Span' = span
req: Request = request
