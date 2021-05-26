import asyncio
import inspect
import json
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

import yaml
from aiohttp import web
from multidict import CIMultiDict
from pydantic import AnyUrl, BaseModel, Field

from ipapp.http.server import ServerHandler as _ServerHandler
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
from ipapp.rpc.jsonrpc.openrpc.models import ExternalDocs
from ipapp.rpc.main import RpcRegistry
from ipapp.rpc.restrpc.main import RestRpcExecutor
from ipapp.rpc.restrpc.openapi.misc import (  # make_dev_server,
    REF_PREFIX,
    get_errors_from_func,
    get_methods,
    get_model_definitions,
    get_model_name_map,
    get_models_from_rpc_methods,
    get_summary_description_from_func,
    make_rpc_path,
    snake_to_camel,
)


@dataclass
class _SetCookie:
    name: str
    value: str
    expires: Optional[str] = None
    domain: Optional[str] = None
    max_age: Optional[Union[int, str]] = None
    path: str = "/"
    secure: Optional[bool] = None
    httponly: Optional[bool] = None
    version: Optional[str] = None
    samesite: Optional[str] = None


@dataclass
class _DelCookie:
    name: str
    domain: Optional[str] = None
    path: str = "/"


response_set_headers: ContextVar[CIMultiDict] = ContextVar(
    "response_set_headers", default=CIMultiDict()
)


response_set_cookies: ContextVar[List[_SetCookie]] = ContextVar(
    "response_set_cookies", default=[]
)
response_del_cookies: ContextVar[List[_DelCookie]] = ContextVar(
    "response_del_cookies", default=[]
)


