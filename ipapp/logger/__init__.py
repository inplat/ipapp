from functools import wraps
from typing import Optional, Callable, Type

from .adapters import (ZipkinConfig, PrometheusConfig, SentryConfig,
                       RequestsConfig)
from .logger import Logger
from .span import Span, HttpSpan
from ..misc import (ctx_span_get, ctx_app_get,
                    ctx_request_get, )


def wrap2span(*, name: Optional[str] = None, kind: Optional[str] = None,
              cls: Type[Span] = Span) -> Callable:
    def create_wrapper(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            span = ctx_span_get()
            if span is None:
                app = ctx_app_get()
                if app is None:
                    raise UserWarning

                web_request = ctx_request_get()
                if web_request is None:
                    new_span = app.logger.span_new(cls=cls)
                    new_span.kind = kind
                else:
                    new_span = app.logger.span_from_headers(
                        web_request.headers)
                    new_span.kind = Span.KIND_SERVER
            else:
                new_span = span.new_child(name, kind, cls=cls)
            with new_span:
                try:
                    return await func(*args, **kwargs)
                except Exception as err:
                    new_span.error(err)
                    raise

        return wrapper

    return create_wrapper
