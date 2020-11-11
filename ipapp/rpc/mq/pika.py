import asyncio
import json
import uuid
import warnings
from typing import Any, Callable, Dict, Optional, Tuple

from iprpc.executor import InternalError, MethodExecutor
from pydantic import Field

from ipapp.ctx import span
from ipapp.logger import Span, wrap2span
from ipapp.misc import json_encode as default_json_encode
from ipapp.mq.pika import (
    AmqpOutSpan,
    AmqpSpan,
    Deliver,
    PikaChannel,
    PikaChannelConfig,
    Properties,
)

from ..const import SPAN_TAG_RPC_CODE, SPAN_TAG_RPC_METHOD

warnings.warn(
    "Module is deprecated. Use ipapp.rpc.jsonrpc.mq.pika instead",
    DeprecationWarning,
    stacklevel=2,
)


class RpcError(Exception):
    def __init__(
        self, code: int, message: Optional[str], detail: Optional[str]
    ) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__('%s[%s] %s' % (message, code, detail))


class RpcServerChannelConfig(PikaChannelConfig):
    queue: str = Field("rpc", description="Название очереди")
    prefetch_count: int = Field(
        1, description="Количество сообщений, получаемых из очереди"
    )
    queue_durable: bool = Field(True, description="Устойчивая очередь")
    queue_auto_delete: bool = Field(
        False,
        description="Удалять очередь, если потребитель отменился или отключился",
    )
    queue_arguments: Optional[dict] = Field(
        None, description="Настройки очереди"
    )
    debug: bool = Field(
        False, description="Возвращать в RPC ошибках стектрейсы"
    )
    encoding: str = Field("UTF-8", description="Кодировка")
    propagate_trace: bool = Field(
        True, description="Передавать заголовки спанов в заголовках сообщений"
    )


class RpcClientChannelConfig(PikaChannelConfig):
    queue: str = Field("rpc", description="Название очереди")
    timeout: float = Field(60.0, description="Таймаут RPC вызова")
    encoding: str = Field("UTF-8", description="Кодировка")
    propagate_trace: bool = Field(
        True, description="Передавать заголовки спанов в заголовках сообщений"
    )


class RpcServerChannel(PikaChannel):
    cfg: RpcServerChannelConfig
    _rpc: MethodExecutor
    _lock: asyncio.Lock

    def __init__(
        self,
        api: object,
        cfg: RpcServerChannelConfig,
        json_encode: Callable[[Any], str] = default_json_encode,
    ) -> None:
        self.api = api
        super().__init__(cfg, json_encode=json_encode)

    async def prepare(self) -> None:
        await self.queue_declare(
            self.cfg.queue,
            False,
            self.cfg.queue_durable,
            False,
            self.cfg.queue_auto_delete,
            self.cfg.queue_arguments,
        )
        await self.qos(prefetch_count=self.cfg.prefetch_count)
        self._lock = asyncio.Lock()
        self._rpc = MethodExecutor(self.api)

    async def start(self) -> None:
        await self.consume(self.cfg.queue, self._message)

    async def stop(self) -> None:
        if self._consumer_tag is not None:
            await self.cancel()
            await self._lock.acquire()

    async def _message(
        self, body: bytes, deliver: Deliver, proprties: Properties
    ) -> None:
        async with self._lock:
            with self.amqp.app.logger.capture_span(AmqpSpan) as trap:
                await self.ack(delivery_tag=deliver.delivery_tag)
                trap.span.skip()
            result = await self._rpc.call(body, encoding=self.cfg.encoding)
            if result.method is not None:
                span.tag(SPAN_TAG_RPC_METHOD, result.method)
            span.name = 'rpc::in (%s)' % result.method
            if result.error is not None:
                span.error(result.error)
                if isinstance(result.error, InternalError):
                    self.amqp.app.log_err(result.error)

            if proprties.reply_to:
                if result.error is not None:
                    resp = {
                        "code": result.error.code,
                        "message": result.error.message,
                        "details": str(result.error.parent),
                    }

                    if self.cfg.debug:
                        resp['trace'] = result.error.trace
                    if result.result is not None:
                        resp['result'] = result.result
                else:
                    resp = {
                        "code": 0,
                        "message": 'OK',
                        'result': result.result,
                    }

                span.tag(SPAN_TAG_RPC_CODE, resp['code'])
                msg = self._json_encode(resp).encode(self.cfg.encoding)
                props = Properties()
                if proprties.correlation_id:
                    props.correlation_id = proprties.correlation_id
                if self.cfg.propagate_trace:
                    props.headers = span.to_headers()

                with self.amqp.app.logger.capture_span(AmqpSpan) as trap:
                    await self.publish(
                        '',
                        proprties.reply_to,
                        msg,
                        props,
                        propagate_trace=False,
                    )
                    # trap.span.name = 'rpc::result::out'
                    trap.span.copy_to(
                        span, annotations=True, tags=True, error=True
                    )
                    trap.span.skip()


