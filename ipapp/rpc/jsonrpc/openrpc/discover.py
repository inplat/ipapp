import re
import inspect
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Type, Set
import collections
import docstring_parser
from pydantic import BaseModel, create_model
from pydantic.schema import model_process_schema

from .models import (
    ContentDescriptor,
    ExternalDocs,
    Info,
    Method,
    OpenRPC,
    Schema,
    Server,
)

OPENRPC_VERSION = '1.2.4'


def discover(
    handler: object,
    *,
    servers: Optional[List[Server]] = None,
    external_docs: Optional[ExternalDocs] = None,
) -> OpenRPC:
    methods = _get_methods(handler)
    model_name_map = ModelDict()
    method_models = _get_methods_models(methods, model_name_map=model_name_map)

    schemas = {}
    for k,v in model_name_map.items():
        model_schema, model_definitions, _ = model_process_schema(
            k,
            model_name_map=model_name_map,
            ref_prefix='#/components/schemas/'
        )
        schemas[v] = model_schema

    version = '0'
    if hasattr(handler, '__version__'):
        version = getattr(handler, '__version__')

    info_kwargs = {
        'title': '',
        'version': version,
    }

    if handler.__doc__:
        docstr = docstring_parser.parse(handler.__doc__)
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
        orpc_kwargs['components'] = {
            'schemas': schemas
        }

    return OpenRPC(**orpc_kwargs)


class ModelDict(collections.defaultdict):

    def __init__(self, default_factory=None, **kwargs):
        super().__init__(default_factory, **kwargs)
        self.name_model_map:Dict[str, Type['BaseModel']] = {}
        self.conflicting_names: Set[str] = set()

    def __missing__(self, model: Type['BaseModel']) -> str:
        model_name = model.__config__.title or model.__name__
        model_name = re.sub(r"[^a-zA-Z0-9.\-_]", "_", model_name)
        if model_name in self.conflicting_names:
            model_name = _get_long_model_name(model)
            self.name_model_map[model_name] = model
        elif model_name in self.name_model_map:
            self.conflicting_names.add(model_name)
            conflicting_model = self.name_model_map.pop(model_name)
            self.name_model_map[
                _get_long_model_name(conflicting_model)
            ] = conflicting_model
            self.name_model_map[_get_long_model_name(model)] = model
        else:
            self[model] = model_name
            return model_name



def _get_methods(handler: object) -> Dict[str, Callable]:
    methods: Dict[str, Callable] = {}
    for key in dir(handler):
        if callable(getattr(handler, key)):
            fn = getattr(handler, key)
            if hasattr(fn, '__rpc_name__'):
                if fn.__rpc_name__ in methods:
                    raise UserWarning(
                        'Method %s duplicated' '' % fn.__rpc_name__
                    )
                methods[key] = fn
    return methods


def _get_methods_models(methods: Dict[str, Callable],model_name_map) -> List[Method]:
    models: List[Method] = []
    for name, fn in methods.items():
        method = _get_method(fn, model_name_map)
        models.append(method)
    return models


def _snake_to_camel(value: str) -> str:
    return value.title().replace("_", "")


def _fix_model_name(model: Type[BaseModel], name: str) -> None:
    if isinstance(model, type(BaseModel)):
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

def _get_model_name_map(
    unique_models: Set[Type[BaseModel]],
) -> Dict[Type[BaseModel], str]:
    name_model_map = {}
    conflicting_names: Set[str] = set()
    for model in unique_models:
        model_name = model.__config__.title or model.__name__
        model_name = re.sub(r"[^a-zA-Z0-9.\-_]", "_", model_name)
        if model_name in conflicting_names:
            model_name = _get_long_model_name(model)
            name_model_map[model_name] = model
        elif model_name in name_model_map:
            conflicting_names.add(model_name)
            conflicting_model = name_model_map.pop(model_name)
            name_model_map[
                _get_long_model_name(conflicting_model)
            ] = conflicting_model
            name_model_map[_get_long_model_name(model)] = model
        else:
            name_model_map[model_name] = model
    return {v: k for k, v in name_model_map.items()}


def _get_model_definition(model: Type[BaseModel], model_name_map: ModelDict) -> Any:
    definitions: Dict[str, Dict] = {}

    model_schema, model_definitions, _ = model_process_schema(
        model,
        model_name_map=model_name_map,
        ref_prefix='#/components/schemas/'
    )

    definitions.update(model_definitions)
    # model_name = model_name_map[model]
    return model_schema


def _get_method(func: Callable, model_name_map: ModelDict) -> Method:
    sig = inspect.signature(func)
    docstr = inspect.getdoc(func)
    kwargs = {}

    params_docs: Dict[str, str] = {}

    if docstr:
        doc = docstring_parser.parse(docstr)
        if doc.short_description:
            kwargs['summary'] = doc.short_description
        if doc.long_description:
            kwargs['description'] = doc.long_description

        for p in doc.params:
            params_docs[p.arg_name] = p.description

    method_name = getattr(func, "__rpc_name__")
    kwargs['name'] = method_name

    request_params_model = getattr(func, "__rpc_request_model__", None)
    response_result_model = getattr(func, "__rpc_response_model__", None)

    camel_method_name = _snake_to_camel(method_name)

    request_model_name = f"{camel_method_name}Request"
    request_params_model_name = f"{camel_method_name}RequestParams"
    response_model_name = f"{camel_method_name}Response"
    response_result_model_name = f"{camel_method_name}ResponseResult"

    defs = _get_field_definitions(sig.parameters)

    RequestParamsModel = request_params_model or create_model(
        request_params_model_name, **{a: b for a, b in defs}
    )

    if getattr(RequestParamsModel, "__name__", "") == request_model_name:
        _fix_model_name(RequestParamsModel, request_params_model_name)

    params_def = _get_model_definition(RequestParamsModel, model_name_map)

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

        ResponseModel = create_model(
            response_model_name, **response  # type: ignore
        )

        params_def = _get_model_definition(ResponseModel, model_name_map)

        result_schema = params_def['properties']['result']
    else:
        result_schema = {}

    kwargs['result'] = ContentDescriptor(
        name='result',
        # summary
        # description
        required=True,
        schema=Schema(**result_schema),
        # deprecated: bool = False
    )

    # Method(name=name, params=params, result=result, summary=summary,
    #        description=description)

    return Method(**kwargs)
