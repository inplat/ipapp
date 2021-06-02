"""Rest RPC Protocol implementation."""
import asyncio
import collections
import json
import traceback
from typing import (
    Any,
    Awaitable,
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

import aiojobs
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic.json import ENCODERS_BY_TYPE, pydantic_encoder
from pydantic.utils import smart_deepcopy
from tinyrpc import (
    InvalidParamsError,
    InvalidReplyError,
    InvalidRequestError,
    MethodNotFoundError,
    RPCProtocol,
    RPCRequest,
    RPCResponse,
)
from tinyrpc.protocols import jsonrpc as rpc

from ipapp import BaseApplication
from ipapp.ctx import app, span
from ipapp.logger import Span
from ipapp.misc import from_bytes
from ipapp.openapi.models import Server
from ipapp.rpc.const import SPAN_TAG_RPC_CODE, SPAN_TAG_RPC_METHOD
from ipapp.rpc.error import InvalidArguments as _InvalidArguments
from ipapp.rpc.error import MethodNotFound as _MethodNotFound
from ipapp.rpc.jsonrpc.openrpc.models import ExternalDocs
from ipapp.rpc.main import Executor as _Executor
from ipapp.rpc.main import RpcRegistry
from ipapp.rpc.restrpc.error import (
    RestRpcError,
    RestRpcErrorResponse,
    RestRpcInvalidParamsError,
    RestRpcInvalidRequestError,
    RestRpcMethodNotFoundError,
    RestRpcParseError,
    RestRpcServerError,
)

SPAN_TAG_RESTRPC_METHOD = 'rpc.method'
SPAN_TAG_RESTRPC_CODE = 'rpc.code'


class RestRpcSuccessResponse(RPCResponse):
    def _to_dict(self) -> Dict[str, Any]:
        if not isinstance(self.result, Dict) or len(self.result) == 0:
            raise RestRpcParseError(data='Wrong reply')
        return self.result

    def serialize(self) -> bytes:
        return json.dumps(self._to_dict()).encode()


def _get_code_message_and_data(
    error: Union[Exception, str]
) -> Tuple[int, str, Any]:
    if not isinstance(error, (Exception, str)):
        raise NotImplementedError
    data = None
    if isinstance(error, Exception):
        if hasattr(error, 'code'):
            code = error.code  # type: ignore
            msg = error.message  # type: ignore
            try:
                data = error.data  # type: ignore
            except AttributeError:
                pass
        elif isinstance(error, InvalidRequestError):
            code = RestRpcInvalidRequestError.code
            msg = RestRpcInvalidRequestError.message
        elif isinstance(error, (MethodNotFoundError, _MethodNotFound)):
            code = RestRpcMethodNotFoundError.code
            msg = RestRpcMethodNotFoundError.message
        elif isinstance(error, (InvalidParamsError, _InvalidArguments)):
            code = RestRpcInvalidParamsError.code
            msg = RestRpcInvalidParamsError.message
        else:
            code = RestRpcServerError.code
            if len(error.args) == 2:
                msg = str(error.args[0])
                data = error.args[1]
            else:
                msg = str(error)
    else:
        code = 500
        msg = error
    return code, msg, data


class RestRpcRequest(RPCRequest):
    """Defines a Rest RPC request."""

    def __init__(self) -> None:
        super().__init__()
        self.one_way = False
        self.method
        self.kwargs: Any = {}

    def error_respond(
        self, error: Union[Exception, str]
    ) -> Optional['RestRpcErrorResponse']:
        response = RestRpcErrorResponse()
        code, msg, data = _get_code_message_and_data(error)
        response.error = msg
        response._code = code
        if data:
            response.data = data
        return response

    def respond(self, result: Any) -> Optional['RestRpcSuccessResponse']:
        if self.one_way:
            return None
        response = RestRpcSuccessResponse()
        response.result = result
        return response

    def _to_dict(self) -> Dict[str, Any]:
        jdata = dict()
        if self.kwargs:
            jdata = self.kwargs
        if not self.kwargs:
            raise RestRpcParseError(data='Wrong response, params required')
        return jdata

    def serialize(self) -> bytes:
        return json.dumps(self._to_dict()).encode()


class RestRpcProtocol(RPCProtocol):
    """RestRpc protocol implementation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super(RestRpcProtocol, self).__init__(*args, **kwargs)

    def request_factory(self) -> 'RestRpcRequest':
        return RestRpcRequest()

    def create_request(
        self,
        method: str,
        kwargs: Dict[str, Any] = None,
        one_way: bool = False,
    ) -> 'RestRpcRequest':
        request = self.request_factory()
        request.one_way = one_way
        request.method = method
        request.kwargs = kwargs
        return request

    def parse_reply(
        self, data: bytes
    ) -> Union['RestRpcSuccessResponse', 'RestRpcErrorResponse']:
        if isinstance(data, bytes):
            data = data.decode()  # type: ignore
        try:
            rep = json.loads(data)
        except Exception as e:
            raise InvalidReplyError(e)
        if isinstance(rep, dict) and 'error' in rep:
            response = RestRpcErrorResponse()
            error = rep['error']
            response.error = error["message"]
            response._code = error["code"]
            if "data" in error:
                response.data = error["data"]
        else:
            response = RestRpcSuccessResponse()
            response.result = rep
        return response

    def parse_request(self, data: bytes, method_name: str) -> 'RestRpcRequest':
        if isinstance(data, bytes):
            data = data.decode()  # type: ignore
        try:
            req = json.loads(data)
        except Exception:
            raise RestRpcInvalidRequestError()
        return self._parse_subrequest(req, method_name)

    def _parse_subrequest(
        self, req: Any, method_name: str
    ) -> 'RestRpcRequest':
        if not isinstance(req, dict):
            raise RestRpcInvalidRequestError()
        if len(req) == 0:
            raise RestRpcInvalidParamsError(
                data='Missing required params in request'
            )
        request = self.request_factory()
        request.method = method_name
        request.kwargs = req
        return request

    def raise_error(
        self, error: Union['RestRpcErrorResponse', Dict[str, Any]]
    ) -> 'RestRpcError':
        exc = RestRpcError(error)  # type: ignore
        if not self.raises_errors:
            return exc
        raise exc

    def _caller(self, method: Callable, kwargs: Dict[str, Any]) -> Any:
        # Custom dispatcher called by RPCDispatcher._dispatch().
        # Override this when you need to call the method with additional parameters for example.
        return method(**kwargs)


class RestRpcExecutor:
    def __init__(
        self,
        registry: Union[RpcRegistry, object],
        app: BaseApplication,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        scheduler_kwargs: Optional[Dict[str, Any]] = None,
        servers: Optional[List[Server]] = None,
        external_docs: Optional[ExternalDocs] = None,
    ) -> None:
        self._registry = registry
        self._app = app
        self._discover_result: Optional[Dict[str, Any]] = None
        self._ex = _Executor(registry)
        self._loop = loop
        self._protocol = RestRpcProtocol()
        self._scheduler: Optional[aiojobs.Scheduler] = None
        self._scheduler_kwargs = scheduler_kwargs or {}
        self._servers: Optional[List[Server]] = servers
        self._external_docs: Optional[ExternalDocs] = external_docs

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

    async def start_scheduler(self) -> None:
        self._scheduler = await aiojobs.create_scheduler(
            **self._scheduler_kwargs
        )

    async def stop_scheduler(self) -> None:
        if self._scheduler is None:
            return
        await self._scheduler.close()
        self._scheduler = None

    async def exec(
        self, request: bytes, method_name: str
    ) -> Tuple[bytes, int]:
        status_code = 200
        try:
            req = self._parse_request(request, method_name)
        except RestRpcError as e:
            err_resp = e.error_respond()
            code = int(err_resp._code)
            status_code = code if code in range(400, 600) else status_code
            if hasattr(err_resp, 'data'):
                err_resp.data = self.cast2dump(err_resp.data)
            return err_resp.serialize(), status_code
        resp: Optional[rpc.RPCResponse]
        if not isinstance(req, RestRpcRequest):  # pragma: no cover
            raise NotImplementedError
        resp = await self._exec_single(req)
        if isinstance(resp, RestRpcErrorResponse):
            code = int(resp._code)
            status_code = code if code in range(400, 600) else status_code
            return resp.serialize(), status_code
        if resp is None:
            raise RestRpcParseError(data='Wrong response, kwargs required')
        try:
            return resp.serialize(), status_code
        except RestRpcError as e:
            err_resp = e.error_respond()
            code = int(err_resp._code)
            status_code = code if code in range(400, 600) else status_code
            if hasattr(err_resp, 'data'):
                err_resp.data = self.cast2dump(err_resp.data)
            return err_resp.serialize(), status_code

    def _parse_request(
        self, request: bytes, method_name: str
    ) -> 'RestRpcRequest':
        if span:
            span.set_name4adapter(
                self._app.logger.ADAPTER_PROMETHEUS, 'rpc_in'
            )
        try:
            return self._protocol.parse_request(request, method_name)
        except (RestRpcInvalidRequestError, RestRpcParseError) as err:
            self._set_span_method(None)
            self._set_span_err(err)
            raise

    async def _exec_single(
        self, req: RestRpcRequest
    ) -> Optional[rpc.RPCResponse]:
        try:
            res = await self._exec(req.method, req.kwargs, req.one_way)
            return req.respond(self.cast2dump(res))
        except Exception as e:
            if not hasattr(e, 'code'):
                app.log_err(e)
            return req.error_respond(e)
        finally:
            if req.one_way:
                return None

    async def _exec(
        self,
        method: str,
        kwargs: Dict[str, Any],
        is_one_way: bool,
    ) -> Any:
        return await self._exec_method(method, kwargs, is_one_way)

    async def _exec_method(
        self,
        method: str,
        kwargs: Dict[str, Any],
        is_one_way: bool,
    ) -> Any:
        self._set_span_method(method)
        try:
            if is_one_way and self._scheduler is not None:
                await self._scheduler.spawn(
                    self._exec_in_executor(method, kwargs)
                )
                return None
            else:
                return await self._exec_in_executor(method, kwargs)
        except Exception as err:
            self._set_span_err(err)
            raise self._map_exc(err)

    async def _exec_in_executor(
        self, method: str, kwargs: Dict[str, Any]
    ) -> Any:
        try:
            return await self._ex.exec(name=method, args=None, kwargs=kwargs)
        except Exception as err:
            if app and span and hasattr(err, 'code'):
                span.tag(SPAN_TAG_RPC_CODE, getattr(err, 'code'))
            raise

    @staticmethod
    def _map_exc(ex: Exception) -> Exception:
        if type(ex) is _MethodNotFound:
            return RestRpcMethodNotFoundError()
        if type(ex) is _InvalidArguments:
            return RestRpcInvalidParamsError(data={'info': str(ex)})
        return ex

    def _set_span_method(self, method: Optional[str]) -> None:
        if not span:
            return
        if method is not None:
            span.name = 'rpc::in (%s)' % method
            span.tag(SPAN_TAG_RESTRPC_METHOD, method)
        else:
            span.name = 'rpc::in::error'

    def _set_span_err(self, err: Exception) -> None:
        if not span:
            return
        span.tag('error', 'true')
        span.annotate(span.ANN_TRACEBACK, traceback.format_exc())
        if hasattr(err, 'code'):
            span.tag(
                SPAN_TAG_RESTRPC_CODE,
                str(err.code),  # type: ignore
            )
        else:
            code, _, _ = _get_code_message_and_data(err)
            span.tag(SPAN_TAG_RESTRPC_CODE, str(code))

    @classmethod
    def cast2dump(cls, result: Any) -> Any:
        if result is None:
            return None
        if isinstance(result, FieldInfo):
            field_info = result
            result = (
                smart_deepcopy(field_info.default)
                if field_info.default_factory is None
                else field_info.default_factory()
            )
            if result == Ellipsis:
                raise _InvalidArguments
            return result
        if isinstance(result, BaseModel):
            return cls.cast2dump(result.dict())
        if isinstance(result, bytes):
            return from_bytes(result)
        if isinstance(result, (int, float, str, bool, type(None))):
            return result
        if isinstance(result, Mapping):
            res_dict = {}
            for key, value in result.items():
                try:
                    res_dict[key] = cls.cast2dump(value)
                except _InvalidArguments:
                    raise _InvalidArguments(
                        Exception(f'Missing required argument: {key}')
                    )
            return res_dict
        if isinstance(result, Iterable):
            res_list = []
            for item in result:
                res_list.append(cls.cast2dump(item))
            return res_list
        for enc_type, enc_func in ENCODERS_BY_TYPE.items():
            if isinstance(result, enc_type):
                return enc_func(result)
        return pydantic_encoder(result)


class RestRpcCall:
    def __init__(
        self,
        client: 'RestRpcClient',
        method: str,
        params: Union[Iterable[Any], Mapping[str, Any], None] = None,
        one_way: bool = False,
        timeout: Optional[float] = None,
        model: Optional[Type[BaseModel]] = None,
    ) -> None:
        self.client = client
        self.method = method
        self.params = params
        self.one_way = one_way
        self.timeout = timeout
        self.model = model

    def __await__(self) -> Generator:
        return self._call().__await__()

    async def _call(self) -> Any:
        res = await self.client._send_single_request(
            self._encode(), self.timeout, self.method, self.one_way
        )
        return self._convert_result(res)

    def _convert_result(self, res: Any) -> Any:
        if res is None:
            return None
        if self.model:
            return self.model(**res)
        return res

    def _encode(self) -> bytes:
        req = self.client._proto.create_request(
            self.method,
            RestRpcExecutor.cast2dump(
                self.params
                if isinstance(self.params, collections.abc.Mapping)
                else None
            ),
            self.one_way,
        )
        return req.serialize()


class RestRpcClient:
    def __init__(
        self,
        transport: Callable[[bytes, str, Optional[float]], Awaitable[bytes]],
        app: BaseApplication,
        exception_mapping_callback: Optional[
            Callable[[Optional[int], Optional[str], Optional[Any]], None]
        ] = None,
    ):
        self._app = app
        self._proto = RestRpcProtocol()
        self._transport = transport
        self._exception_mapping_callback = exception_mapping_callback

    def exec(
        self,
        method: str,
        params: Mapping[str, Any],
        one_way: bool = False,
        timeout: Optional[float] = None,
        model: Optional[Type[BaseModel]] = None,
    ) -> RestRpcCall:
        return RestRpcCall(self, method, params, one_way, timeout, model)

    def _raise_restrpc_error(
        self,
        code: Optional[int] = None,
        message: Optional[str] = None,
        data: Optional[Any] = None,
    ) -> None:
        if self._exception_mapping_callback is None:
            raise RestRpcError(code=code, message=message, data=data)
        return self._exception_mapping_callback(code, message, data)

    async def _send_single_request(
        self,
        request: bytes,
        timeout: Optional[float],
        method: str,
        one_way: bool,
    ) -> Any:
        with self._app.logger.capture_span(Span) as trap:
            response = await self._transport(request, method, timeout)
            if trap.is_captured:
                trap.span.name = 'rpc::out (%s)' % method
                trap.span.set_name4adapter(
                    self._app.logger.ADAPTER_PROMETHEUS, 'rpc_out'
                )
                trap.span.tag(SPAN_TAG_RPC_METHOD, method)
            if one_way:
                return None
            try:
                data = self._proto.parse_reply(response)
            except InvalidReplyError as err:
                raise RestRpcParseError(data=str(err))
            if isinstance(data, RestRpcErrorResponse):
                code: int = int(data._code)
                if trap.is_captured:
                    trap.span.tag(SPAN_TAG_RPC_CODE, str(code))
                self._raise_restrpc_error(
                    code,
                    str(data.error),
                    data.data if hasattr(data, 'data') else None,
                )
            if isinstance(data, RestRpcSuccessResponse):
                return data.result
            raise RuntimeError
