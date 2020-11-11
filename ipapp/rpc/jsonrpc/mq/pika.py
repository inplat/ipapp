import asyncio
import uuid
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Mapping,
    Optional,
    Tuple,
    Union,
)

from pydantic import Field

from ipapp.ctx import span
from ipapp.logger import Span
from ipapp.misc import json_encode as default_json_encode
from ipapp.mq.pika import (
    AmqpSpan,
    Deliver,
    PikaChannel,
    PikaChannelConfig,
    Properties,
)
from ipapp.rpc.jsonrpc import (
    JsonRpcCall,
    JsonRpcClient,
    JsonRpcError,
    JsonRpcExecutor,
)
from ipapp.rpc.main import RpcRegistry


class RpcServerChannelConfig(PikaChannelConfig):
    queue: str = Field("rpc", description="Название очереди")
    prefetch_count: int = Field(
        1, description="Количество сообщений, получаемых из очереди"
    )
    queue_durable: bool = Field(True, description="Устойчивая очередь")
    queue_auto_delete: bool = Field(
        False,
        description="Удалять очередь, если потребитель "
        "отменился или отключился",
    )
    queue_arguments: Optional[dict] = Field(
        None, description="Настройки очереди"
    )
    debug: bool = Field(
        False, description="Возвращать в RPC ошибках стектрейсы"
    )
    propagate_trace: bool = Field(
        True, description="Передавать заголовки спанов в заголовках сообщений"
    )


class RpcClientChannelConfig(PikaChannelConfig):
    queue: str = Field("rpc", description="Название очереди")
    timeout: float = Field(60.0, description="Таймаут RPC вызова")
    propagate_trace: bool = Field(
        True, description="Передавать заголовки спанов в заголовках сообщений"
    )


class RpcServerChannel(PikaChannel):
    cfg: RpcServerChannelConfig
    _rpc: JsonRpcExecutor
    _lock: asyncio.Lock

    def __init__(
        self,
        registry: Union[RpcRegistry, object],
        cfg: RpcServerChannelConfig,
        json_encode: Callable[[Any], str] = default_json_encode,
    ) -> None:
        self.registry = registry
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
        self._rpc = JsonRpcExecutor(self.registry, self.amqp.app)

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

            result = await self._rpc.exec(body)

            if proprties.reply_to:
                props = Properties()
                if proprties.correlation_id:
                    props.correlation_id = proprties.correlation_id
                if self.cfg.propagate_trace:
                    props.headers = span.to_headers()

                with self.amqp.app.logger.capture_span(AmqpSpan) as trap:
                    await self.publish(
                        '',
                        proprties.reply_to,
                        result,
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
    _clt: JsonRpcClient
    _exception_mapping_callback: Optional[
        Callable[[Optional[int], Optional[str], Optional[Any]], None]
    ] = None

    async def prepare(self) -> None:
        res = await self.queue_declare('', exclusive=True)
        self._queue = res.method.queue
        self._lock = asyncio.Lock()
        self._clt = JsonRpcClient(
            self._transport, self.amqp.app, self._exception_mapping_callback
        )

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

            fut.set_result(body)

    async def _transport(
        self, request: bytes, timeout: Optional[float]
    ) -> bytes:
        correlation_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.Future()
        self._futs[correlation_id] = (fut, span)
        try:
            with self.amqp.app.logger.capture_span(AmqpSpan) as trap:
                headers: Dict[str, str] = {}
                if self.cfg.propagate_trace and span:
                    headers = span.to_headers()
                await self.publish(
                    '',
                    self.cfg.queue,
                    request,
                    Properties(
                        correlation_id=correlation_id,
                        reply_to=self._queue,
                        headers=headers,
                    ),
                    propagate_trace=False,
                )
                if span:
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

    def exec(
        self,
        method: str,
        params: Union[Iterable[Any], Mapping[str, Any], None] = None,
        one_way: bool = False,
        timeout: Optional[float] = None,
    ) -> JsonRpcCall:
        return self._clt.exec(method, params, one_way=one_way, timeout=timeout)

    async def exec_batch(
        self, *calls: JsonRpcCall, timeout: Optional[float] = None
    ) -> Tuple[Union[JsonRpcError, Any], ...]:
        return await self._clt.exec_batch(*calls, timeout=timeout)