class RestRpcHttpHandlerConfig(BaseModel):
    path: str = Field("/", description="Путь Rest-RPC сервера")
    healthcheck_path: str = Field(
        "/health", description="Путь health check RPC сервера"
    )
    shield: bool = False
    cors_enabled: bool = True
    cors_origin: str = ""
    openapi_json_url: str = Field(
        "/openapi.json", description="Путь публикации OpenAPI спецификации"
    )
    openapi_yaml_url: str = Field(
        "/openapi.yaml", description="Путь публикации OpenAPI спецификации"
    )
    swager_ui_url: str = Field(
        "/swagger", description="Путь публикации Swagger документации"
    )
    redoc_url: str = Field(
        "/redoc", description="Путь публикации ReDoc документации"
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
    openapi_schemas: List[str] = []


class RestRpcHttpHandler(_ServerHandler):
    _restrpc: RestRpcExecutor

    def __init__(
        self,
        registry: Union[RpcRegistry, object],
        cfg: RestRpcHttpHandlerConfig,
        servers: Optional[List[Server]] = None,
        external_docs: Optional[ExternalDocs] = None,
    ) -> None:
        self._cfg = cfg
        self._registry = registry
        self._servers = servers
        self._external_docs = external_docs
        self._openapi = OpenApiRestRpc(
            registry=registry,
            cfg=cfg,
            servers=servers,
            external_docs=external_docs,
        )

    async def prepare(self) -> None:
        self._methods = get_methods(self._registry)
        self._restrpc = RestRpcExecutor(
            self._registry,
            self.app,
            servers=self._servers,
            external_docs=self._external_docs,
        )
        if self._cfg.healthcheck_path:
            self._setup_healthcheck(self._cfg.healthcheck_path)
        self.server.add_options(self._cfg.path, self.rpc_options_handler)
        try:
            self.openapi_prepare()
        except Exception as exc:
            self.app.log_err(f"Cannot initialize openapi: {exc}")
        path = self._cfg.path
        if path.endswith("/"):
            path = path[:-1]
        for method_name in self._methods.keys():
            self.server.add_post(f"{path}/{method_name}/", self.rpc_handler)
            self.server.add_options(
                f"{path}/{method_name}/", self.rpc_options_handler
            )
            self.server.add_post(f"{path}/{method_name}", self.rpc_handler)
            self.server.add_options(
                f"{path}/{method_name}", self.rpc_options_handler
            )
        await self._restrpc.start_scheduler()

    async def stop(self) -> None:
        await self._restrpc.stop_scheduler()

    def _get_cors_headers(
        self,
    ) -> Dict[str, str]:
        if not self._cfg.cors_enabled:
            return {}
        return {
            "Access-Control-Allow-Origin": self._cfg.cors_origin,
            "Access-Control-Allow-Methods": "OPTIONS, POST",
            "Access-Control-Allow-Headers": "*",
        }

    async def rpc_options_handler(self, request: web.Request) -> web.Response:
        return web.HTTPOk(headers=self._get_cors_headers())

    async def rpc_handler(self, request: web.Request) -> web.Response:
        if self._cfg.shield:
            return await asyncio.shield(self._handle(request))
        return await self._handle(request)

    async def _handle(self, request: web.Request) -> web.Response:
        req_body = await request.read()
        method_name = request.path
        if method_name.startswith(self._cfg.path):
            method_name = method_name[len(self._cfg.path) :]
        if method_name.startswith("/"):
            method_name = method_name[1:]
        if method_name.endswith("/"):
            method_name = method_name[:-1]
        if not method_name:
            return web.HTTPNotFound()
        response_set_headers_token = response_set_headers.set(CIMultiDict())
        response_set_cookies_token = response_set_cookies.set([])
        response_del_cookies_token = response_del_cookies.set([])
        resp_body, resp_status_code = await self._restrpc.exec(
            request=req_body, method_name=method_name
        )
        resp = web.Response(
            status=resp_status_code,
            body=resp_body,
            content_type="application/json",
            headers=self._get_cors_headers(),
        )

        set_headers = response_set_headers.get()
        resp.headers.extend(set_headers)

        set_cookies = response_set_cookies.get()
        for sc in set_cookies:
            resp.set_cookie(
                sc.name,
                sc.value,
                expires=sc.expires,
                domain=sc.domain,
                max_age=sc.max_age,
                path=sc.path,
                secure=sc.secure,
                httponly=sc.httponly,
                version=sc.version,
                samesite=sc.samesite,
            )

        del_cookies = response_del_cookies.get()
        for dc in del_cookies:
            resp.del_cookie(dc.name, domain=dc.domain, path=dc.path)

        response_set_headers.reset(response_set_headers_token)
        response_set_cookies.reset(response_set_cookies_token)
        response_del_cookies.reset(response_del_cookies_token)
        return resp

    async def error_handler(
        self, request: web.Request, err: Exception
    ) -> web.Response:
        if isinstance(err, web.Response):
            return err
        return web.HTTPInternalServerError()

    def openapi_routers_prepare(self) -> None:
        for schema in self._cfg.openapi_schemas:
            self.server.web_app.router.add_get(
                f"{self._cfg.path.rstrip('/')}/{Path(schema).name}",
                self.file_factory(schema),
            )
        self.server.web_app.router.add_get(
            self._cfg.openapi_json_url, self.openapi_json_handler
        )
        self.server.web_app.router.add_get(
            self._cfg.openapi_yaml_url, self.openapi_yaml_handler
        )
        self.server.add_get(self._cfg.path, self.rpc_doc_redirect_handler)
        self.server.web_app.router.add_get(
            self._openapi.swager_ui_url, self.swagger_ui_handler
        )
        self.server.web_app.router.add_get(
            self._openapi.redoc_url, self.redoc_handler
        )

    def openapi_prepare(self) -> None:
        self._openapi.openapi_generate()
        self.openapi_routers_prepare()

    async def openapi_json_handler(self, request: web.Request) -> web.Response:
        return web.Response(
            body=json.dumps(
                self._openapi.openapi.dict(by_alias=True, exclude_none=True),
                indent=4,
                sort_keys=True,
            ),
            content_type="application/json",
            headers=self._get_cors_headers(),
        )

    async def openapi_yaml_handler(self, request: web.Request) -> web.Response:
        return web.Response(
            body=yaml.dump(
                self._openapi.openapi.dict(by_alias=True, exclude_none=True),
                default_flow_style=False,
            ),
            content_type="application/yaml",
            headers=self._get_cors_headers(),
        )

    async def rpc_doc_redirect_handler(
        self, request: web.Request
    ) -> web.Response:
        return web.HTTPTemporaryRedirect(
            location=self._openapi.swager_ui_url,
            headers=self._get_cors_headers(),
        )

    async def swagger_ui_handler(self, request: web.Request) -> web.Response:
        return render_swagger_ui_html(
            openapi_url=self._cfg.openapi_json_url, title=self._openapi.title
        )

    async def redoc_handler(self, request: web.Request) -> web.Response:
        return render_redoc_html(
            openapi_url=self._cfg.openapi_json_url, title=self._openapi.title
        )

    def file_factory(
        self, filepath: str
    ) -> Callable[[web.Request], Coroutine[Any, Any, web.FileResponse]]:
        async def file_handler(request: web.Request) -> web.FileResponse:
            return web.FileResponse(filepath)

        return file_handler


def set_reponse_header(name: str, value: str) -> CIMultiDict:
    headers = response_set_headers.get()
    headers[name] = value
    return headers


def set_response_cookie(
    name: str,
    value: str,
    *,
    expires: Optional[str] = None,
    domain: Optional[str] = None,
    max_age: Optional[Union[int, str]] = None,
    path: str = "/",
    secure: Optional[bool] = None,
    httponly: Optional[bool] = None,
    version: Optional[str] = None,
    samesite: Optional[str] = None,
) -> List[_SetCookie]:
    scl = response_set_cookies.get()
    scl.append(
        _SetCookie(
            name,
            value,
            expires,
            domain,
            max_age,
            path,
            secure,
            httponly,
            version,
            samesite,
        )
    )
    return scl


def del_response_cookie(
    name: str, domain: Optional[str] = None, path: str = "/"
) -> List[_DelCookie]:
    dcl = response_del_cookies.get()
    dcl.append(_DelCookie(name, domain, path))
    return dcl


class OpenApiRestRpc:
    def __init__(
        self,
        registry: Union[RpcRegistry, object],
        cfg: RestRpcHttpHandlerConfig,
        servers: Optional[List[Server]] = None,
        external_docs: Optional[ExternalDocs] = None,
    ) -> None:
        self._cfg = cfg
        self._registry = registry
        self._servers = servers
        self._external_docs = external_docs
        self.openapi = self.openapi_model

        if self._cfg.license_name:
            self.openapi.info.license = License(
                name=self._cfg.license_name, url=self._cfg.license_url
            )

        if self._external_docs:
            self.openapi.externalDocs = ExternalDocumentation(
                url=self._external_docs.url,
                description=self._external_docs.description,
            )

    @property
    def openapi_model(self) -> OpenAPIModel:
        return OpenAPIModel(
            openapi="3.0.3",
            info=Info(
                title=self.title,
                description=self.description,
                termsOfService=self._cfg.terms_of_service,
                contact=Contact(
                    name=self._cfg.contact_name,
                    url=self._cfg.contact_url,
                    email=self._cfg.contact_email,
                ),
                version=self.version,
            ),
            tags=[
                Tag(
                    name=self.title,
                    description=self.description,
                )
            ],
            components=Components(examples={}),
            paths={
                self._cfg.healthcheck_path: PathItem(
                    get=Operation(
                        tags=["Health Check"],
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

    @property
    def title(self) -> str:
        return (
            self._registry.title
            if isinstance(self._registry, RpcRegistry) and self._registry.title
            else "Api"
        )

    @property
    def version(self) -> str:
        return (
            self._registry.version
            if isinstance(self._registry, RpcRegistry)
            and self._registry.version
            else "dev"
        )

    @property
    def description(self) -> str:
        return (
            self._registry.description
            if isinstance(self._registry, RpcRegistry)
            and self._registry.description
            else ""
        )

    @property
    def redoc_url(self) -> str:
        return f"{self._cfg.path.rstrip('/')}{self._cfg.redoc_url}"

    @property
    def swager_ui_url(self) -> str:
        return f"{self._cfg.path.rstrip('/')}{self._cfg.swager_ui_url}"

    def openapi_generate(self) -> None:
        methods = get_methods(self._registry)
        models = get_models_from_rpc_methods(methods)
        model_name_map = get_model_name_map(models)
        definitions = get_model_definitions(
            models=models, model_name_map=model_name_map
        )
        for func in methods.values():
            sig = inspect.signature(func)
            errors = get_errors_from_func(func)
            summary, description = get_summary_description_from_func(func)
            method_name = getattr(func, "__rpc_name__", func.__name__)
            deprecated = getattr(func, "__rpc_deprecated__", False)
            request_ref = getattr(func, "__rpc_request_ref__", None)
            response_ref = getattr(func, "__rpc_response_ref__", None)
            examples = getattr(func, "__rpc_examples__", [])
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
                tags=[self.title],
                examples=examples,
            )
            self.openapi.paths.update(path)
            for error in errors:
                if (
                    self.openapi.components is not None
                    and self.openapi.components.examples is not None
                ):
                    self.openapi.components.examples[
                        error.__name__  # type: ignore
                    ] = Example(
                        value={
                            "error": {
                                "code": error.code,
                                "message": error.message,
                            }
                        }
                    )
            if examples:
                for example in examples:
                    index = examples.index(example)
                    if (
                        self.openapi.components is not None
                        and self.openapi.components.examples is not None
                    ):
                        self.openapi.components.examples[
                            f'{camel_method_name}{index}ExampleRequest'
                        ] = Example(
                            value={
                                value["name"]: value["value"]
                                for value in example["params"]
                            }
                        )
                        self.openapi.components.examples[
                            f'{camel_method_name}{index}ExampleResponse'
                        ] = Example(
                            value={
                                value["name"]: value["value"]
                                for value in example["result"]
                            }
                        )
        if self.openapi.components and definitions:
            self.openapi.components.schemas = {
                x: definitions[x] for x in sorted(definitions)
            }