class RpcClientChannel(PikaChannel):
    name = 'rpc_client'

    cfg: RpcClientChannelConfig
    _lock: asyncio.Lock
    _queue: str
    _futs: Dict[str, Tuple[asyncio.Future, Span]] = {}

    async def prepare(self) -> None:
        res = await self.queue_declare('', exclusive=True)
        self._queue = res.method.queue
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self.consume(self._queue, self._message)

    async def stop(self) -> None:
        if self._consumer_tag is not None:
            await self.cancel()
            await self._lock.acquire()

    async def _message(
        self, body: bytes, deliver: Deliver, proprties: Properties
    ) -> None:
        async with self._lock:
            await self.ack(delivery_tag=deliver.delivery_tag)

        js = json.loads(body, encoding=self.cfg.encoding)

        if proprties.correlation_id in self._futs:
            fut, parent_span = self._futs[proprties.correlation_id]

            anns = span.annotations.get(AmqpSpan.ANN_IN_PROPS)
            if anns is not None and len(anns) > 0:
                ann_body, ann_stamp = anns[0]
                parent_span.annotate(
                    AmqpSpan.ANN_OUT_PROPS, ann_body, ann_stamp
                )

            anns = span.annotations.get(AmqpSpan.ANN_IN_BODY)
            if anns is not None and len(anns) > 0:
                ann_body, ann_stamp = anns[0]
                parent_span.annotate(
                    AmqpSpan.ANN_OUT_BODY, ann_body, ann_stamp
                )

            span.copy_to(
                parent_span, annotations=False, tags=False, error=True
            )
            span.skip()

            parent_span.tag(SPAN_TAG_RPC_CODE, js['code'])

            if js['code'] == 0:
                fut.set_result(js['result'])
            else:
                fut.set_exception(
                    RpcError(js['code'], js['message'], js.get('detail'))
                )

    async def call(
        self,
        method: str,
        params: Dict[str, Any],
        timeout: Optional[float] = None,
    ) -> Any:
        msg = self._json_encode({"method": method, "params": params}).encode(
            self.cfg.encoding
        )
        correlation_id = str(uuid.uuid4())

        fut: asyncio.Future = asyncio.Future()
        with wrap2span(
            kind=Span.KIND_CLIENT, app=self.amqp.app, cls=AmqpOutSpan
        ) as span:
            span.tag(SPAN_TAG_RPC_METHOD, method)
            span.name = 'rpc::out (%s)' % method

            self._futs[correlation_id] = (fut, span)
            try:
                with self.amqp.app.logger.capture_span(AmqpSpan) as trap:
                    headers: Dict[str, str] = {}
                    if self.cfg.propagate_trace:
                        headers = span.to_headers()
                    await self.publish(
                        '',
                        self.cfg.queue,
                        msg,
                        Properties(
                            correlation_id=correlation_id,
                            reply_to=self._queue,
                            headers=headers,
                        ),
                        propagate_trace=False,
                    )

                    anns = trap.span.annotations.get(AmqpSpan.ANN_OUT_PROPS)
                    if anns is not None and len(anns) > 0:
                        ann_body, ann_stamp = anns[0]
                        span.annotate(
                            AmqpSpan.ANN_IN_PROPS, ann_body, ann_stamp
                        )

                    anns = trap.span.annotations.get(AmqpSpan.ANN_OUT_BODY)
                    if anns is not None and len(anns) > 0:
                        ann_body, ann_stamp = anns[0]
                        span.annotate(
                            AmqpSpan.ANN_IN_BODY, ann_body, ann_stamp
                        )

                    trap.span.copy_to(
                        span, annotations=False, tags=True, error=True
                    )
                    trap.span.skip()

                return await asyncio.wait_for(
                    fut, timeout=timeout or self.cfg.timeout
                )

            finally:
                del self._futs[correlation_id]
