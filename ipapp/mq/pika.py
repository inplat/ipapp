import asyncio
import logging
import sys
from abc import ABC
from functools import partial
from typing import Any, Awaitable, Callable, List, Optional

import pika
import pika.adapters.asyncio_connection
import pika.adapters.base_connection
import pika.adapters.select_connection
import pika.adapters.utils.connection_workflow
import pika.adapters.utils.io_services_utils
import pika.adapters.utils.selector_ioloop_adapter
import pika.callback
import pika.channel
import pika.compat
import pika.exceptions
import pika.frame
from async_timeout import timeout
from pika.adapters.asyncio_connection import AsyncioConnection
from pydantic import BaseModel, Field

from ipapp.component import Component
from ipapp.error import PrepareError
from ipapp.logger import wrap2span
from ipapp.logger.span import Span
from ipapp.misc import ctx_span_reset, ctx_span_set, decode_bytes, dict_merge
from ipapp.misc import json_encode as default_json_encode
from ipapp.misc import mask_url_pwd

pika.adapters.utils.selector_ioloop_adapter.LOGGER.level = logging.CRITICAL
pika.connection.LOGGER.level = logging.CRITICAL
pika.callback.LOGGER.level = logging.CRITICAL
pika.channel.LOGGER.level = logging.CRITICAL
pika.adapters.utils.connection_workflow._LOG.level = logging.CRITICAL
pika.adapters.utils.io_services_utils._LOGGER.level = logging.CRITICAL
pika.adapters.base_connection.LOGGER.level = logging.CRITICAL
pika.adapters.select_connection.LOGGER.level = logging.CRITICAL
pika.adapters.asyncio_connection.LOGGER.level = logging.CRITICAL


Deliver = pika.spec.Basic.Deliver
Properties = pika.spec.BasicProperties


class AmqpSpan(Span):
    NAME_PREPARE = 'amqp::prepare'
    NAME_CONNECT = 'amqp::connect'
    NAME_CHANNEL = 'amqp::channel'
    NAME_PUBLISH = 'amqp::publish'
    NAME_CONSUME = 'amqp::consume'
    NAME_ACK = 'amqp::ack'
    NAME_NACK = 'amqp::nack'
    NAME_DECLARE_EXCHANGE = 'amqp::declare_exchange'
    NAME_DECLARE_QUEUE = 'amqp::declare_queue'
    NAME_BIND = 'amqp::bind'
    NAME_QOS = 'amqp::qos'
    NAME_CANCEL = 'amqp::cancel'
    NAME_MESSAGE = 'amqp::message'

    ANN_IN_PROPS = 'amqp_in_props'
    ANN_IN_BODY = 'amqp_in_body'
    ANN_OUT_PROPS = 'amqp_out_props'
    ANN_OUT_BODY = 'amqp_out_body'

    TAG_CHANNEL_NAME = 'amqp.channel_name'
    TAG_CHANNEL_NUMBER = 'amqp.channel_number'
    TAG_EXCHANGE = 'amqp.exchange'
    TAG_ROUTING_KEY = 'amqp.routing_key'
    TAG_URL = 'amqp.url'


class AmqpInSpan(AmqpSpan):
    pass


class AmqpOutSpan(AmqpSpan):
    pass


class PikaConfig(BaseModel):
    url: str = Field(
        "amqp://guest:guest@localhost:5672/",
        description="Строка подключения к брокеру сообщений",
    )
    connect_timeout: float = Field(
        60.0, description="Таймаут подключения к брокеру сообщений"
    )
    channel_open_timeout: float = Field(
        60.0, description="Таймаут открытия канала"
    )
    exchange_declare_timeout: float = Field(
        60.0, description="Таймаут объявления exchange"
    )
    queue_declare_timeout: float = Field(
        60.0, description="Таймаут объявления очереди"
    )
    bind_timeout: float = Field(60.0, description="Таймаут привязки очереди")
    publish_timeout: float = Field(
        60.0, description="Таймаут публикации сообщения"
    )
    connect_max_attempts: int = Field(
        10,
        description=(
            "Максимальное количество попыток подключения к брокеру сообщений"
        ),
    )
    connect_retry_delay: float = Field(
        1.0,
        description=(
            "Задержка перед повторной попыткой подключения к брокеру сообщений"
        ),
    )
    log_in_props: bool = Field(
        True, description="Логирование входящих свойств сообщения"
    )
    log_in_body: bool = Field(
        True, description="Логирование входящего тела сообщения"
    )
    log_out_props: bool = Field(
        True, description="Логирование исходящих свойств сообщения"
    )
    log_out_body: bool = Field(
        True, description="Логирование исходящего тела сообщения"
    )


