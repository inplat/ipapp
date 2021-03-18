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
from tinyrpc import exc as rpc_exc
from tinyrpc.protocols import jsonrpc as rpc
from tinyrpc.protocols.jsonrpc import (
    InvalidReplyError,
    JSONRPCErrorResponse,
    JSONRPCInvalidRequestError,
    JSONRPCParseError,
    JSONRPCProtocol,
    JSONRPCSuccessResponse,
    _get_code_message_and_data,
)

from ipapp import BaseApplication
from ipapp.ctx import app, span
from ipapp.logger import Span
from ipapp.misc import from_bytes
from ipapp.rpc.error import InvalidArguments as _InvalidArguments
from ipapp.rpc.error import MethodNotFound as _MethodNotFound
from ipapp.rpc.main import Executor as _Executor
from ipapp.rpc.main import RpcRegistry

from ..const import SPAN_TAG_RPC_CODE, SPAN_TAG_RPC_METHOD
from .error import JsonRpcError
from .openrpc.discover import discover
from .openrpc.models import ExternalDocs, Server

SPAN_TAG_JSONRPC_METHOD = 'rpc.method'
SPAN_TAG_JSONRPC_CODE = 'rpc.code'
SPAN_TAG_JSONRPC_IS_BATCH = 'rpc.is_batch'

# не легаси, потокол в соответствии со спецификацией json-rpc 2.0
REG_PROTO_JSON_RPC = 0

# легси где параметры на том же уровне, что и method, результат на том же
# уровне, что и code/message
# {"method": "name", "a": 1, "b": 2}
# {"code": 0, "message": "Ok", "any": "object", "another": "value"}
REG_PROTO_LEGACY_V1 = 1

# легси где method и params в разных аргументах, есть code и message
# {"method": "name", "params": {"a": 1, "b": 2}}
# {"code": 0, "message": "Ok", "result": {"any": "object"}}
REG_PROTO_LEGACY_V2 = 2


