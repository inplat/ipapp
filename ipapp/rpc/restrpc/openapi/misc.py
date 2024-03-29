import inspect
import re
from datetime import datetime
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
)

from pydantic import BaseModel, Field, create_model
from pydantic.schema import (
    TypeModelOrEnum,
    TypeModelSet,
    get_flat_models_from_models,
    model_process_schema,
)

from ipapp.http.server import Server as HttpServer
from ipapp.openapi.models import (
    MediaType,
    Operation,
    PathItem,
    Reference,
    RequestBody,
    Response,
    Server,
)
from ipapp.rpc import RpcRegistry
from ipapp.rpc.restrpc import RestRpcExecutor
from ipapp.rpc.restrpc.error import (
    RestRpcError,
    RestRpcInvalidParamsError,
    RestRpcInvalidRequestError,
    RestRpcMethodNotFoundError,
    RestRpcParseError,
    RestRpcServerError,
)

REF_PREFIX = "#/components/schemas/"


def get_methods(registry: Union[RpcRegistry, object]) -> Dict[str, Callable]:
    methods: Dict[str, Callable] = {}
    for fn in RestRpcExecutor.iter_handler(registry):
        if hasattr(fn, "__rpc_name__"):
            name = getattr(fn, "__rpc_name__")
            if name in methods:
                raise UserWarning("Method %s duplicated" "" % name)
            methods[name] = fn
    return methods


def snake_to_camel(value: str) -> str:
    return value.title().replace("_", "")


def get_errors_from_func(func: Callable) -> List[Type[RestRpcError]]:
    errors = getattr(func, "__rpc_errors__", [])
    errors.extend(
        [
            RestRpcParseError,
            RestRpcInvalidRequestError,
            RestRpcMethodNotFoundError,
            RestRpcInvalidParamsError,
            RestRpcServerError,
        ]
    )
    return errors


def get_summary_description_from_func(func: Callable) -> Tuple[str, str]:
    doc = inspect.getdoc(func)
    method = getattr(func, "__rpc_name__", func.__name__)
    summary = getattr(func, "__rpc_summary__", "")
    description = getattr(func, "__rpc_description__", "")
    doc_lines = doc.strip().split("\n", maxsplit=1) if doc else []
    doc_summary = (
        doc_lines[0].strip() if doc_lines else method.replace("_", " ").title()
    )
    doc_description = (
        "\n".join(doc_lines[1:]).strip() if len(doc_lines) > 1 else ""
    )
    return (summary or doc_summary, description or doc_description)


def get_field_definitions(
    parameters: Mapping[str, inspect.Parameter]
) -> Dict[str, Any]:
    return {
        k: (v.annotation, ... if v.default is v.empty else v.default)
        for k, v in parameters.items()
        if v.kind is not v.VAR_KEYWORD and v.kind is not v.VAR_POSITIONAL
    }


def get_models_from_rpc_methods(methods: Dict[str, Callable]) -> TypeModelSet:
    clean_models: List[Type[BaseModel]] = [
        create_model(
            "Health",
            is_sick=(bool, False),
            checks=(Dict[str, str], Field(..., example={"srv": "ok"})),
            version=(str, Field(..., example="1.0.0")),
            start_time=(datetime, ...),
            up_time=(str, Field(..., example="0:00:12.850850")),
        )
    ]
    for method, func in methods.items():
        sig = inspect.signature(func)
        method_name = getattr(func, "__rpc_name__", method)
        request_params_model = getattr(func, "__rpc_request_model__", None)
        response_result_model = getattr(func, "__rpc_response_model__", None)
        camel_method_name = snake_to_camel(method_name)
        request_model_name = f"{camel_method_name}Request"
        response_model_name = f"{camel_method_name}Response"
        RequestModel = request_params_model or create_model(
            request_model_name, **get_field_definitions(sig.parameters)
        )
        fix_model_name(RequestModel, request_model_name)
        response: Dict[str, Any] = dict()
        ResponseResultModel = response_result_model or sig.return_annotation
        ResponseModel: Type[BaseModel] = create_model(
            response_model_name, **response
        )
        if ResponseResultModel is not None:
            if issubclass(ResponseResultModel, BaseModel):
                ResponseModel = ResponseResultModel
                fix_model_name(ResponseModel, response_model_name)
        clean_models.extend([RequestModel, ResponseModel])
    flat_models = get_flat_models_from_models(clean_models)
    return flat_models


