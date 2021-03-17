import collections
import inspect
import re
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Tuple,
    Type,
    Union,
)

import docstring_parser
from pydantic import BaseModel, create_model
from pydantic.schema import model_process_schema

from ipapp.rpc import RpcRegistry
from ipapp.rpc.error import RpcError
from ipapp.rpc.jsonrpc.error import JsonRpcError
from ipapp.rpc.main import Executor

from .models import (
    ContentDescriptor,
    Error,
    ExternalDocs,
    Info,
    Method,
    OpenRPC,
    ParamStructure,
    Schema,
    Server,
)

OPENRPC_VERSION = '1.2.4'


def discover(
    registry: Union[RpcRegistry, object],
    *,
    servers: Optional[List[Server]] = None,
    external_docs: Optional[ExternalDocs] = None,
) -> OpenRPC:
    methods = _get_methods(registry)
    model_name_map = ModelDict()
    schemas: Dict[str, Dict] = {}
    method_models = _get_methods_models(
        methods, model_name_map=model_name_map, schemas=schemas
    )

    for k, v in model_name_map.items():
        model_schema, model_definitions, _ = model_process_schema(
            k,
            model_name_map=model_name_map,
            ref_prefix='#/components/schemas/',
        )
        schemas[v] = model_schema

        for key in model_definitions.keys():
            if key not in schemas:
                schemas[key] = model_definitions[key]

    if isinstance(registry, RpcRegistry):

        info_kwargs = {'title': '', 'version': registry.version or '0'}
        if registry.title is not None:
            info_kwargs['title'] = registry.title
        if registry.description:
            info_kwargs['description'] = registry.description
    else:
        # legacy
        version = '0'
        if hasattr(registry, '__version__'):
            version = getattr(registry, '__version__')

        info_kwargs = {
            'title': '',
            'version': version,
        }

        if not isinstance(registry, list) and registry.__doc__:
            docstr = docstring_parser.parse(registry.__doc__)
            if docstr.short_description:
                info_kwargs['title'] = docstr.short_description
            if docstr.long_description:
                info_kwargs['description'] = docstr.long_description

    orpc_kwargs = {
        'openrpc': OPENRPC_VERSION,
        'info': Info(**info_kwargs),
        'methods': method_models,
    }
    if servers is not None:
        orpc_kwargs['servers'] = servers
    if external_docs is not None:
        orpc_kwargs['external_docs'] = external_docs
    if len(schemas) > 0:
        orpc_kwargs['components'] = {'schemas': schemas}

    return OpenRPC(**orpc_kwargs)


