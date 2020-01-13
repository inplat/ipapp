from contextvars import Token
from functools import wraps
from types import TracebackType
from typing import Any, Callable, Optional, Type

import ipapp  # noqa
import ipapp.misc

from .logger import Logger
from .span import Span


def wrap2span(
    *,
    name: Optional[str] = None,
    kind: Optional[str] = None,
    cls: Type[Span] = Span,
    ignore_ctx: bool = False,
    app: Optional['ipapp.BaseApplication'] = None,
) -> '_Wrapper':
    return _Wrapper(name, kind, cls, ignore_ctx, app)


class _Wrapper:
    def __init__(
        self,
        name: Optional[str] = None,
        kind: Optional[str] = None,
        cls: Type[Span] = Span,
        ignore_ctx: bool = False,
        app: Optional['ipapp.BaseApplication'] = None,
    ) -> None:
        self.name = name
        self.kind = kind
        self.cls = cls
        self.ignore_ctx = ignore_ctx
        self.app = app
        self.span: Optional[Span] = None
        self._app_token: Optional[Token] = None

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            span = ipapp.misc.ctx_span_get()
            if span is None or self.ignore_ctx:
                if self.app is None:
                    app = ipapp.misc.ctx_app_get()
                    if app is None:  # pragma: no cover
                        raise UserWarning
                else:
                    app = self.app
                    if app is not None:
                        self._app_token = ipapp.misc.ctx_app_set(app)
                new_span = app.logger.span_new(
                    self.name, self.kind, cls=self.cls
                )
            else:
                new_span = span.new_child(self.name, self.kind, cls=self.cls)
            with new_span:
                try:
                    return await func(*args, **kwargs)
                except Exception as err:
                    new_span.error(err)
                    raise
                finally:
                    if self._app_token is not None:
                        ipapp.misc.ctx_app_reset(self._app_token)

        return wrapper

    def __enter__(self) -> Span:
        span = ipapp.misc.ctx_span_get()
        if span is None or self.ignore_ctx:
            if self.app is None:
                app = ipapp.misc.ctx_app_get()
            else:
                app = self.app
                if app is not None:
                    self._app_token = ipapp.misc.ctx_app_set(app)
            if app is None:  # pragma: no cover
                raise UserWarning
            self.span = app.logger.span_new(self.name, self.kind, cls=self.cls)
        else:
            self.span = span.new_child(self.name, self.kind, cls=self.cls)

        self.span.__enter__()

        return self.span

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if self.span is None:
            return

        self.span.__exit__(exc_type, exc_value, traceback)

        if self._app_token is not None:
            ipapp.misc.ctx_app_reset(self._app_token)


__all__ = [
    "Span",
    "wrap2span",
    "Logger",
]