class JsonRpcExecutor:
    def __init__(
        self,
        registry: Union[RpcRegistry, object],
        app: BaseApplication,
        discover_enabled: bool = True,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        scheduler_kwargs: Optional[Dict[str, Any]] = None,
        servers: Optional[List[Server]] = None,
        external_docs: Optional[ExternalDocs] = None,
    ) -> None:
        self._registry = registry
        self._app = app
        self._discover_enabled = discover_enabled
        self._discover_result: Optional[Dict[str, Any]] = None
        self._ex = _Executor(registry)
        self._loop = loop
        self._protocol = rpc.JSONRPCProtocol()
        self._scheduler: Optional[aiojobs.Scheduler] = None
        self._scheduler_kwargs = scheduler_kwargs or {}

        self._servers: Optional[List[Server]] = servers
        self._external_docs: Optional[ExternalDocs] = external_docs

    async def start_scheduler(self) -> None:
        self._scheduler = await aiojobs.create_scheduler(
            **self._scheduler_kwargs
        )

    async def stop_scheduler(self) -> None:
        if self._scheduler is not None:
            await self._scheduler.close()
            self._scheduler = None

    async def exec(self, request: bytes) -> bytes:
        try:
            req = self._parse_request(request)
        except rpc_exc.RPCError as e:

            err_resp = e.error_respond()
            if hasattr(err_resp, 'data'):
                err_resp.data = self.cast2dump(err_resp.data)

            return err_resp.serialize()

        resp: Optional[Union[rpc.RPCBatchResponse, rpc.RPCResponse]]

        if isinstance(req, rpc.JSONRPCBatchRequest):
            resp = await self._exec_batch(req)
        elif isinstance(req, rpc.JSONRPCRequest):
            resp = await self._exec_single(req)
            code: int
            proto_ver = getattr(req, 'proto_ver', REG_PROTO_JSON_RPC)
            if proto_ver == REG_PROTO_LEGACY_V2:
                if isinstance(resp, JSONRPCSuccessResponse):
                    return json.dumps(
                        {'result': resp.result, 'code': 0, 'message': 'OK'}
                    ).encode()
                if isinstance(resp, JSONRPCErrorResponse):
                    code = int(resp._jsonrpc_error_code)
                    res = {
                        "code": code,
                        "message": str(resp.error),
                        "details": None,
                    }
                    if hasattr(resp, 'data'):
                        res['details'] = resp.data
                    return json.dumps(res).encode()
                raise RuntimeError
            elif proto_ver == REG_PROTO_LEGACY_V1:
                if isinstance(resp, JSONRPCSuccessResponse):
                    res = {'code': 0, 'message': 'OK'}
                    if isinstance(resp.result, dict):
                        res.update(resp.result)
                    elif resp.result is None:
                        pass
                    else:
                        raise NotImplementedError

                    return json.dumps(res).encode()
                if isinstance(resp, JSONRPCErrorResponse):
                    code = int(resp._jsonrpc_error_code)
                    res = {
                        "code": code,
                        "message": str(resp.error),
                    }
                    if hasattr(resp, 'data') and isinstance(resp.data, dict):
                        res.update(resp.data)
                    return json.dumps(res).encode()
                raise RuntimeError
        else:  # pragma: no cover
            raise NotImplementedError

        if resp is None:
            return b''

        return resp.serialize()

    def _parse_request(
        self, request: bytes
    ) -> Union['rpc.JSONRPCRequest', 'rpc.JSONRPCBatchRequest']:
        if span:
            span.set_name4adapter(
                self._app.logger.ADAPTER_PROMETHEUS, 'rpc_in'
            )

        try:
            return self._protocol.parse_request(request)
        except (JSONRPCInvalidRequestError, JSONRPCParseError) as err:
            if isinstance(err, JSONRPCInvalidRequestError):
                old = self._old_parse_request(request)
                if old is not None:
                    return old

            self._set_span_method(None)
            self._set_span_err(err)
            raise

    def _old_parse_request(
        self, request: bytes
    ) -> Optional[rpc.JSONRPCRequest]:
        # TODO поддержка старого формата

        try:
            data = json.loads(request)
        except Exception:
            return None

        if not isinstance(data, dict) or 'method' not in data:
            return None

        method = data.pop('method')

        if (len(data) == 1 and 'params' in data) or len(data) == 0:
            if 'params' in data and not isinstance(data['params'], dict):
                return None

            req = rpc.JSONRPCRequest()
            req.method = method
            req.kwargs = data.get('params')
            req.one_way = False
            req.unique_id = 1
            req.proto_ver = REG_PROTO_LEGACY_V2
        else:
            req = rpc.JSONRPCRequest()
            req.method = method
            req.kwargs = data
            req.one_way = False
            req.unique_id = 1
            req.proto_ver = REG_PROTO_LEGACY_V1

        return req

    async def _exec_batch(
        self, req: rpc.JSONRPCBatchRequest
    ) -> Optional[rpc.RPCBatchResponse]:
        if span:
            span.name = 'rpc::in::batch'
            span.tag(SPAN_TAG_JSONRPC_IS_BATCH, 'true')
            span.set_name4adapter(
                self._app.logger.ADAPTER_PROMETHEUS, 'rpc_in_batch'
            )

        resp = req.create_batch_response()
        batch = []

        for req_item in req:
            if isinstance(req_item, rpc.InvalidRequestError):
                batch.append(self._exec_err(req_item))
            else:
                batch.append(
                    self._exec(
                        req_item.method,
                        req_item.args,
                        req_item.kwargs,
                        req_item.one_way,
                    )
                )

        results = await asyncio.gather(*batch, return_exceptions=True)

        if resp is None:
            return None

        for i in range(len(req)):
            if isinstance(req[i], rpc.InvalidRequestError):
                err_resp = req[i].error_respond()
                if hasattr(err_resp, 'data'):
                    err_resp.data = self.cast2dump(err_resp.data)
                resp.append(err_resp)
            elif req[i].one_way:
                pass
            elif isinstance(results[i], BaseException):
                err_resp = req[i].error_respond(results[i])
                if hasattr(err_resp, 'data'):
                    err_resp.data = self.cast2dump(err_resp.data)
                resp.append(err_resp)
            else:
                resp.append(req[i].respond(self.cast2dump(results[i])))

        return resp

    async def _exec_single(
        self, req: rpc.JSONRPCRequest
    ) -> Optional[rpc.RPCResponse]:
        try:
            res = await self._exec(
                req.method, tuple(req.args), req.kwargs, req.one_way
            )
            return req.respond(self.cast2dump(res))
        except Exception as e:
            if not hasattr(e, 'jsonrpc_error_code'):
                app.log_err(e)
            return req.error_respond(e)
        finally:
            if req.one_way:
                return None

    async def _exec(
        self,
        method: str,
        args: Tuple[Any, ...],
        kwargs: Dict[str, Any],
        is_one_way: bool,
    ) -> Any:
        if span and span.tags.get(SPAN_TAG_JSONRPC_IS_BATCH):
            with span.new_child(kind=Span.KIND_SERVER):
                span.set_name4adapter(
                    self._app.logger.ADAPTER_PROMETHEUS, 'rpc_in'
                )
                return await self._exec_method(
                    method, args, kwargs, is_one_way
                )
        else:
            return await self._exec_method(method, args, kwargs, is_one_way)

    async def _exec_method(
        self,
        method: str,
        args: Tuple[Any, ...],
        kwargs: Dict[str, Any],
        is_one_way: bool,
    ) -> Any:
        self._set_span_method(method)
        try:
            if is_one_way and self._scheduler is not None:
                await self._scheduler.spawn(
                    self._exec_in_executor(method, args, kwargs)
                )
                return None
            else:
                return await self._exec_in_executor(method, args, kwargs)
        except Exception as err:
            self._set_span_err(err)
            raise self._map_exc(err)

    async def _exec_in_executor(
        self, method: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]
    ) -> Any:
        try:
            if self._discover_enabled and method == 'rpc.discover':
                if len(args) or len(kwargs):
                    raise _InvalidArguments()
                return self._discover()
            return await self._ex.exec(method, args, kwargs)
        except Exception as err:
            if app and span and hasattr(err, 'jsonrpc_error_code'):
                span.tag(SPAN_TAG_RPC_CODE, getattr(err, 'jsonrpc_error_code'))
            raise

    def _discover(self) -> Dict[str, Any]:
        if self._discover_result is not None:
            return self._discover_result

        result = discover(
            self._registry,
            servers=self._servers,
            external_docs=self._external_docs,
        )

        self._discover_result = json.loads(
            result.json(by_alias=True, exclude_unset=True)
        )

        return self._discover_result

    async def _exec_err(self, err: Exception) -> None:

        if span and span.tags.get(SPAN_TAG_JSONRPC_IS_BATCH):
            with span.new_child(kind=Span.KIND_SERVER):
                self._set_span_method(None)
                await self._handle_err(err)
        else:
            await self._handle_err(err)

    async def _handle_err(self, err: Exception) -> None:
        try:
            raise self._map_exc(err)
        except Exception as err:
            self._set_span_err(err)
            raise

    @staticmethod
    def _map_exc(ex: Exception) -> Exception:
        if type(ex) is _MethodNotFound:
            return JsonRpcError(
                jsonrpc_error_code=-32601, message='Method not found'
            )
        if type(ex) is _InvalidArguments:
            return JsonRpcError(
                jsonrpc_error_code=-32602,
                message='Invalid params',
                data={'info': str(ex)},
            )
        return ex

    def _set_span_method(self, method: Optional[str]) -> None:
        if not span:
            return
        if method is not None:
            span.name = 'rpc::in (%s)' % method
            span.tag(SPAN_TAG_JSONRPC_METHOD, method)
        else:
            span.name = 'rpc::in::error'

    def _set_span_err(self, err: Exception) -> None:
        if not span:
            return
        span.tag('error', 'true')
        span.annotate(span.ANN_TRACEBACK, traceback.format_exc())
        if hasattr(err, 'jsonrpc_error_code'):
            span.tag(
                SPAN_TAG_JSONRPC_CODE,
                str(err.jsonrpc_error_code),  # type: ignore
            )
        else:
            code, _, _ = _get_code_message_and_data(err)
            span.tag(SPAN_TAG_JSONRPC_CODE, str(code))

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


