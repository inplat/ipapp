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
)

from iprpc import (
    BaseError,
    DeserializeError,
    InternalError,
    InvalidArguments,
    InvalidRequest,
    MethodNotFound,
)
from pydantic import BaseModel, Field, create_model
from pydantic.schema import get_flat_models_from_models, model_process_schema

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

REF_PREFIX = "#/components/schemas/"


def snake_to_camel(value: str) -> str:
    return value.title().replace("_", "")


def get_errors_from_func(func: Callable) -> List[BaseError]:
    errors = getattr(func, "__rpc_errors__", [])
    errors.extend(
        [
            DeserializeError,
            InvalidRequest,
            MethodNotFound,
            InvalidArguments,
            InternalError,
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


def get_models_from_rpc_methods(
    methods: Dict[str, Callable]
) -> Set[Type[BaseModel]]:
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
        request_params_model_name = f"{camel_method_name}RequestParams"
        response_model_name = f"{camel_method_name}Response"
        response_result_model_name = f"{camel_method_name}ResponseResult"

        RequestParamsModel = request_params_model or create_model(
            request_params_model_name, **get_field_definitions(sig.parameters),
        )

        if getattr(RequestParamsModel, "__name__", "") == request_model_name:
            fix_model_name(RequestParamsModel, request_params_model_name)

        RequestModel = create_model(
            request_model_name,
            method=(str, Field(..., example=method_name)),
            params=(RequestParamsModel, ...),
        )

        response: Dict[str, Any] = dict(
            code=(int, Field(..., example=0)),
            message=(str, Field(..., example="OK")),
        )

        ResponseResultModel = response_result_model or sig.return_annotation
        if ResponseResultModel is not None:
            if (
                getattr(ResponseResultModel, "__name__", "")
                == response_model_name
            ):
                fix_model_name(ResponseResultModel, response_result_model_name)

            response["result"] = (ResponseResultModel, None)

        ResponseModel = create_model(response_model_name, **response)

        clean_models.extend([RequestParamsModel, RequestModel, ResponseModel])

    flat_models = get_flat_models_from_models(clean_models)

    return flat_models


def fix_model_name(model: Type[BaseModel], name: str) -> None:
    if isinstance(model, type(BaseModel)):
        setattr(model.__config__, "title", name)
    else:
        # TODO: warning
        setattr(model, "__name__", name)


def get_long_model_name(model: Type[BaseModel]) -> str:
    return f"{model.__module__}__{model.__name__}".replace(".", "__")


def get_model_name_map(
    unique_models: Set[Type[BaseModel]],
) -> Dict[Type[BaseModel], str]:
    name_model_map = {}
    conflicting_names: Set[str] = set()
    for model in unique_models:
        model_name = model.__config__.title or model.__name__
        model_name = re.sub(r"[^a-zA-Z0-9.\-_]", "_", model_name)
        if model_name in conflicting_names:
            model_name = get_long_model_name(model)
            name_model_map[model_name] = model
        elif model_name in name_model_map:
            conflicting_names.add(model_name)
            conflicting_model = name_model_map.pop(model_name)
            name_model_map[
                get_long_model_name(conflicting_model)
            ] = conflicting_model
            name_model_map[get_long_model_name(model)] = model
        else:
            name_model_map[model_name] = model
    return {v: k for k, v in name_model_map.items()}


def get_model_definitions(
    *,
    models: Set[Type[BaseModel]],
    model_name_map: Dict[Type[BaseModel], str],
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
    errors: List[BaseError],
    summary: str = "",
    description: str = "",
    deprecated: bool = False,
    tags: Optional[List[str]] = None,
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
                        schema_=Reference(ref=request_ref)
                    )
                },
                required=True,
            ),
            responses={
                "200": Response(
                    description="Successful operation",
                    content={
                        "application/json": MediaType(
                            schema_=Reference(ref=response_ref),
                        ),
                    },
                ),
                "default": Response(
                    description="Failed operation",
                    content={
                        "application/json": MediaType(
                            schema_=Reference(ref=response_ref), examples={},
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

    return {
        f"/#{method}": path_item,
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
        url=f"{schema}://{host}:{port}{path}", description=description,
    )
