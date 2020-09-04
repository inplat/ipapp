import inspect
import json
import warnings
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from aiohttp import web
from iprpc.executor import BaseError, InternalError, MethodExecutor
from pydantic import AnyUrl, BaseModel, Field

from ipapp.ctx import span
from ipapp.http.server import ServerHandler
from ipapp.misc import json_encode as default_json_encode
from ipapp.openapi.misc import (
    REF_PREFIX,
    get_errors_from_func,
    get_model_definitions,
    get_model_name_map,
    get_models_from_rpc_methods,
    get_summary_description_from_func,
    make_dev_server,
    make_rpc_path,
    snake_to_camel,
)
from ipapp.openapi.models import (
    Components,
    Contact,
    Example,
    ExternalDocumentation,
    Info,
    License,
    MediaType,
)
from ipapp.openapi.models import OpenAPI as OpenAPIModel
from ipapp.openapi.models import (
    Operation,
    PathItem,
    Reference,
    Response,
    Server,
    Tag,
)
from ipapp.openapi.templates import render_redoc_html, render_swagger_ui_html
from ipapp.rpc.const import SPAN_TAG_RPC_CODE, SPAN_TAG_RPC_METHOD

warnings.warn(
    "module is deprecated. Use ipapp.rpc.jsonrpc.http.server instead",
    DeprecationWarning,
    stacklevel=2,
)


class RpcHandlerConfig(BaseModel):
    path: str = Field("/", description="Путь RPC сервера")
    healthcheck_path: str = Field(
        "/health", description="Путь health check RPC сервера"
    )
    debug: bool = Field(
        False, description="Возвращать в RPC ошибках стектрейсы"
    )


class OpenApiRpcHandlerConfig(RpcHandlerConfig):
    title: str = Field(
        "Application API", description="Заголовок OpenAPI спецификации"
    )
    description: Optional[str] = Field(
        None,
        description="Описание OpenAPI спецификации",
        example="Application description",
    )
    terms_of_service: Optional[str] = Field(
        None,
        description="Ссылка на условия обслуживания",
        example="https://acme.inc/tos",
    )
    contact_name: Optional[str] = Field(
        None, description="Имя контакта", example="Ivan Ivanov"
    )
    contact_url: Optional[AnyUrl] = Field(
        None, description="URL контакта", example="https://acme.inc"
    )
    contact_email: Optional[str] = Field(
        None, description="Email контакта", example="ivan.ivanov@acme.inc"
    )
    license_name: Optional[str] = Field(
        None, description="Название лицензии", example="Apache License 2.0"
    )
    license_url: Optional[AnyUrl] = Field(
        None,
        description="Ссылка на лицензию",
        example="https://spdx.org/licenses/Apache-2.0.html",
    )
    version: str = Field("1.0.0", description="Версия API")
    servers: List[Server] = []
    external_docs_url: Optional[str] = Field(
        None,
        description="Ссылка на внешнюю документацию",
        example="https://acme.inc/docs",
    )
    external_docs_description: Optional[str] = Field(
        None, description="Описание внешней документации", example="Acme docs"
    )
    openapi_url: str = Field(
        "/openapi.json", description="Путь публикации OpenAPI спецификации"
    )
    openapi_prefix: str = Field(
        "",
        description="Префикс пути публикации OpenAPI спецификации",
        example="/api/v1",
    )
    openapi_schemas: List[str] = []
    docs_url: str = Field(
        "/docs", description="Путь публикации Swagger документации"
    )
    redoc_url: str = Field(
        "/redoc", description="Путь публикации ReDoc документации"
    )