class PikaChannelConfig(BaseModel):
    pass


class _Connection:
    STATE_NONE = 0
    STATE_CONNECTING = 1
    STATE_CONNECTED = 2
    STATE_CLOSING = 3
    STATE_CLOSED = 4

    def __init__(
        self,
        pika: 'Pika',
        cfg: PikaConfig,
        *,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self.pika = pika
        self.cfg = cfg
        self._conn: Optional[AsyncioConnection] = None
        self._loop: asyncio.AbstractEventLoop = (
            loop or asyncio.get_event_loop()
        )
        self._fut: Optional[asyncio.Future] = None
        self._lock = asyncio.Lock()
        self._on_close: Optional[
            Callable[['_Connection', Exception], Awaitable[None]]
        ] = None
        self._state = self.STATE_NONE

    @property
    def state(self) -> int:
        return self._state

    async def connect(
        self,
        on_close: Callable[['_Connection', Exception], Awaitable[None]],
    ) -> None:
        with wrap2span(
            name=AmqpSpan.NAME_CONNECT,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.pika.app,
        ) as span:
            span.tag(AmqpSpan.TAG_URL, self.pika._masked_url)
            async with self._lock:
                self._state = self.STATE_CONNECTING
                self._on_close = on_close
                self._fut = asyncio.Future()
                self._conn = AsyncioConnection(
                    parameters=pika.URLParameters(self.cfg.url),
                    on_open_callback=self._on_connection_open,
                    on_open_error_callback=self._on_connection_open_error,
                    on_close_callback=self._on_connection_closed,
                )
                try:
                    await asyncio.wait_for(
                        self._fut, timeout=self.cfg.connect_timeout
                    )
                    if self._fut.result() != 1:
                        raise RuntimeError()
                    self._state = self.STATE_CONNECTED
                except Exception:
                    self._state = self.STATE_CLOSED
                    raise
                finally:
                    self._fut = None

    async def close(self) -> None:
        async with self._lock:
            if self._conn is None:
                self._state = self.STATE_CLOSED
                return

            self._state = self.STATE_CLOSING
            self._on_close = None
            self._fut = asyncio.Future()
            self._conn.close()
            try:
                await asyncio.wait_for(
                    self._fut, timeout=self.cfg.connect_timeout
                )
            except pika.exceptions.ConnectionClosed:
                self._state = self.STATE_CLOSED
                return
            finally:
                self._fut = None

    def _on_connection_open(
        self, _unused_connection: AsyncioConnection
    ) -> None:
        if self._fut is not None:
            self._fut.set_result(1)

    def _on_connection_open_error(
        self, _unused_connection: AsyncioConnection, err: Exception
    ) -> None:
        if self._fut is not None:
            self._fut.set_exception(err)
        elif self._on_close is not None:
            asyncio.ensure_future(self._on_close(self, err))

    def _on_connection_closed(
        self, _unused_connection: AsyncioConnection, reason: Exception
    ) -> None:
        if self._fut is not None:
            self._fut.set_exception(reason)
        elif self._on_close is not None:
            asyncio.ensure_future(self._on_close(self, reason))

    async def channel(
        self,
        on_close: Optional[
            Callable[[pika.channel.Channel, Exception], Awaitable[None]]
        ],
        name: Optional[str],
    ) -> pika.channel.Channel:
        with wrap2span(
            name=AmqpSpan.NAME_CHANNEL,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.pika.app,
        ) as span:
            if name is not None:
                span.tag(AmqpSpan.TAG_CHANNEL_NAME, name)
            async with self._lock:
                if self._conn is None:
                    raise pika.exceptions.AMQPConnectionError()
                self._fut = asyncio.Future()
                self._conn.channel(
                    on_open_callback=partial(self._on_channel_open, on_close)
                )
                try:
                    channel: Any = await asyncio.wait_for(
                        self._fut, timeout=self.cfg.channel_open_timeout
                    )
                    if not isinstance(channel, pika.channel.Channel):
                        raise RuntimeError()
                finally:
                    self._fut = None

                span.tag(
                    AmqpSpan.TAG_CHANNEL_NUMBER, str(channel.channel_number)
                )

                return channel

    def _on_channel_open(
        self,
        on_close: Optional[
            Callable[[pika.channel.Channel, Exception], Awaitable[None]]
        ],
        channel: pika.channel.Channel,
    ) -> None:
        channel.add_on_close_callback(
            partial(self._on_channel_closed, on_close)
        )
        if self._fut is None:
            raise RuntimeError()
        self._fut.set_result(channel)

    def _on_channel_closed(
        self,
        on_close: Optional[
            Callable[[pika.channel.Channel, Exception], Awaitable[None]]
        ],
        channel: pika.channel.Channel,
        reason: Exception,
    ) -> None:
        if on_close is not None:
            asyncio.ensure_future(on_close(channel, reason))


class PikaChannel(ABC):
    name: Optional[str] = None
    amqp: 'Pika'
    _conn: '_Connection'
    _ch: 'pika.channel.Channel'
    _lock: 'asyncio.Lock'
    _consumer_tag: Optional[str]

    def __init__(
        self,
        cfg: PikaChannelConfig,
        json_encode: Callable[[Any], str] = default_json_encode,
    ) -> None:
        self.cfg = cfg
        self._json_encode = json_encode

    def _init(self, amqp: 'Pika', ch: pika.channel.Channel) -> None:
        self.amqp = amqp
        if amqp._conn is None:
            raise UserWarning()
        self._conn = amqp._conn  # noqa
        self._ch = ch
        self._lock = asyncio.Lock()
        self._consumer_tag: Optional[str] = None
        self._close_fut: asyncio.Future = asyncio.Future()

    async def prepare(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def on_close(self, err: Exception) -> None:
        self.amqp.app.log_err(err)
        self._close_fut.set_exception(err)

    async def exchange_declare(
        self,
        exchange: str,
        exchange_type: str = 'direct',
        passive: bool = False,
        durable: bool = False,
        auto_delete: bool = False,
        internal: bool = False,
        arguments: Optional[dict] = None,
    ) -> pika.frame.Method:
        with wrap2span(
            name=AmqpSpan.NAME_DECLARE_EXCHANGE,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.amqp.app,
        ) as span:
            if self.amqp is None:
                raise UserWarning
            async with self._lock:
                span.tag(
                    AmqpSpan.TAG_CHANNEL_NUMBER, str(self._ch.channel_number)
                )
                fut: asyncio.Future = asyncio.Future()
                cb = partial(self._on_exchange_declareok, fut)
                self._ch.exchange_declare(
                    exchange=exchange,
                    exchange_type=exchange_type,
                    passive=passive,
                    durable=durable,
                    auto_delete=auto_delete,
                    internal=internal,
                    arguments=arguments,
                    callback=cb,
                )

                await asyncio.wait(
                    [fut, self._close_fut],
                    timeout=self.amqp.cfg.exchange_declare_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._close_fut.done():
                    self._close_fut.result()
                return fut.result()

    def _on_exchange_declareok(
        self, fut: asyncio.Future, _unused_frame: pika.frame.Method
    ) -> None:
        fut.set_result(_unused_frame)

    async def queue_declare(
        self,
        queue: str,
        passive: bool = False,
        durable: bool = False,
        exclusive: bool = False,
        auto_delete: bool = False,
        arguments: dict = None,
    ) -> pika.frame.Method:
        with wrap2span(
            name=AmqpSpan.NAME_DECLARE_QUEUE,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.amqp.app,
        ) as span:
            async with self._lock:
                span.tag(
                    AmqpSpan.TAG_CHANNEL_NUMBER, str(self._ch.channel_number)
                )
                fut: asyncio.Future = asyncio.Future()
                cb = partial(self._on_queue_declareok, fut)

                self._ch.queue_declare(
                    queue=queue,
                    passive=passive,
                    durable=durable,
                    exclusive=exclusive,
                    auto_delete=auto_delete,
                    arguments=arguments,
                    callback=cb,
                )

                await asyncio.wait(
                    [fut, self._close_fut],
                    timeout=self.amqp.cfg.queue_declare_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._close_fut.done():
                    self._close_fut.result()
                return fut.result()

    def _on_queue_declareok(
        self, fut: asyncio.Future, _unused_frame: pika.frame.Method
    ) -> None:
        fut.set_result(_unused_frame)

    async def queue_bind(
        self,
        queue: str,
        exchange: str,
        routing_key: Optional[str] = None,
        arguments: Optional[dict] = None,
    ) -> pika.frame.Method:
        with wrap2span(
            name=AmqpSpan.NAME_BIND,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.amqp.app,
        ) as span:
            async with self._lock:
                span.tag(
                    AmqpSpan.TAG_CHANNEL_NUMBER, str(self._ch.channel_number)
                )
                fut: asyncio.Future = asyncio.Future()
                cb = partial(self._on_bindok, fut)
                self._ch.queue_bind(
                    queue=queue,
                    exchange=exchange,
                    routing_key=routing_key,
                    arguments=arguments,
                    callback=cb,
                )
                await asyncio.wait(
                    [fut, self._close_fut],
                    timeout=self.amqp.cfg.bind_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._close_fut.done():
                    self._close_fut.result()
                return fut.result()

    def _on_bindok(
        self, fut: asyncio.Future, _unused_frame: pika.frame.Method
    ) -> None:
        fut.set_result(_unused_frame)

    async def qos(
        self,
        prefetch_size: int = 0,
        prefetch_count: int = 0,
        global_qos: bool = False,
    ) -> pika.frame.Method:
        with wrap2span(
            name=AmqpSpan.NAME_QOS,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.amqp.app,
        ) as span:
            async with self._lock:
                span.tag(
                    AmqpSpan.TAG_CHANNEL_NUMBER, str(self._ch.channel_number)
                )
                fut: asyncio.Future = asyncio.Future()
                cb = partial(self._on_basic_qos_ok, fut)
                self._ch.basic_qos(
                    prefetch_size=prefetch_size,
                    prefetch_count=prefetch_count,
                    global_qos=global_qos,
                    callback=cb,
                )
                await asyncio.wait(
                    [fut, self._close_fut],
                    timeout=self.amqp.cfg.bind_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._close_fut.done():
                    self._close_fut.result()
                return fut.result()

    def _on_basic_qos_ok(
        self, fut: asyncio.Future, _unused_frame: pika.frame.Method
    ) -> None:
        fut.set_result(_unused_frame)

    async def publish(
        self,
        exchange: str,
        routing_key: str,
        body: bytes,
        properties: Optional[pika.spec.BasicProperties] = None,
        mandatory: bool = False,
        propagate_trace: bool = True,
    ) -> None:
        with wrap2span(
            name=AmqpSpan.NAME_PUBLISH,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpOutSpan,
            app=self.amqp.app,
        ) as span:
            span.tag(AmqpSpan.TAG_CHANNEL_NUMBER, str(self._ch.channel_number))
            span.tag(AmqpSpan.TAG_EXCHANGE, str(exchange))
            span.tag(AmqpSpan.TAG_ROUTING_KEY, str(routing_key))
            span.tag(AmqpSpan.TAG_URL, self._conn.pika._masked_url)

            with timeout(self.amqp.cfg.publish_timeout):
                if not self._ch.is_closed or self.name is None:

                    if propagate_trace:
                        hdrs = span.to_headers()
                        if properties is None:
                            properties = pika.spec.BasicProperties(
                                headers=hdrs
                            )
                        elif properties.headers is None:
                            properties.headers = hdrs
                        else:
                            properties.headers = dict_merge(
                                properties.headers, hdrs
                            )

                    if self.amqp.cfg.log_out_props:
                        span.annotate(
                            AmqpSpan.ANN_OUT_PROPS, props2ann(properties)
                        )
                        span.annotate4adapter(
                            self.amqp.app.logger.ADAPTER_ZIPKIN,
                            AmqpSpan.ANN_OUT_PROPS,
                            self._json_encode(
                                {
                                    "properties": repr(
                                        {
                                            k: v
                                            for k, v in properties.__dict__.items()
                                            if v is not None
                                        }
                                    )
                                }
                            ),
                        )
                    if self.amqp.cfg.log_out_body:
                        _body = decode_bytes(body)
                        span.annotate(AmqpSpan.ANN_OUT_BODY, _body)
                        span.annotate4adapter(
                            self.amqp.app.logger.ADAPTER_ZIPKIN,
                            AmqpSpan.ANN_OUT_BODY,
                            self._json_encode({"message": _body}),
                        )

                    self._ch.basic_publish(
                        exchange, routing_key, body, properties, mandatory
                    )
                else:
                    while True:
                        ch = self.amqp.channel(self.name)
                        if ch is None:
                            await asyncio.sleep(0.1)
                            continue

                        return await ch.publish(
                            exchange,
                            routing_key,
                            body,
                            properties,
                            mandatory,
                            propagate_trace,
                        )

    async def consume(
        self,
        queue: str,
        on_message_callback: Callable[
            [bytes, pika.spec.Basic.Deliver, pika.spec.BasicProperties],
            Awaitable[None],
        ],
        auto_ack: bool = False,
        exclusive: bool = False,
        consumer_tag: Optional[str] = None,
        arguments: Optional[dict] = None,
    ) -> pika.frame.Method:
        with wrap2span(
            name=AmqpSpan.NAME_CONSUME,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.amqp.app,
        ) as span:
            async with self._lock:
                span.tag(
                    AmqpSpan.TAG_CHANNEL_NUMBER, str(self._ch.channel_number)
                )
                fut: asyncio.Future = asyncio.Future()
                cb = partial(self._on_basic_consume_ok, fut)
                self._consumer_tag = self._ch.basic_consume(
                    queue=queue,
                    on_message_callback=partial(
                        self._on_message_callback, on_message_callback
                    ),
                    auto_ack=auto_ack,
                    exclusive=exclusive,
                    consumer_tag=consumer_tag,
                    arguments=arguments,
                    callback=cb,
                )

                self.amqp.app.log_info('Consuming %s', queue)
                await asyncio.wait(
                    [fut, self._close_fut],
                    timeout=self.amqp.cfg.bind_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._close_fut.done():
                    self._close_fut.result()
                return fut.result()

    def _on_basic_consume_ok(
        self, fut: asyncio.Future, _unused_frame: pika.frame.Method
    ) -> None:
        fut.set_result(_unused_frame)

    def _on_message_callback(
        self,
        cb: Callable[
            [bytes, pika.spec.Basic.Deliver, pika.spec.BasicProperties],
            Awaitable[None],
        ],
        _unused_channel: pika.channel.Channel,
        basic_deliver: pika.spec.Basic.Deliver,
        properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        asyncio.ensure_future(
            self._async_on_message_callback(
                cb, _unused_channel, basic_deliver, properties, body
            )
        )

    async def _async_on_message_callback(
        self,
        cb: Callable[
            [bytes, pika.spec.Basic.Deliver, pika.spec.BasicProperties],
            Awaitable[None],
        ],
        _unused_channel: pika.channel.Channel,
        basic_deliver: pika.spec.Basic.Deliver,
        properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        _fix_properties(properties)
        headers = dict(properties.headers or {})
        with self.amqp.app.logger.span_from_headers(
            headers, cls=AmqpInSpan
        ) as span:
            span.name = AmqpSpan.NAME_MESSAGE
            span.kind = AmqpSpan.KIND_SERVER
            span.tag(AmqpSpan.TAG_URL, self._conn.pika._masked_url)
            span.tag(
                AmqpSpan.TAG_CHANNEL_NUMBER,
                str(_unused_channel.channel_number),
            )
            span.tag(AmqpSpan.TAG_EXCHANGE, basic_deliver.exchange)
            span.tag(AmqpSpan.TAG_ROUTING_KEY, basic_deliver.routing_key)

            if self.amqp.cfg.log_in_props:
                span.annotate(AmqpSpan.ANN_IN_PROPS, props2ann(properties))
                span.annotate4adapter(
                    self.amqp.app.logger.ADAPTER_ZIPKIN,
                    AmqpSpan.ANN_IN_PROPS,
                    self._json_encode(
                        {
                            "properties": repr(
                                {
                                    k: v
                                    for k, v in properties.__dict__.items()
                                    if v is not None
                                }
                            )
                        }
                    ),
                )
            if self.amqp.cfg.log_in_body:
                _body = decode_bytes(body)
                span.annotate(AmqpSpan.ANN_IN_BODY, _body)
                span.annotate4adapter(
                    self.amqp.app.logger.ADAPTER_ZIPKIN,
                    AmqpSpan.ANN_IN_BODY,
                    self._json_encode({"message": _body}),
                )

            token = ctx_span_set(span)
            try:
                await cb(body, basic_deliver, properties)
            except BaseException:
                raise
            finally:
                ctx_span_reset(token)

    async def ack(self, delivery_tag: int, multiple: bool = False) -> None:
        with wrap2span(
            name=AmqpSpan.NAME_ACK,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.amqp.app,
        ) as span:
            span.tag(AmqpSpan.TAG_CHANNEL_NUMBER, str(self._ch.channel_number))
            self._ch.basic_ack(delivery_tag=delivery_tag, multiple=multiple)

    async def nack(
        self, delivery_tag: int, multiple: bool = False, requeue: bool = True
    ) -> None:
        with wrap2span(
            name=AmqpSpan.NAME_NACK,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.amqp.app,
        ) as span:
            span.tag(AmqpSpan.TAG_CHANNEL_NUMBER, str(self._ch.channel_number))
            self._ch.basic_nack(
                delivery_tag=delivery_tag, multiple=multiple, requeue=requeue
            )

    async def cancel(
        self, consumer_tag: Optional[str] = None
    ) -> pika.frame.Method:
        with wrap2span(
            name=AmqpSpan.NAME_CANCEL,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.amqp.app,
        ) as span:
            async with self._lock:
                span.tag(
                    AmqpSpan.TAG_CHANNEL_NUMBER, str(self._ch.channel_number)
                )
                if consumer_tag is None:
                    consumer_tag = self._consumer_tag
                if consumer_tag is None:
                    raise UserWarning('consumer_tag is empty')
                fut: asyncio.Future = asyncio.Future()
                cb = partial(self._on_cancel_ok, fut)
                self._ch.basic_cancel(consumer_tag=consumer_tag, callback=cb)
                await asyncio.wait(
                    [fut, self._close_fut],
                    timeout=self.amqp.cfg.exchange_declare_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._close_fut.done():
                    self._close_fut.result()
                return fut.result()

    def _on_cancel_ok(
        self, fut: asyncio.Future, _unused_frame: pika.frame.Method
    ) -> None:
        fut.set_result(_unused_frame)


class Pika(Component):
    def __init__(
        self,
        cfg: PikaConfig,
        channel_factories: List[Callable[[], PikaChannel]],
    ):
        self.cfg = cfg
        self._channel_factories = channel_factories
        self._channels: List[PikaChannel] = []
        self._conn: Optional[_Connection] = None
        self._started = False

    @property
    def _masked_url(self) -> Optional[str]:
        if self.cfg.url is not None:
            return mask_url_pwd(self.cfg.url)
        return None

    async def prepare(self) -> None:
        if self.app is None:
            raise UserWarning('Unattached component')

        await self._connect(max_attempts=self.cfg.connect_max_attempts)

    async def _on_disconnect(self, conn: _Connection, err: Exception) -> None:
        self._channels = []
        self.app.log_err(err)
        await self._connect()

    async def _connect(self, max_attempts: Optional[int] = None) -> None:
        with wrap2span(
            name=AmqpSpan.NAME_PREPARE,
            kind=AmqpSpan.KIND_CLIENT,
            cls=AmqpSpan,
            app=self.app,
        ):
            attempt: int = 0
            while max_attempts is None or attempt < max_attempts:
                attempt += 1
                try:
                    self._conn = _Connection(self, self.cfg)
                    self.app.log_info("Connecting to %s", self._masked_url)
                    await self._conn.connect(self._on_disconnect)
                    self.app.log_info("Connected to %s", self._masked_url)
                    break
                except Exception as err:
                    self.app.log_err(err)
                    if self._conn is not None:
                        try:
                            await self._conn.close()
                        except Exception:  # nosec
                            pass
                    await asyncio.sleep(self.cfg.connect_retry_delay)

            if max_attempts is not None and attempt >= max_attempts:
                raise PrepareError(
                    "Could not connect to %s" % self._masked_url
                )

            await self._open_channels()

            if self._started:
                await asyncio.gather(*[ch.start() for ch in self._channels])

    async def _on_channel_close(
        self, ch: PikaChannel, pika_ch: pika.channel.Channel, err: Exception
    ) -> None:
        await ch.on_close(err)

    async def _open_channels(self) -> None:
        if self._conn is None:
            raise UserWarning
        self._channels = []
        corors = []
        for fn in self._channel_factories:
            ch = fn()
            if not isinstance(ch, PikaChannel):
                raise UserWarning()
            pch = await self._conn.channel(
                partial(self._on_channel_close, ch), ch.name
            )
            ch._init(self, pch)  # noqa
            self._channels.append(ch)
            corors.append(ch.prepare())
        await asyncio.gather(*corors)

    async def start(self) -> None:
        self._started = True

        await asyncio.gather(*[ch.start() for ch in self._channels])

    async def stop(self) -> None:
        await asyncio.gather(*[ch.stop() for ch in self._channels])

        if self._conn is not None:
            self.app.log_info("Disconnecting from %s", self._masked_url)
            await self._conn.close()

    async def health(self) -> None:
        pass

    def channel(self, name: str) -> Optional[PikaChannel]:
        for ch in self._channels:
            if ch.name == name:
                return ch
        return None


def props2ann(properties: pika.spec.BasicProperties) -> str:
    anns: List[str] = []
    for prop in (
        'content_type',
        'content_encoding',
        'headers',
        'delivery_mode',
        'priority',
        'correlation_id',
        'reply_to',
        'expiration',
        'message_id',
        'timestamp',
        'type',
        'user_id',
        'app_id',
        'cluster_id',
    ):
        attr: Any = getattr(properties, prop)
        if attr is not None:
            anns.append('%s: %s' % (prop, default_json_encode(attr)))

    return '\n'.join(anns)


def _fix_properties(properties: Optional[pika.spec.BasicProperties]) -> None:
    if sys.version_info < (3, 8):
        return

    if properties is None:
        return

    # Python >= 3.8 Fix
    #
    # Removed __str__ implementations from builtin types
    # bool, int, float, complex and few
    # classes from the standard library.
    # They now inherit __str__() from object.
    # As result, defining the __repr__() method in the
    # subclass of these classes will affect their string
    # representation. (Contributed by Serhiy Storchaka in bpo-36793.)
    # see: https://docs.python.org/3/whatsnew/3.8.html#changes-in-python-behavior

    class long(pika.compat.long):
        def __repr__(self) -> str:
            return str(int(self)) + 'L'

    def _fix_val(val: Any) -> Any:
        if isinstance(val, pika.compat.long):
            return long(val)
        if isinstance(val, list):
            for i in range(len(val)):
                val[i] = _fix_val(val[i])
        if isinstance(val, dict):
            for k, v in val.items():
                val[k] = _fix_val(v)
        return val

    for prop in properties.__dict__.keys():
        setattr(properties, prop, _fix_val(getattr(properties, prop)))
