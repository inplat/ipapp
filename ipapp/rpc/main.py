import asyncio
import inspect
from functools import wraps
from types import MethodType
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
    Type,
)

from jsonschema import FormatChecker as JSONFormatChecker
from jsonschema import validate as json_validate
from jsonschema.exceptions import SchemaError as JSONSchemaError
from jsonschema.exceptions import ValidationError as JSONValidationError
from pydantic import BaseConfig, BaseModel, ValidationError, create_model

from .error import InvalidArguments, MethodNotFound, RpcError


class _PydanticConfig(BaseConfig):
    arbitrary_types_allowed = True


def method(
    *,
    name: Optional[str] = None,
    errors: Optional[List[Type[RpcError]]] = None,
    deprecated: Optional[bool] = False,
    summary: str = "",
    description: str = "",
    request_model: Optional[Any] = None,
    response_model: Optional[Any] = None,
    request_ref: Optional[str] = None,
    response_ref: Optional[str] = None,
    validators: Optional[Dict[str, dict]] = None,
    examples: Optional[List[Dict[str, Optional[str]]]] = None,
) -> Callable:
    def decorator(func: Callable) -> Callable:
        func_name = name or func.__name__

        if func_name is not None and not isinstance(func_name, str):
            raise UserWarning('Method name must be a string')
        if deprecated is not None and not isinstance(deprecated, bool):
            raise UserWarning('Method deprecated must be a bool')
        if summary is not None and not isinstance(summary, str):
            raise UserWarning('Method summary must be a string')
        if description is not None and not isinstance(description, str):
            raise UserWarning('Method description must be a string')
        if request_model is not None and (
            not isinstance(request_model, type)
            or not issubclass(request_model, BaseModel)
        ):
            raise UserWarning(
                'Method request_model must be a subclass '
                'of pydantic.BaseModel'
            )
        if response_model is not None and (
            not isinstance(response_model, type)
            or not issubclass(response_model, BaseModel)
        ):
            raise UserWarning(
                'Method response_model must be a subclass '
                'of pydantic.BaseModel'
            )
        if request_ref is not None and not isinstance(request_ref, str):
            raise UserWarning('Method request_ref must be a string')
        if response_ref is not None and not isinstance(response_ref, str):
            raise UserWarning('Method response_ref must be a string')

        if errors is not None:
            for error in errors:
                if not isinstance(error, type):
                    raise UserWarning(
                        'Method errors must be a list of RpcError subclasses'
                    )
                if not issubclass(error, RpcError):
                    raise UserWarning(
                        'Method errors must be a list of RpcError subclasses'
                    )

        if examples is not None:
            _validate_examples(examples)

        setattr(func, "__rpc_name__", func_name)
        setattr(func, "__rpc_errors__", errors or [])
        setattr(func, "__rpc_deprecated__", deprecated)
        setattr(func, "__rpc_summary__", summary)
        setattr(func, "__rpc_description__", description)
        setattr(func, "__rpc_request_model__", request_model)
        setattr(func, "__rpc_response_model__", response_model)
        setattr(func, "__rpc_request_ref__", request_ref)
        setattr(func, "__rpc_response_ref__", response_ref)
        setattr(func, "__rpc_examples__", examples)

        if validators is not None:
            setattr(func, "__validators__", validators)
            unknown = set(validators.keys()) - set(func.__code__.co_varnames)
            if unknown:
                raise UserWarning(
                    "Found validator(s) for nonexistent argument(s): "
                    ", ".join(unknown)
                )

        @wraps(func)
        def wrapper(*args: Any, **kwrags: Any) -> Callable:
            return func(*args, **kwrags)

        return wrapper

    return decorator


def _validate_examples(examples: Any) -> None:
    if not isinstance(examples, list):
        raise UserWarning()
    struct: Dict[str, Optional[type]] = {
        'name': str,
        'description': str,
        'summary': str,
        'params': list,
        'result': None,
    }
    struct_keys = set(struct.keys())
    for ex in examples:
        if not isinstance(ex, dict):
            raise UserWarning
        ex_keys = set(ex.keys())
        if ex_keys - struct_keys:
            raise UserWarning(
                'Unexpected example keys %s' '' % (ex_keys - struct_keys,)
            )
        for key in ex.keys():
            if struct[key] is not None:
                cls = struct[key]
                if cls is not None and not isinstance(ex[key], cls):
                    raise UserWarning