class RpcHandler(ServerHandler):
    def __init__(
        self,
        api: object,
        cfg: RpcHandlerConfig,
        json_encode: Callable[[Any], str] = default_json_encode,
    ) -> None:
        self._cfg = cfg
        self._api = api
        self._rpc = MethodExecutor(api)
        self._json_encode = json_encode

    async def prepare(self) -> None:
        self._setup_healthcheck(self._cfg.healthcheck_path)
        self.server.add_route('POST', self._cfg.path, self.rpc_handler)

    def _err_resp(self, err: BaseError) -> dict:
        resp = {
            "code": err.code,
            "message": err.message,
            "details": str(err.parent) if err.parent is not None else None,
        }

        if self._cfg.debug:
            resp['trace'] = err.trace

        return resp

    async def rpc_handler(self, request: web.Request) -> web.Response:
        try:
            result = await self._rpc.call(
                await request.read(), request.charset
            )
            if result.method is not None:
                span.tag(SPAN_TAG_RPC_METHOD, result.method)

            if result.error is not None:
                span.error(result.error)
                if isinstance(result.error, InternalError):
                    self.app.log_err(result.error)
                resp = self._err_resp(result.error)
                if result.result is not None:
                    resp['result'] = result.result

            else:
                resp = {"code": 0, "message": 'OK', 'result': result.result}

            span.tag(SPAN_TAG_RPC_CODE, resp['code'])
            span.name = 'rpc::in (%s)' % result.method
            span.set_name4adapter(self.app.logger.ADAPTER_PROMETHEUS, 'rpc_in')

            body = self._json_encode(resp).encode()

            return web.Response(body=body, content_type='application/json')
        except Exception as err:
            span.error(err)
            self.app.log_err(err)
            return web.Response(
                body=self._json_encode(
                    self._err_resp(InternalError(parent=err))
                ).encode(),
                content_type='application/json',
            )