class ModelDict(collections.defaultdict):
    def __init__(
        self,
        default_factory: Optional[Callable[[], Any]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(default_factory, **kwargs)
        self.name_model_map: Dict[str, Type['BaseModel']] = {}

    def __missing__(self, model: Type['BaseModel']) -> str:
        model_name = (
            hasattr(model, "__config__")
            and model.__config__.title
            or model.__name__
        )
        model_name = re.sub(r"[^a-zA-Z0-9.\-_]", "_", model_name)

        if model_name in self.name_model_map:
            long_name = model_name
            c = 0
            while long_name == model_name or long_name in self.name_model_map:
                long_name = _snake_to_camel(_get_long_model_name(model))
                if c > 0:
                    long_name = long_name + str(c)
                c += 1
            model_name = long_name

        self[model] = model_name
        self.name_model_map[model_name] = model
        return model_name


def _get_methods(registry: Union[RpcRegistry, object]) -> Dict[str, Callable]:
    methods: Dict[str, Callable] = {}

    for fn in Executor.iter_handler(registry):
        if hasattr(fn, '__rpc_name__'):
            name = getattr(fn, '__rpc_name__')
            if name in methods:
                raise UserWarning('Method %s duplicated' '' % name)
            methods[name] = fn
    return methods


def _get_methods_models(
    methods: Dict[str, Callable],
    model_name_map: ModelDict,
    schemas: Dict[str, Dict],
) -> List[Method]:
    models: List[Method] = []
    for name in sorted(methods.keys()):
        fn = methods[name]
        method = _get_method(fn, model_name_map, schemas)
        models.append(method)
    return models


def _snake_to_camel(value: str) -> str:
    return value.title().replace("_", "")


def _fix_model_name(model: Any, name: str) -> None:
    if issubclass(model, BaseModel):
        setattr(model.__config__, "title", name)
    else:
        # TODO: warning
        setattr(model, "__name__", name)


def _get_field_definitions(
    parameters: Mapping[str, inspect.Parameter]
) -> List[Tuple[str, Any]]:
    return [
        (
            k,
            (
                (Any if v.annotation is v.empty else v.annotation),
                ... if v.default is v.empty else v.default,
            ),
        )
        for k, v in parameters.items()
        if v.kind is not v.VAR_KEYWORD and v.kind is not v.VAR_POSITIONAL
    ]


def _get_long_model_name(model: Type[BaseModel]) -> str:
    return f"{model.__module__}__{model.__name__}".replace(".", "__")


def _get_model_definition(
    model: Type[BaseModel], model_name_map: ModelDict, schemas: Dict[str, Dict]
) -> Any:
    """
    ВАЖНО! Аргументы model_name_map и schemas модифицируются в процессе
    выполнения функции
    """

    model_schema, model_definitions, a = model_process_schema(
        model,
        model_name_map=model_name_map,
        ref_prefix='#/components/schemas/',
    )

    # TODO тут стоит сверить с существющими моделями. Чтоб исключить дубликаты
    # обновляем по ссылке все схемы связанных объектов
    schemas.update(model_definitions)

    # при обращении к элементу словаря, магия добавляет его в этот словарь и
    # все его дочерние модели
    _ = model_name_map[model]

    # хак для того чтоб в список components.schemas не попадали искусственно
    # созданные классы запроса и ответа
    # (MethodnameRequestParams и MethodnameResponse)
    del model_name_map[model]

    return model_schema


def _get_method(
    func: Callable, model_name_map: ModelDict, schemas: Dict[str, Dict]
) -> Method:
    sig = inspect.signature(func)
    docstr = inspect.getdoc(func)
    kwargs: Dict[str, Any] = {
        'paramStructure': ParamStructure.BY_NAME,
    }

    method_name: str = getattr(func, "__rpc_name__")
    request_params_model: Optional[Type[BaseModel]] = getattr(
        func, "__rpc_request_model__", None
    )
    response_result_model: Optional[Type[BaseModel]] = getattr(
        func, "__rpc_response_model__", None
    )
    deprecated: bool = getattr(func, "__rpc_deprecated__", False)
    summary: str = getattr(func, "__rpc_summary__", "")
    description: str = getattr(func, "__rpc_description__", "")

    kwargs['name'] = method_name
    if summary:
        kwargs['summary'] = summary
    if description:
        kwargs['description'] = description
    if deprecated:
        kwargs['deprecated'] = True

    params_docs: Dict[str, str] = {}
    result_doc: Optional[str] = None

    if docstr:
        doc = docstring_parser.parse(docstr)
        if 'summary' not in kwargs and doc.short_description:
            kwargs['summary'] = doc.short_description
        if 'description' not in kwargs and doc.long_description:
            kwargs['description'] = doc.long_description
        if doc.returns:
            # import docstring_parser.common
            # docstring_parser.common.DocstringReturns().description
            result_doc = doc.returns.description

        for p in doc.params:
            params_docs[p.arg_name] = p.description

    camel_method_name = _snake_to_camel(method_name)

    request_model_name = f"{camel_method_name}Request"
    request_params_model_name = f"{camel_method_name}RequestParams"
    response_model_name = f"{camel_method_name}Response"
    response_result_model_name = f"{camel_method_name}ResponseResult"

    defs = _get_field_definitions(sig.parameters)

    RequestParamsModel: Type[BaseModel] = request_params_model or create_model(
        request_params_model_name, **{a: b for a, b in defs}
    )

    if getattr(RequestParamsModel, "__name__", "") == request_model_name:
        _fix_model_name(RequestParamsModel, request_params_model_name)

    params_def = _get_model_definition(
        RequestParamsModel, model_name_map, schemas
    )

    kwargs['params'] = []

    for name, typ in defs:
        schema = params_def['properties'][name]
        required = 'required' in params_def and name in params_def['required']
        params_kwargs = dict(
            name=name,
            # summary
            # description
            required=required,
            schema=Schema(**schema),
            # deprecated: bool = False
        )
        if name in params_docs:
            params_kwargs['summary'] = params_docs[name]
        kwargs['params'].append(ContentDescriptor(**params_kwargs))

    ResponseResultModel = response_result_model or (
        Any if sig.return_annotation is sig.empty else sig.return_annotation
    )
    response = {}
    if ResponseResultModel is not None:
        if getattr(ResponseResultModel, "__name__", "") == response_model_name:
            _fix_model_name(ResponseResultModel, response_result_model_name)

        response["result"] = (ResponseResultModel, None)

        ResponseModel: Type[BaseModel] = create_model(
            response_model_name, **response  # type: ignore
        )

        params_def = _get_model_definition(
            ResponseModel, model_name_map, schemas
        )

        result_schema = params_def['properties']['result']
    else:
        result_schema = {}

    result_kwargs = dict(
        name='result',
        # summary
        # description
        required=True,
        schema=Schema(**result_schema),
        # deprecated: bool = False)
    )
    if result_doc:
        result_kwargs['summary'] = result_doc

    kwargs['result'] = ContentDescriptor(**result_kwargs)

    examples = _get_examples(func)
    if examples:
        kwargs['examples'] = examples

    errors = _get_errors(func)
    if errors:
        kwargs['errors'] = errors

    return Method(**kwargs)


def _get_examples(func: Callable) -> Optional[List[dict]]:
    examples: List[Dict[str, Any]] = getattr(func, "__rpc_examples__", [])
    if examples is not None and len(examples) > 0:
        res = []
        for ex in examples:
            res.append(ex)
        return res
    return None


def _get_errors(func: Callable) -> Optional[List[Error]]:
    errors: Optional[List[Type[RpcError]]] = getattr(
        func, "__rpc_errors__", None
    )
    if errors is not None and len(errors) > 0:
        error_models: List[Error] = []
        for error in errors:
            if isinstance(error, type) and issubclass(error, JsonRpcError):
                error_models.append(
                    Error(
                        code=error.jsonrpc_error_code,
                        message=error.message,
                        data=getattr(error, 'data', None),
                    )
                )
        if len(error_models):
            return error_models
    return None