def fix_model_name(model: Type[BaseModel], name: str) -> None:
    if isinstance(model, type(BaseModel)):
        setattr(model.__config__, "title", name)
    else:
        # TODO: warning
        setattr(model, "__name__", name)


def get_long_model_name(model: TypeModelOrEnum) -> str:
    return f"{model.__module__}__{model.__name__}".replace(".", "__")


def get_model_name_map(
    unique_models: Set[TypeModelOrEnum],
) -> Dict[TypeModelOrEnum, str]:
    name_model_map = {}
    conflicting_names: Set[str] = set()
    for model in unique_models:
        if issubclass(model, BaseModel):
            model_name = model.__config__.title or model.__name__
        else:
            model_name = model.__name__
        model_name = re.sub(r"[^a-zA-Z0-9.\-_]", "_", model_name)
        if model_name in conflicting_names:
            model_name = get_long_model_name(model)
            name_model_map[model_name] = model
        elif model_name in name_model_map:
            conflicting_names.add(model_name)
            conflicting_model = name_model_map.pop(model_name)
            name_model_map[get_long_model_name(conflicting_model)] = (
                conflicting_model
            )
            name_model_map[get_long_model_name(model)] = model
        else:
            name_model_map[model_name] = model
    return {v: k for k, v in name_model_map.items()}


def get_model_definitions(
    *,
    models: Set[TypeModelOrEnum],
    model_name_map: Dict[TypeModelOrEnum, str],
) -> Dict[str, Any]:
    definitions: Dict[str, Dict] = {}
    for model in models:
        model_schema, model_definitions, _ = model_process_schema(
            model, model_name_map=model_name_map, ref_prefix=REF_PREFIX
        )
        definitions.update(model_definitions)
        model_name = model_name_map[model]
        definitions[model_name] = model_schema
    return definitions


def make_rpc_path(
    *,
    method: str,
    parameters: Mapping[str, inspect.Parameter],
    errors: List[Type[RestRpcError]],
    summary: str = "",
    description: str = "",
    deprecated: bool = False,
    tags: Optional[List[str]] = None,
    examples: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    camel_method = snake_to_camel(method)
    request_ref = f"{REF_PREFIX}{camel_method}Request"
    response_ref = f"{REF_PREFIX}{camel_method}Response"
    path_item = PathItem(
        post=Operation(
            tags=tags or [],
            summary=summary,
            operationId=method,
            description=description,
            deprecated=deprecated,
            requestBody=RequestBody(
                content={
                    "application/json": MediaType(
                        schema_=Reference(ref=request_ref), examples=None
                    )
                },
                required=True,
            ),
            responses={
                "200": Response(
                    description="Successful operation",
                    content={
                        "application/json": MediaType(
                            schema_=Reference(ref=response_ref), examples=None
                        ),
                    },
                ),
                "default": Response(
                    description="Failed operation",
                    content={
                        "application/json": MediaType(
                            schema_=Reference(ref=response_ref),
                            examples={},
                        ),
                    },
                ),
            },
        ),
    )
    for error in errors:
        path_item.post.responses["default"].content[  # type: ignore
            "application/json"
        ].examples[error.__name__] = Reference(
            ref=f"#/components/examples/{error.__name__}"
        )
    if examples:
        req_examples = dict()
        resp_examples = dict()
        for example in examples:
            index = examples.index(example)
            req_examples[f'{camel_method}{index}ExampleRequest'] = Reference(
                ref=f"#/components/examples/{camel_method}{index}ExampleResponse"
            )
            resp_examples[f'{camel_method}{index}ExampleResponse'] = Reference(
                ref=f"#/components/examples/{camel_method}{index}ExampleResponse"
            )
        path_item.post.requestBody.content[  # type: ignore
            "application/json"
        ].examples = req_examples
        path_item.post.responses["200"].content[  # type: ignore
            "application/json"
        ].examples = resp_examples
    return {
        f"/{method}": path_item,
    }


def make_dev_server(
    server: HttpServer, path: str, description: str = "dev"
) -> Server:
    schema = "https" if server.ssl_context else "http"
    host = (
        "localhost"
        if server.cfg.host == "0.0.0.0"  # nosec
        else server.cfg.host
    )
    port = "" if server.cfg.port in (80, 443) else server.cfg.port
    return Server(
        url=f"{schema}://{host}:{port}{path}",
        description=description,
    )
