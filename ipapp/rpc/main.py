import asyncio
import base64
import binascii
import inspect
import warnings
from functools import wraps
from types import MethodType
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
    Type,
    Union,
)

from jsonschema import FormatChecker as JSONFormatChecker
from jsonschema import validate as json_validate
from jsonschema.exceptions import SchemaError as JSONSchemaError
from jsonschema.exceptions import ValidationError as JSONValidationError
from pydantic import (
    BaseConfig,
    BaseModel,
    ValidationError,
    create_model,
    validator,
)

from ipapp.misc import BASE64_MARKER

from .error import InvalidArguments, MethodNotFound, RpcError


def to_bytes(value: str) -> Union[str, bytes]:
    if value.startswith(BASE64_MARKER):
        value_ = value[len(BASE64_MARKER) :]
        try:
            return base64.b64decode(value_, validate=True)
        except binascii.Error:
            pass
    return value


def parse_collection(col: Union[dict, list]) -> Union[dict, list]:
    def check_value(
        value_: Union[dict, list, str]
    ) -> Union[bytes, dict, list, str]:
        if isinstance(value_, str):
            return to_bytes(value_)
        if isinstance(value_, (dict, list)):
            parse_collection(value_)
        return value_

    if isinstance(col, dict):
        for key, value in col.items():
            col[key] = check_value(value)
    if isinstance(col, list):
        for idx, value in enumerate(col):
            col[idx] = check_value(value)

    return col


def base64_validator(
    cls: BaseModel, v: Union[dict, list, str]
) -> Union[bytes, dict, list, str]:
    if isinstance(v, str):
        return to_bytes(v)
    if isinstance(v, (dict, list)):
        return parse_collection(v)
    return v


validators = {
    'base64_validator': validator('*', pre=True)(base64_validator),
}


class _PydanticConfig(BaseConfig):
    arbitrary_types_allowed = True


class RpcRegistry(list):
    def __init__(
        self,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        version: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.title = title
        self.description = description
        self.version = version

    def method(
        self,
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

            _validate_method(
                func,
                func_name,
                errors,
                deprecated,
                summary,
                description,
                request_model,
                response_model,
                request_ref,
                response_ref,
                validators,
                examples,
            )

            setattr(func, '__rpc_registry__', self)
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
            self.append(func)
            return func

        return decorator


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
    warnings.warn(
        "@method decorator is deprecated. User RpcRegistry instead.\n"
        "Also use RpcRegistry as handler object.\n"
        "Example:\n\n"
        "reg = RpcRegistry()\n"
        "\n"
        "@reg.method()\n"
        "def some_method(): pass\n\n"
        "IMPORTANT! Do not use \"self\" argument! "
        "Function can not be a method",
        DeprecationWarning,
        stacklevel=2,
    )

    def decorator(func: Callable) -> Callable:
        func_name = name or func.__name__

        _validate_method(
            func,
            func_name,
            errors,
            deprecated,
            summary,
            description,
            request_model,
            response_model,
            request_ref,
            response_ref,
            validators,
            examples,
        )

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

        @wraps(func)
        def wrapper(*args: Any, **kwrags: Any) -> Callable:
            return func(*args, **kwrags)

        return wrapper

    return decorator


def _validate_method(
    func: Callable,
    func_name: str,
    errors: Optional[List[Type[RpcError]]],
    deprecated: Optional[bool],
    summary: str,
    description: str,
    request_model: Optional[Any],
    response_model: Optional[Any],
    request_ref: Optional[str],
    response_ref: Optional[str],
    validators: Optional[Dict[str, dict]],
    examples: Optional[List[Dict[str, Optional[str]]]],
) -> None:
    if func_name is not None and not isinstance(func_name, str):
        raise UserWarning('Method name must be a string')
    if deprecated is not None and not isinstance(deprecated, bool):
        raise UserWarning('Method deprecated must be a bool')
    if summary is not None and not isinstance(summary, str):
        raise UserWarning('Method summary must be a string')
    if description is not None and not isinstance(description, str):
        raise UserWarning('Method description must be a string')
    if hasattr(func.__code__, 'co_posonlyargcount'):
        if getattr(func.__code__, 'co_posonlyargcount') > 0:
            raise UserWarning('Positional-Only arguments are not supported')
    if request_model is not None and (
        not isinstance(request_model, type)
        or not issubclass(request_model, BaseModel)
    ):
        raise UserWarning(
            'Method request_model must be a subclass ' 'of pydantic.BaseModel'
        )
    if response_model is not None and (
        not isinstance(response_model, type)
        or not issubclass(response_model, BaseModel)
    ):
        raise UserWarning(
            'Method response_model must be a subclass ' 'of pydantic.BaseModel'
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

    if validators is not None:
        unknown = set(validators.keys()) - set(func.__code__.co_varnames)
        if unknown:
            raise UserWarning(
                "Found validator(s) for nonexistent argument(s): "
                ", ".join(unknown)
            )


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
                __validators__=validators,
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
                    loc = '.'.join(str(loc) for loc in e['loc'])
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
    def __init__(self, registry: Union[RpcRegistry, object]) -> None:
        self._handler = registry
        self._methods: Dict[str, _Method] = {}
        for fn in self.iter_handler(registry):
            name = getattr(fn, '__rpc_name__', fn.__name__)
            if getattr(fn, '__rpc_name__') in self._methods:
                raise UserWarning('Method %s duplicated' '' % name)
            self._methods[name] = _Method(fn)

    @staticmethod
    def iter_handler(
        registry: Union[RpcRegistry, object]
    ) -> Generator[Callable, None, None]:
        if isinstance(registry, RpcRegistry):
            for fn in registry:
                if not hasattr(fn, '__rpc_name__'):
                    raise UserWarning('Invalid handler %s' % fn)
                yield fn
        else:
            for key in dir(registry):
                if callable(getattr(registry, key)):
                    fn = getattr(registry, key)
                    if hasattr(fn, '__rpc_name__'):
                        yield fn

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