class OpenApiRpcHandler(RpcHandler):
    def __init__(
        self,
        api: object,
        cfg: OpenApiRpcHandlerConfig,
        json_encode: Callable[[Any], str] = default_json_encode,
    ) -> None:
        super().__init__(api=api, cfg=cfg, json_encode=json_encode)
        self._cfg: OpenApiRpcHandlerConfig = cfg  # for mypy
        self.openapi = OpenAPIModel(
            openapi="3.0.3",
            info=Info(
                title=self._cfg.title,
                description=self._cfg.description,
                termsOfService=self._cfg.terms_of_service,
                contact=Contact(
                    name=self._cfg.contact_name,
                    url=self._cfg.contact_url,
                    email=self._cfg.contact_email,
                ),
                version=self._cfg.version,
            ),
            tags=[
                Tag(
                    name=self.name,
                    description=inspect.getdoc(self._api) or "",
                )
            ],
            components=Components(examples={}),
            paths={
                self._cfg.healthcheck_path: PathItem(
                    get=Operation(
                        tags=[self.name],
                        summary="Health Check",
                        operationId="health",
                        description="",
                        responses={
                            "200": Response(
                                description="Successful operation",
                                content={
                                    "application/json": MediaType(
                                        schema_=Reference(
                                            ref=f"{REF_PREFIX}Health"
                                        ),
                                    ),
                                },
                            ),
                            "default": Response(description="Error"),
                        },
                    ),
                )
            },
        )

        if self._cfg.license_name:
            self.openapi.info.license = License(
                name=self._cfg.license_name,
                url=self._cfg.license_url,
            )

        if self._cfg.external_docs_url:
            self.openapi.externalDocs = ExternalDocumentation(
                url=self._cfg.external_docs_url,
                description=self._cfg.external_docs_description,
            )

    @property
    def name(self) -> str:
        return self._api.__class__.__name__

    @property
    def docs_url(self) -> str:
        return f"{self._cfg.path.rstrip('/')}{self._cfg.docs_url}"

    @property
    def redoc_url(self) -> str:
        return f"{self._cfg.path.rstrip('/')}{self._cfg.redoc_url}"

    @property
    def openapi_prefix(self) -> str:
        return self._cfg.openapi_prefix.rstrip("/") or self._cfg.path.rstrip(
            "/"
        )

    @property
    def openapi_url(self) -> str:
        return f"{self.openapi_prefix}{self._cfg.openapi_url}"

    def file_factory(
        self, filepath: str
    ) -> Callable[[web.Request], Coroutine[Any, Any, web.FileResponse]]:
        async def file_handler(request: web.Request) -> web.FileResponse:
            return web.FileResponse(filepath)

        return file_handler

    async def openapi_handler(self, request: web.Request) -> web.Response:
        return web.Response(
            body=json.dumps(
                self.openapi.dict(by_alias=True, exclude_none=True),
                indent=4,
                sort_keys=True,
            ),
            headers={"Content-Type": "application/json"},
        )

    async def docs_handler(self, request: web.Request) -> web.Response:
        return render_swagger_ui_html(
            openapi_url=self.openapi_url, title=self._cfg.title
        )

    async def redoc_handler(self, request: web.Request) -> web.Response:
        return render_redoc_html(
            openapi_url=self.openapi_url, title=self._cfg.title
        )

    async def openapi_prepare(self) -> None:
        models = get_models_from_rpc_methods(self._rpc._methods)
        model_name_map = get_model_name_map(models)
        definitions = get_model_definitions(
            models=models, model_name_map=model_name_map
        )

        for func in self._rpc._methods.values():
            sig = inspect.signature(func)
            errors = get_errors_from_func(func)
            summary, description = get_summary_description_from_func(func)

            method_name = getattr(func, "__rpc_name__", func.__name__)
            deprecated = getattr(func, "__rpc_deprecated__", False)
            request_ref = getattr(func, "__rpc_request_ref__", None)
            response_ref = getattr(func, "__rpc_response_ref__", None)

            camel_method_name = snake_to_camel(method_name)
            request_model_name = f"{camel_method_name}Request"
            response_model_name = f"{camel_method_name}Response"

            if request_ref:
                definitions.pop(f"{request_model_name}Params", None)
                definitions[request_model_name]["properties"]["params"] = {
                    "$ref": request_ref,
                }

            if response_ref:
                definitions.pop(f"{response_model_name}Result", None)
                definitions[response_model_name]["properties"]["result"] = {
                    "$ref": response_ref,
                }

            path = make_rpc_path(
                method=method_name,
                parameters=sig.parameters,
                errors=errors,
                summary=summary,
                description=description,
                deprecated=deprecated,
                tags=[self.name],
            )
            self.openapi.paths.update(path)

            for error in errors:
                if (
                    self.openapi.components is not None
                    and self.openapi.components.examples is not None
                ):
                    self.openapi.components.examples[error.__name__] = Example(
                        value={"code": error.code, "message": error.message}
                    )

        if self.openapi.components and definitions:
            self.openapi.components.schemas = {
                x: definitions[x] for x in sorted(definitions)
            }
        self.server.web_app.router.add_get(
            self.openapi_url,
            self.openapi_handler,
        )
        self.server.web_app.router.add_get(
            self.docs_url,
            self.docs_handler,
        )

        self.server.web_app.router.add_get(
            self.redoc_url,
            self.redoc_handler,
        )

        for schema in self._cfg.openapi_schemas:
            self.server.web_app.router.add_get(
                f"{self.openapi_prefix}/{Path(schema).name}",
                self.file_factory(schema),
            )

        self.openapi.servers = [make_dev_server(self.server, self._cfg.path)]
        self.openapi.servers.extend(self._cfg.servers)

    async def prepare(self) -> None:
        await super().prepare()
        try:
            await self.openapi_prepare()
        except Exception as exc:
            self.app.log_err(f"Cannot initialize openapi: {exc}")


def method(
    *,
    name: Optional[str] = None,
    errors: Optional[List[BaseError]] = None,
    deprecated: Optional[bool] = False,
    summary: str = "",
    description: str = "",
    request_model: Optional[Any] = None,
    response_model: Optional[Any] = None,
    request_ref: Optional[str] = None,
    response_ref: Optional[str] = None,
    validators: Optional[Dict[str, dict]] = None,
) -> Callable:
    def decorator(func: Callable) -> Callable:
        setattr(func, "__rpc_name__", name or func.__name__)
        setattr(func, "__rpc_errors__", errors or [])
        setattr(func, "__rpc_deprecated__", deprecated)
        setattr(func, "__rpc_summary__", summary)
        setattr(func, "__rpc_description__", description)
        setattr(func, "__rpc_request_model__", request_model)
        setattr(func, "__rpc_response_model__", response_model)
        setattr(func, "__rpc_request_ref__", request_ref)
        setattr(func, "__rpc_response_ref__", response_ref)

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


__all__ = [
    'RpcHandlerConfig',
    'OpenApiRpcHandlerConfig',
    'RpcHandler',
    'OpenApiRpcHandler',
    'method',
]
