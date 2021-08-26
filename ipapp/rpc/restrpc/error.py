import json
from collections import defaultdict
from typing import Any, Optional

from tinyrpc import (
    InvalidParamsError,
    InvalidReplyError,
    InvalidRequestError,
    MethodNotFoundError,
    RPCErrorResponse,
    ServerError,
)

from ipapp.rpc.error import InvalidArguments as ipapp_InvalidArguments
from ipapp.rpc.error import MethodNotFound as ipapp_MethodNotFound
from ipapp.rpc.error import RpcError
from ipapp.rpc.jsonrpc import JsonRpcExecutor


class RestRpcFixedErrorMessageMixin:
    code = 500
    message = 'Internal Server Error'

    def __init__(
        self,
        code: Optional[int] = None,
        message: Optional[str] = None,
        data: Any = None,
        **kwargs: Any,
    ) -> None:
        self.kwargs: dict = defaultdict(lambda: "")
        self.kwargs.update(kwargs)
        if code is not None:
            self.code = code
        if message is not None:
            self.message = message
        if data is not None:
            self.data = data
        self.message = str(self.message).format_map(self.kwargs)
        super().__init__()

    def error_respond(self) -> 'RestRpcErrorResponse':
        response = RestRpcErrorResponse()
        response.error = self.message
        response._code = self.code
        if hasattr(self, 'data'):
            response.data = self.data
        return response


class RestRpcError(RestRpcFixedErrorMessageMixin, RpcError):
    pass


class RestRpcParseError(RestRpcError, InvalidReplyError):
    code = 500
    message = 'Parse reply error'


class RestRpcInvalidRequestError(RestRpcError, InvalidRequestError):
    code = 400
    message = 'Bad Request'


class RestRpcMethodNotFoundError(
    RestRpcError, MethodNotFoundError, ipapp_MethodNotFound
):
    code = 404
    message = 'Method not found'


class RestRpcInvalidParamsError(
    RestRpcError, InvalidParamsError, ipapp_InvalidArguments
):
    code = 400
    message = 'Invalid params'


class RestRpcServerError(RestRpcError, ServerError):
    code = 500
    message = 'Internal Server Error'


class RestRpcErrorResponse(RPCErrorResponse):
    def _to_dict(self) -> dict:
        msg = {
            'error': {
                'message': str(self.error),
                'code': self._code,
            },
        }
        if hasattr(self, 'data'):
            msg['error']['data'] = self.data
        return msg

    def serialize(self) -> bytes:
        return json.dumps(JsonRpcExecutor.cast2dump(self._to_dict())).encode()
