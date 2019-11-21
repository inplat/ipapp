from functools import wraps
from typing import Any, Callable, Optional, Type

import ipapp.misc

from .logger import Logger
from .span import Span


def wrap2span(
    *,
    name: Optional[str] = None,
    kind: Optional[str] = None,
    cls: Type[Span] = Span,
    ignore_ctx: bool = False,
) -> Callable:
    def create_wrapper(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            span = ipapp.misc.ctx_span_get()
            if span is None or ignore_ctx:
                app = ipapp.misc.ctx_app_get()
                if app is None:  # pragma: no cover
                    raise UserWarning
                new_span = app.logger.span_new(name, kind, cls=cls)
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
    "wrap2span",
    "Logger",
]
