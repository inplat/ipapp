import asyncio
import json
import uuid
from typing import Any, Dict, Optional, Tuple

from iprpc.executor import MethodExecutor

from ipapp.ctx import span
from ipapp.logger.span import Span
from ipapp.misc import ctx_span_get
from ipapp.mq.pika import (
    AmqpSpan,
    Deliver,
    PikaChannel,
    PikaChannelConfig,
    Properties,
)

from ..const import SPAN_TAG_RPC_CODE, SPAN_TAG_RPC_METHOD


class RpcError(Exception):
    def __init__(
        self, code: int, message: Optional[str], detail: Optional[str]
    ) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__('%s[%s] %s' % (message, code, detail))


class RpcServerChannelConfig(PikaChannelConfig):
    api: object
    queue: str
    prefetch_count: int = 1
    queue_durable: bool = True
    queue_auto_delete: bool = False
    queue_arguments: Optional[dict] = None
    debug: bool = False
    encoding: str = 'UTF-8'
    propagate_trace: bool = True

    class Config:
        arbitrary_types_allowed = True


class RpcClientChannelConfig(PikaChannelConfig):
    queue: str
    timeout: float = 60.0
    encoding: str = 'UTF-8'
    propagate_trace: bool = True


class RpcServerChannel(PikaChannel):
    cfg: RpcServerChannelConfig
    _rpc: MethodExecutor
    _lock: asyncio.Lock

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
        self._lock = asyncio.Lock(loop=self.amqp.loop)
        self._rpc = MethodExecutor(self.cfg.api)

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
            await self.ack(delivery_tag=deliver.delivery_tag)
            result = await self._rpc.call(body, encoding=self.cfg.encoding)
            span.tag(SPAN_TAG_RPC_METHOD, result.method)
            span.name = 'rpc::in::%s' % result.method
            if result.error is not None:
                span.error(result.error)

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
                msg = json.dumps(resp).encode(self.cfg.encoding)
                props = Properties()
                if proprties.correlation_id:
                    props.correlation_id = proprties.correlation_id

                with self.amqp.app.logger.capture_span(AmqpSpan) as trap:
                    await self.publish(
                        '',
                        proprties.reply_to,
                        msg,
                        props,
                        propagate_trace=self.cfg.propagate_trace,
                    )
                    trap.span.name = 'rpc::result::out'


class RpcClientChannel(PikaChannel):
    name = 'rpc_client'

    cfg: RpcClientChannelConfig
    _lock: asyncio.Lock
    _queue: str
    _futs: Dict[str, Tuple[asyncio.Future, Span]] = {}

    async def prepare(self) -> None:
        res = await self.queue_declare('', exclusive=True)
        self._queue = res.method.queue
        self._lock = asyncio.Lock(loop=self.amqp.loop)

    async def start(self) -> None:
        await self.consume(self._queue, self._message)

    async def stop(self) -> None:
        if self._consumer_tag is not None:
            await self.cancel()
            await self._lock.acquire()

    async def _message(
        self, body: bytes, deliver: Deliver, proprties: Properties
    ) -> None:
        span.name = 'rpc::result::in'
        async with self._lock:
            await self.ack(delivery_tag=deliver.delivery_tag)

        js = json.loads(body, encoding=self.cfg.encoding)

        if proprties.correlation_id in self._futs:
            fut, parent_span = self._futs[proprties.correlation_id]

            span.move(parent_span)

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
        msg = json.dumps({"method": method, "params": params}).encode(
            self.cfg.encoding
        )
        correlation_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.Future(loop=self.amqp.app.loop)
        parent_span = ctx_span_get()
        if parent_span is None:
            raise UserWarning
        self._futs[correlation_id] = (fut, parent_span)
        try:
            with self.amqp.app.logger.capture_span(AmqpSpan) as trap:
                await self.publish(
                    '',
                    self.cfg.queue,
                    msg,
                    Properties(
                        correlation_id=correlation_id, reply_to=self._queue
                    ),
                    propagate_trace=self.cfg.propagate_trace,
                )
                trap.span.tag(SPAN_TAG_RPC_METHOD, method)
                trap.span.name = 'rpc::out::%s' % method
            return await asyncio.wait_for(
                fut, timeout=timeout or self.cfg.timeout
            )
        finally:
            del self._futs[correlation_id]
