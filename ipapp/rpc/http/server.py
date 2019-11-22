import json

from aiohttp import web
from iprpc.executor import BaseError, InternalError, MethodExecutor
from pydantic.main import BaseModel

from ipapp.ctx import span
from ipapp.http.server import ServerHandler

from ..const import SPAN_TAG_RPC_CODE, SPAN_TAG_RPC_METHOD


class RpcHandlerConfig(BaseModel):
    path: str = '/'
    healthcheck_path: str = '/health'
    debug: bool = False


class RpcHandler(ServerHandler):
    def __init__(self, api: object, cfg: RpcHandlerConfig) -> None:
        self._cfg = cfg
        self._api = api
        self._rpc = MethodExecutor(api)

    async def prepare(self) -> None:
        self._setup_healthcheck(self._cfg.healthcheck_path)
        self.server.add_route('POST', self._cfg.path, self.rpc_handler)

    def _err_resp(self, err: BaseError) -> dict:
        resp = {
            "code": err.code,
            "message": err.message,
            "details": str(err.parent),
        }

        if self._cfg.debug:
            resp['trace'] = err.trace

        return resp

    async def error_handler(
        self, request: web.Request, err: Exception
    ) -> web.Response:
        return web.Response(
            body=json.dumps(
                self._err_resp(InternalError(parent=err))
            ).encode(),
            content_type='application/json',
        )

    async def rpc_handler(self, request: web.Request) -> web.Response:
        try:
            result = await self._rpc.call(
                await request.read(), request.charset
            )
            if result.method is not None:
                span.tag(SPAN_TAG_RPC_METHOD, result.method)

            if result.error is not None:
                span.error(result.error)
                resp = self._err_resp(result.error)
                if result.result is not None:
                    resp['result'] = result.result

            else:
                resp = {"code": 0, "message": 'OK', 'result': result.result}

            span.tag(SPAN_TAG_RPC_CODE, resp['code'])
            span.name = 'rpc::in::%s' % result.method
            span.set_name4adapter(self.app.logger.ADAPTER_PROMETHEUS, 'rpc_in')

            body = json.dumps(resp).encode()

            return web.Response(body=body, content_type='application/json')
        except Exception as err:
            span.error(err)
            self.app.log_err(err)
            raise