class _Method:
    def __init__(self, func: Callable) -> None:
        self.func = func
        self._model: Optional[Type[BaseModel]] = None
        self._analyse_arguments(func)
        self._validators: Dict[str, dict] = {}
        if hasattr(func, '__validators__'):
            self._validators = func.__validators__  # type: ignore

    def _analyse_arguments(self, func: Callable) -> None:
        is_method = isinstance(func, MethodType)
        while hasattr(func, '__wrapped__'):
            func = func.__wrapped__  # type: ignore
        self.required_params: List[str] = []
        self.optional_params: Dict[str, Any] = {}
        self.params_order = []
        ispec = inspect.getfullargspec(func)
        self.is_kwargs = True if ispec.varkw is not None else False
        args = ispec.args
        if ispec.kwonlyargs:
            raise NotImplementedError(
                'Keyword-only arguments are not supported'
            )
        self_name = None
        if is_method:
            self_name = args.pop(0)  # rm self
        args_cnt = len(args)
        if ispec.defaults is not None:
            defaults_cnt = len(ispec.defaults)
        else:
            defaults_cnt = 0
        for i in range(args_cnt - defaults_cnt):
            self.required_params.append(args[i])
            self.params_order.append(args[i])
        for i in range(args_cnt - defaults_cnt, args_cnt):
            di = i - args_cnt + defaults_cnt
            if ispec.defaults is None:
                raise RuntimeError
            self.optional_params[args[i]] = ispec.defaults[di]
            self.params_order.append(args[i])
        kwargs = ispec.kwonlyargs
        kwargs_cnt = len(kwargs)
        if ispec.kwonlydefaults is not None:
            kwdefaults_cnt = len(ispec.kwonlydefaults)
        else:
            kwdefaults_cnt = 0
        for i in range(kwargs_cnt - kwdefaults_cnt):
            self.required_params.append(kwargs[i])
        for i in range(kwargs_cnt - kwdefaults_cnt, kwargs_cnt):
            if ispec.kwonlydefaults is None:
                raise RuntimeError
            self.optional_params[kwargs[i]] = ispec.kwonlydefaults[kwargs[i]]

        if len(ispec.annotations) > 0:
            opt = self.optional_params

            self._model = create_model(
                'Model',
                __config__=_PydanticConfig,
                **{  # type: ignore
                    k: (v, ... if k not in opt else opt[k])
                    for k, v in ispec.annotations.items()
                    if k != 'return' and k != self_name
                },
            )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        _kwargs = self._validate_arguments(args, kwargs)
        return self.func(**_kwargs)

    def _validate_arguments(
        self, args: Tuple[Any, ...], kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        if len(args) > 0:

            if len(self.params_order) < len(args) or len(args) < len(
                self.required_params
            ):
                raise InvalidArguments(
                    'Method takes %s positional arguments but %s %s given'
                    ''
                    % (
                        len(self.params_order),
                        len(args),
                        'were' if len(args) > 1 else 'was',
                    )
                )

            kwargs = {}
            for i in range(len(args)):
                kwargs[self.params_order[i]] = args[i]

        self._validate_required_arguments(kwargs)
        _args = kwargs.copy()

        for arg_name, arg_rule in self._validators.items():
            if arg_name in _args:
                val = _args[arg_name]
            else:
                val = self.optional_params[arg_name]

            try:
                json_validate(
                    schema=arg_rule,
                    instance=val,
                    format_checker=JSONFormatChecker(),
                )
            except JSONValidationError as err:
                raise InvalidArguments(
                    Exception('%s: %s' % (arg_name, err.message))
                ) from err
            except JSONSchemaError as err:
                raise UserWarning('Invalid JSON Schema definition: %s' % err)

        if self._model:
            try:
                model = self._model(**_args)
                for key in model.__fields__.keys():
                    _args[key] = getattr(model, key)

            except ValidationError as err:
                es: List[str] = []
                for e in err.errors():
                    loc = '.'.join(str(l) for l in e['loc'])
                    es.append('%s in %s' % (e['msg'], loc))
                raise InvalidArguments(Exception('; '.join(es)))

        return _args

    def _validate_required_arguments(self, kwargs: Dict[str, Any]) -> None:
        req = self.required_params.copy()
        for arg in kwargs.keys():
            if arg in req:
                req.remove(arg)
            elif arg in self.optional_params:
                pass
            elif self.is_kwargs:
                pass
            else:
                raise InvalidArguments(
                    Exception('Got an unexpected argument: %s' % arg)
                )
        if len(req) > 0:
            raise InvalidArguments(
                Exception(
                    'Missing %s required argument(s):  %s'
                    '' % (len(req), ', '.join(req))
                )
            )


class Executor:
    def __init__(
        self, handler: object, loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> None:
        self._handler = handler
        self._loop = loop
        self._methods: Dict[str, _Method] = {}
        for key in dir(self._handler):
            if callable(getattr(self._handler, key)):
                fn = getattr(self._handler, key)
                if hasattr(fn, '__rpc_name__'):
                    if fn.__rpc_name__ in self._methods:
                        raise UserWarning(
                            'Method %s duplicated' '' % fn.__rpc_name__
                        )
                    self._methods[fn.__rpc_name__] = _Method(fn)

    async def exec(
        self,
        name: str,
        args: Optional[Iterable[Any]] = None,
        kwargs: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        _args: Tuple[Any, ...] = tuple(args or ())
        _kwargs: Dict[str, Any] = dict(kwargs or {})
        if len(_args) > 0 and len(_kwargs) > 0:
            raise NotImplementedError('Only args or kwargs supported')

        fn = self._methods.get(name)
        if fn is None:
            raise MethodNotFound()

        result = fn(*_args, **_kwargs)
        if asyncio.iscoroutine(result):
            result = await result

        return result
