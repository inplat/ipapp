from functools import wraps
from typing import Any, Callable, Optional, Type

from ..misc import ctx_app_get, ctx_request_get, ctx_span_get
from .adapters import (
    PrometheusConfig,
    RequestsConfig,
    SentryConfig,
    ZipkinConfig,
)
from .logger import Logger
from .span import HttpSpan, Span


def wrap2span(
    *,
    name: Optional[str] = None,
    kind: Optional[str] = None,
    cls: Type[Span] = Span,
) -> Callable:
    def create_wrapper(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            span = ctx_span_get()
            if span is None:
                app = ctx_app_get()
                if app is None:  # pragma: no cover
                    raise UserWarning

                web_request = ctx_request_get()
                if web_request is None:
                    new_span = app.logger.span_new(cls=cls)
                    new_span.kind = kind
                else:
                    new_span = app.logger.span_from_headers(
                        web_request.headers
                    )
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


__all__ = [
    "Span",
    "HttpSpan",
    "wrap2span",
    "ZipkinConfig",
    "PrometheusConfig",
    "SentryConfig",
    "RequestsConfig",
    "Logger",
]