class JsonRpcCall:
    def __init__(
        self,
        client: 'JsonRpcClient',
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
        self.unique_id = None

    def __await__(self) -> Generator:
        return self._call().__await__()

    async def _call(self) -> Any:
        res = await self.client._send_single_request(
            self._encode(), self.timeout, self.method, self.one_way
        )
        return self._convert_result(res)

    def _convert_result(self, res: Any) -> Any:
        if res is not None:
            if self.model:
                return self.model(**res)
            return res
        return None

    def _encode(self) -> bytes:
        req = self.client._proto.create_request(
            self.method,
            JsonRpcExecutor.cast2dump(
                self.params
                if isinstance(self.params, collections.abc.Sequence)
                else None
            ),
            JsonRpcExecutor.cast2dump(
                self.params
                if isinstance(self.params, collections.abc.Mapping)
                else None
            ),
            self.one_way,
        )
        self.unique_id = req.unique_id
        return req.serialize()


class JsonRpcClient:
    """

    async def transport(request: bytes,
                        timeout: Optional[float] = None) -> bytes:
        # send request via http, amqp, ...
        return response

    clt = JsonRpcClient(transport, app)

    res = await clt.exec('test', (1, 2, 3))

    res_1, res_2, res_3, res_4 = await clt.batch(
        clt.exec('test', (1, 2, 3)),
        clt.exec('test', (4, 5, 6)),
        clt.exec('test2', (4, 5, 6)),
        clt.exec('err'),
    )

    """

    def __init__(
        self,
        transport: Callable[[bytes, Optional[float]], Awaitable[bytes]],
        app: BaseApplication,
        exception_mapping_callback: Optional[
            Callable[[Optional[int], Optional[str], Optional[Any]], None]
        ] = None,
    ):
        self._app = app
        self._proto = JSONRPCProtocol()
        self._transport = transport
        self._exception_mapping_callback = exception_mapping_callback

    def exec(
        self,
        method: str,
        params: Union[Iterable[Any], Mapping[str, Any], None] = None,
        one_way: bool = False,
        timeout: Optional[float] = None,
        model: Optional[Type[BaseModel]] = None,
    ) -> JsonRpcCall:
        return JsonRpcCall(self, method, params, one_way, timeout, model)

    async def exec_batch(
        self, *calls: JsonRpcCall, timeout: Optional[float] = None
    ) -> Tuple[Union[JsonRpcError, Any], ...]:
        if len(calls) == 0:
            return ()
        b = []
        results_order: List[Any] = []

        one_way = True
        for c in calls:
            b.append(c._encode())
            results_order.append(c.unique_id)
            if not c.one_way:
                one_way = False

        request = b'[%s]' % (b','.join(b),)
        results: List[Any] = [None for _ in results_order]

        rep = await self._send_batch_request(request, timeout, not one_way)

        if rep is None:  # one_way=true
            return tuple(results)

        for r in rep:
            rd = json.dumps(r).encode()
            try:
                result = self._proto.parse_reply(rd)  # FIXME in tinyrpc(batch)
            except InvalidReplyError as err:
                raise JsonRpcError(jsonrpc_error_code=-32000, message=str(err))

            if result.unique_id is not None:
                try:
                    idx = results_order.index(result.unique_id)
                except ValueError:
                    raise JsonRpcError(
                        jsonrpc_error_code=-32000,
                        message="Invalid reply: Unexpected request id %s"
                        "" % result.unique_id,
                    )

                if isinstance(result, JSONRPCErrorResponse):
                    data: Any = None
                    if hasattr(result, 'data'):
                        data = result.data
                    code = getattr(result, '_jsonrpc_error_code')
                    results[idx] = JsonRpcError(
                        jsonrpc_error_code=code,
                        message=str(result.error),
                        data=data,
                    )
                elif isinstance(result, JSONRPCSuccessResponse):
                    results[idx] = calls[idx]._convert_result(result.result)
                else:
                    raise NotImplementedError

        return tuple(results)

    def _raise_jsonrpc_error(
        self,
        code: Optional[int] = None,
        message: Optional[str] = None,
        data: Optional[Any] = None,
    ) -> None:
        if self._exception_mapping_callback is not None:
            return self._exception_mapping_callback(code, message, data)
        else:
            raise JsonRpcError(
                jsonrpc_error_code=code, message=message, data=data
            )

    async def _send_single_request(
        self,
        request: bytes,
        timeout: Optional[float],
        method: str,
        one_way: bool,
    ) -> Any:
        with self._app.logger.capture_span(Span) as trap:
            response = await self._transport(request, timeout)
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
                raise JsonRpcError(jsonrpc_error_code=-32000, message=str(err))

            if isinstance(data, JSONRPCErrorResponse):
                code: int = int(data._jsonrpc_error_code)

                if trap.is_captured:
                    trap.span.tag(SPAN_TAG_RPC_CODE, str(code))

                self._raise_jsonrpc_error(
                    code,
                    str(data.error),
                    data.data if hasattr(data, 'data') else None,
                )

            if isinstance(data, JSONRPCSuccessResponse):

                return data.result

            raise RuntimeError

    async def _send_batch_request(
        self, request: bytes, timeout: Optional[float], parse_resp: bool
    ) -> Optional[List[Any]]:
        with self._app.logger.capture_span(Span) as trap:
            result = await self._transport(request, timeout)
            if trap.is_captured:
                trap.span.name = 'rpc::out::batch'
                trap.span.tag(SPAN_TAG_JSONRPC_IS_BATCH, 'true')

            if not parse_resp:
                return None

            try:
                rep = json.loads(result)
            except Exception as err:
                self._raise_jsonrpc_error(message='Invalid reply: %s' % err)

            if not isinstance(rep, list):
                rep_err = self._proto.parse_reply(result)
                if isinstance(rep_err, JSONRPCErrorResponse):
                    code: int = int(rep_err._jsonrpc_error_code)

                    if trap.is_captured:
                        trap.span.tag(SPAN_TAG_RPC_CODE, str(code))

                    self._raise_jsonrpc_error(
                        code=code,
                        message=str(rep_err.error),
                        data=rep_err.data
                        if hasattr(rep_err, 'data')
                        else None,
                    )
                else:
                    self._raise_jsonrpc_error(message='Invalid reply')

            return rep
