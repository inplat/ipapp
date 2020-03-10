import inspect
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
from pydantic import BaseConfig, BaseModel, create_model
from pydantic.schema import model_process_schema

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


def get_models_from_rpc_methods(
    methods: Dict[str, Callable]
) -> Set[Type[BaseModel]]:
    models: Set[Type[BaseModel]] = set()

    for method, func in methods.items():
        sig = inspect.signature(func)

        method = getattr(func, "__rpc_name__", method)
        request_model = getattr(func, "__rpc_request_model__", None)
        response_model = getattr(func, "__rpc_response_model__", None)

        camel_method = snake_to_camel(method)

        for param in sig.parameters.values():
            origin_name = getattr(param.annotation, "__origin__", None)
            origin_args = getattr(param.annotation, "__args__", [])
            if param.kind is param.VAR_KEYWORD:
                pass
            elif param.kind is param.VAR_POSITIONAL:
                pass
            elif isinstance(param.annotation, type(BaseModel)):
                models.add(param.annotation)
            elif origin_name and origin_name._name == "Union":
                for arg in origin_args:
                    if isinstance(arg, type(BaseModel)):
                        models.add(arg)

        class RequestParamsConfig(BaseConfig):
            arbitrary_types_allowed = True

        RequestParamsModel = create_model(
            f"{camel_method}RequestParams",
            __config__=RequestParamsConfig,
            **{  # type: ignore
                k: (v.annotation, ... if v.default is v.empty else v.default)
                for k, v in sig.parameters.items()
                if v.kind is not v.VAR_KEYWORD
                and param.kind is not param.VAR_POSITIONAL
            },
        )

        class RequestConfig(BaseConfig):
            arbitrary_types_allowed = True
            schema_extra = {"examples": [{"method": method}]}

        RequestModel = create_model(
            f"{camel_method}Request",
            __config__=RequestConfig,
            method=(str, ...),
            params=(request_model or RequestParamsModel, ...),
        )

        class ResponseConfig(BaseConfig):
            arbitrary_types_allowed = True
            schema_extra = {"examples": [{"code": 0, "message": "OK"}]}

        resp = dict(code=(int, ...), message=(str, ...))
        if response_model or sig.return_annotation is not None:
            resp["result"] = (response_model or sig.return_annotation, None)  # type: ignore

        ResponseModel = create_model(
            f"{camel_method}Response", __config__=ResponseConfig, **resp,  # type: ignore
        )

        models.update({RequestParamsModel, RequestModel, ResponseModel})

        if isinstance(sig.return_annotation, type(BaseModel)):
            models.add(sig.return_annotation)

    return models


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

    for model_name, model_definition in definitions.items():
        for example in model_definition.pop("examples", []):
            for key, value in example.items():
                if key in definitions[model_name]["properties"]:
                    definitions[model_name]["properties"][key][
                        "example"
                    ] = value

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
        )
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
