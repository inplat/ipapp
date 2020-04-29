from collections import defaultdict
from typing import Any, Optional

from tinyrpc.protocols.jsonrpc import FixedErrorMessageMixin

from ipapp.rpc.error import RpcError


class JsonRpcError(FixedErrorMessageMixin, RpcError):

    jsonrpc_error_code = -32000
    message = 'Server error'

    def __init__(
        self,
        jsonrpc_error_code: Optional[int] = None,
        message: Optional[str] = None,
        data: Any = None,
        **kwargs: Any,
    ) -> None:
        self.kwargs: dict = defaultdict(lambda: "")
        self.kwargs.update(kwargs)

        if jsonrpc_error_code is not None:
            self.jsonrpc_error_code = jsonrpc_error_code
        if message is not None:
            self.message = message
        if data is not None:
            self.data = data

        self.message = str(self.message).format_map(self.kwargs)

        super().__init__()
