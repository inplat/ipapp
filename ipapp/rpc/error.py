class RpcError(Exception):
    pass


class InvalidArguments(RpcError):
    pass


class MethodNotFound(RpcError):
    pass
