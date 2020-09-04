import re
import sys
import time
import traceback
from contextvars import Token
from types import TracebackType
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type

import aiozipkin.helpers as azh
import aiozipkin.utils as azu

import ipapp.logger  # noqa
import ipapp.misc as misc

RE_P8S_METRIC_NAME = re.compile(r'[^a-zA-Z0-9_]')


class Span:
    KIND_CLIENT = 'CLIENT'
    KIND_SERVER = 'SERVER'

    TAG_ERROR = 'error'
    TAG_ERROR_CLASS = 'error.class'
    TAG_ERROR_MESSAGE = 'error.message'

    ANN_TRACEBACK = 'traceback'

    def __init__(
        self,
        logger: Optional['ipapp.logger.Logger'],
        trace_id: str,
        id: Optional[str] = None,
        parent_id: Optional[str] = None,
        parent: Optional['Span'] = None,
    ) -> None:
        self.logger = logger
        self.trace_id = trace_id
        if id is None:
            self.id = azu.generate_random_64bit_string()
        else:
            self.id = id
        self.parent = parent
        self.parent_id: Optional[str] = parent_id
        if parent is not None and self.parent_id is None:
            self.parent_id = parent.id
        self._skip = False
        self._children: List['Span'] = []

        self._name: str = ''
        self._name4adapter: Dict[str, str] = {}
        self._kind: Optional[str] = None
        self._annotations: Dict[str, List[Tuple[str, float]]] = {}
        self._annotations4adapter: Dict[
            str, Dict[str, List[Tuple[str, float]]]
        ] = {}
        self._tags: Dict[str, str] = {}
        self._tags4adapter: Dict[str, Dict[str, str]] = {}
        self._start_stamp: Optional[float] = None
        self._finish_stamp: Optional[float] = None
        self._exception: Optional[BaseException] = None
        self._is_handled = False
        self._ctx_token: Optional[Token] = None

    def skip(self) -> 'Span':
        """
        Не сохранять нигде этот span
        :return:
        """
        self._skip = True
        for child in self._children:
            child.skip()
        return self

    def to_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            azh.TRACE_ID_HEADER: self.trace_id,
            azh.SPAN_ID_HEADER: self.id,
            azh.FLAGS_HEADER: '0',
            azh.SAMPLED_ID_HEADER: '1' if not self._skip else '0',
        }
        if self.parent_id is not None:
            headers[azh.PARENT_ID_HEADER] = self.parent_id
        return headers

    @classmethod
    def from_headers(
        cls,
        headers: Optional[Mapping],
        app: Optional['ipapp.app.BaseApplication'] = None,
    ) -> 'Span':
        if headers is not None:
            headers = {k.lower(): v for k, v in headers.items()}
        else:
            headers = {}

        sampled = azh.parse_sampled_header(headers)

        if azh.TRACE_ID_HEADER.lower() not in headers:
            span = cls.new()
        else:
            trace_id = headers.get(azh.TRACE_ID_HEADER.lower())
            if not trace_id:
                trace_id = azu.generate_random_128bit_string()
            if app is None:
                app = misc.ctx_app_get()
                if app is None:  # pragma: no cover
                    raise UserWarning
            span = cls(
                logger=app.logger,
                trace_id=trace_id,
                id=azu.generate_random_64bit_string(),
                parent_id=headers.get(azh.SPAN_ID_HEADER.lower()),
            )

        if sampled is not None and not sampled:
            span.skip()

        return span

    @classmethod
    def new(
        cls,
        name: Optional[str] = None,
        kind: Optional[str] = None,
        app: Optional['ipapp.app.BaseApplication'] = None,
    ) -> 'Span':
        if app is None:
            app = misc.ctx_app_get()
            if app is None:  # pragma: no cover
                raise UserWarning
        span = cls(
            logger=app.logger,
            trace_id=azu.generate_random_128bit_string(),
            id=azu.generate_random_64bit_string(),
        )
        if name is not None:
            span.name = name
        if kind:
            span.kind = kind
        return span

    def new_child(
        self,
        name: Optional[str] = None,
        kind: Optional[str] = None,
        cls: Optional[Type['Span']] = None,
    ) -> 'Span':
        if cls is None:
            cls = Span
        span = cls(
            logger=self.logger,
            trace_id=self.trace_id,
            id=azu.generate_random_64bit_string(),
            parent=self,
        )
        if self._skip:
            span.skip()
        if name is not None:
            span.name = name
        if kind:
            span.kind = kind
        self._children.append(span)
        return span

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value

    def set_name4adapter(self, adapter: str, name: str) -> None:
        self._name4adapter[adapter] = name

    def get_name4adapter(
        self, adapter: str, merge: bool = True
    ) -> Optional[str]:
        if merge:
            return self._name4adapter.get(adapter, self.name)
        else:
            return self._name4adapter.get(adapter)

    @property
    def kind(self) -> Optional[str]:
        return self._kind

    @kind.setter
    def kind(self, value: str) -> None:
        self._kind = value

    @property
    def tags(self) -> Dict[str, str]:
        return self._tags

    def tag(self, name: str, value: Any) -> 'Span':
        self.tags[name] = str(value)
        return self

    def set_tag4adapter(self, adapter: str, name: str, value: Any) -> None:
        if adapter not in self._tags4adapter:
            self._tags4adapter[adapter] = {}
        self._tags4adapter[adapter][name] = str(value)

    def get_tags4adapter(
        self, adapter: str, merge: bool = True
    ) -> Dict[str, str]:
        if adapter not in self._tags4adapter:
            tags: Dict[str, str] = {}
        else:
            tags = self._tags4adapter[adapter]

        if merge:
            return misc.dict_merge(self._tags, tags)
        else:
            return tags

    @property
    def annotations(self) -> Dict[str, List[Tuple[str, float]]]:
        return self._annotations

    def annotate(
        self, kind: str, value: Any, ts: Optional[float] = None
    ) -> 'Span':
        if kind not in self._annotations:
            self._annotations[kind] = []
        self._annotations[kind].append((str(value), ts or time.time()))
        return self

    def annotate4adapter(
        self, adapter: str, kind: str, value: Any, ts: Optional[float] = None
    ) -> None:
        if adapter not in self._annotations4adapter:
            self._annotations4adapter[adapter] = {}
        if kind not in self._annotations4adapter[adapter]:
            self._annotations4adapter[adapter][kind] = []
        self._annotations4adapter[adapter][kind].append(
            (str(value), ts or time.time())
        )

    def get_annotations4adapter(
        self, adapter: str, merge: bool = True
    ) -> Dict[str, List[Tuple[str, float]]]:
        if adapter not in self._annotations4adapter:
            anns: Dict[str, List[Tuple[str, float]]] = {}
        else:
            anns = self._annotations4adapter[adapter]

        if merge:
            return misc.dict_merge(self._annotations, anns)
        else:
            return anns

    def error(self, err: BaseException) -> 'Span':
        self.tag(self.TAG_ERROR, 'true')
        self.tag(self.TAG_ERROR_CLASS, err.__class__.__name__)
        self.tag(self.TAG_ERROR_MESSAGE, str(err))
        self._exception = err

        trace: Optional[str] = None
        has_tb = (
            hasattr(err, '__traceback__') and err.__traceback__ is not None
        )
        if has_tb:
            trace = "".join(traceback.format_tb(err.__traceback__))
        elif hasattr(err, 'trace') and isinstance(err.trace, str):  # type: ignore
            # RPC err
            trace = err.trace  # type: ignore
        else:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            if exc_type is not None:
                trace = "".join(
                    traceback.format_exception(
                        exc_type, exc_value, exc_traceback
                    )
                )

        if trace is not None:
            self.annotate(
                self.ANN_TRACEBACK,
                trace,
            )

        return self

    def get_error(self) -> Optional[BaseException]:
        return self._exception

    @property
    def start_stamp(self) -> Optional[float]:
        return self._start_stamp

    @property
    def finish_stamp(self) -> Optional[float]:
        return self._finish_stamp

    @property
    def duration(self) -> float:
        if self._start_stamp is None:
            return 0
        elif self._finish_stamp is None:
            return time.time() - self._start_stamp
        else:
            return self._finish_stamp - self._start_stamp

    def start(self, ts: Optional[float] = None) -> 'Span':
        self._start_stamp = ts or time.time()
        if self.logger is not None:
            self.logger._span_started(self)  # noqa
        return self

    def finish(
        self,
        ts: Optional[float] = None,
        exception: Optional[BaseException] = None,
    ) -> 'Span':
        self._finish_stamp = ts or time.time()
        if exception is not None:
            self.error(exception)
        if self.logger is not None and self.logger.app is not None:
            self.logger.app.loop.call_soon(self._hasndle_spans)
        return self

    def _hasndle_spans(self) -> None:
        if self.parent is None or self.parent._is_handled:
            self._handle_children(self)
            if not self._skip and self.logger is not None:
                self.logger.handle_span(self)
            self._is_handled = True

        if self.logger is not None:
            self.logger._span_finished(self)  # noqa

    def move(self, parent: 'Span') -> None:
        if self._is_handled:
            raise UserWarning('Moving error. Span is handled')

        if self.parent is not None:
            self.parent._children.remove(self)
        parent._children.append(self)
        self.parent_id = parent.id
        self.parent = parent
        if parent._is_handled:
            self._handle_children(self)
            if not self._skip and self.logger is not None:
                self.logger.handle_span(self)
            self._is_handled = True

    def _handle_children(self, span: 'Span') -> None:
        for child in span._children:
            if child._finish_stamp is not None:
                if not child._skip:
                    self._handle_children(child)
                    if self.logger is not None:
                        self.logger.handle_span(child)
                child._is_handled = True

    def __enter__(self) -> 'Span':
        self.start()
        wrap = misc.ctx_span_trap_get()
        if wrap is not None:
            for item in wrap:
                item._set_span(self)

        self._ctx_token = misc.ctx_span_set(self)
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.finish(exception=exc_value)
        if self._ctx_token is not None:
            misc.ctx_span_reset(self._ctx_token)

    def __str__(self) -> str:
        dur = self.duration * 1000
        d = ' in %.02f ms' % dur if self.start_stamp is not None else ''
        return '%s[%s]%s' % (self.__class__.__name__, self._name, d)

    def copy_to(
        self,
        target: 'Span',
        *,
        annotations: bool = False,
        tags: bool = False,
        error: bool = False,
    ) -> None:
        if annotations:
            for kind, anns in self._annotations.items():
                for value, ts in anns:
                    target.annotate(kind, value, ts)

            for adapter, adapter_anns in self._annotations4adapter.items():
                for kind, anns in adapter_anns.items():
                    for value, ts in anns:
                        target.annotate4adapter(adapter, kind, value, ts)

        if tags:
            for name, value in self._tags.items():
                target.tag(name, value)
            for adapter, adapter_tags in self._tags4adapter.items():
                for name, value in self._tags.items():
                    target.set_tag4adapter(adapter, name, value)

        if error:
            _err = self.get_error()
            if _err is not None:
                target.error(_err)


class SpanTrap:
    def __init__(self, cls: Type[Span]) -> None:
        self._cls = cls
        self._token: Optional[Token] = None
        self._span: Optional[Span] = None

    def __enter__(self) -> 'SpanTrap':
        traps = misc.ctx_span_trap_get()
        if traps is None:
            traps = []
            self._token = misc.ctx_span_trap_set(traps)
        traps.append(self)
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        traps = misc.ctx_span_trap_get()
        if traps is not None:
            traps.remove(self)

        if self._token is not None:
            misc.ctx_span_trap_reset(self._token)
            self._token = None

    @property
    def is_captured(self) -> bool:
        return self._span is not None

    @property
    def span(self) -> 'Span':
        if self._span is None:
            raise UserWarning('Span not captured yet')
        return self._span

    def _set_span(self, span: 'Span') -> None:
        if self._cls is None or isinstance(span, self._cls):
            self._span = span
