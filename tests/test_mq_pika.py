import asyncio
from typing import Any, Callable, List, Tuple

from async_timeout import timeout as async_timeout
from pika.spec import BasicProperties

from ipapp import BaseApplication, BaseConfig
from ipapp.mq.pika import (
    Deliver,
    Pika,
    PikaChannel,
    PikaChannelConfig,
    PikaConfig,
    Properties,
)


async def wait_for(fn: Callable, timeout=60) -> Any:
    err = None
    try:
        async with async_timeout(timeout):
            while True:
                try:
                    if asyncio.iscoroutine(fn):
                        res = await fn()
                    else:
                        res = fn()

                    if not res:
                        raise Exception('Not ready')
                    return res
                except Exception as e:
                    err = e
                await asyncio.sleep(0.05)
    except asyncio.TimeoutError:
        if err is not None:
            raise err
        else:
            raise


async def test_pika(rabbitmq_url):

    messages: List[Tuple[bytes]] = []

    class TestPubChg(PikaChannel):
        name = 'pub'

    class TestCnsChg(PikaChannel):
        name = 'sub'

        async def prepare(self) -> None:
            await self.exchange_declare('myexchange', durable=False)
            await self.queue_declare('myqueue', durable=False)
            await self.queue_bind('myqueue', 'myexchange', '')
            await self.qos(prefetch_count=1)

        async def start(self) -> None:
            await self.consume('myqueue', self.message)

        async def message(
            self, body: bytes, deliver: Deliver, properties: Properties
        ) -> None:
            await self.ack(delivery_tag=deliver.delivery_tag)
            messages.append((body,))

    app = BaseApplication(BaseConfig())
    app.add(
        'mq',
        Pika(
            PikaConfig(url=rabbitmq_url),
            [
                lambda: TestPubChg(PikaChannelConfig()),
                lambda: TestCnsChg(PikaChannelConfig()),
            ],
        ),
    )
    await app.start()
    mq: Pika = app.get('mq')  # type: ignore

    await mq.channel('pub').publish('myexchange', '', 'testmsg')

    await wait_for(lambda: len(messages) > 0)
    assert messages == [(b'testmsg',)]

    await app.stop()


async def test_dead_letter_exchange(rabbitmq_url):
    messages: List[Tuple[bytes]] = []

    class TestPubChg(PikaChannel):
        name = 'pub'

        async def prepare(self) -> None:
            await self.exchange_declare('myexchange1', durable=False)
            await self.queue_declare('myqueue1', durable=False)
            await self.queue_bind('myqueue1', 'myexchange1', '')

            q = await self.queue_declare(
                '',
                exclusive=True,
                arguments={
                    'x-dead-letter-exchange': '',
                    'x-dead-letter-routing-key': 'myqueue1',
                },
            )
            self.queue = q.method.queue

        async def send(self, body: bytes, expiration: str):
            await self.publish(
                '', self.queue, body, BasicProperties(expiration=expiration)
            )

    class TestCnsChg(PikaChannel):
        name = 'sub'

        async def prepare(self) -> None:
            await self.qos(prefetch_count=1)

        async def start(self) -> None:
            await self.consume('myqueue1', self.message)

        async def message(
            self, body: bytes, deliver: Deliver, properties: Properties
        ) -> None:
            await self.ack(delivery_tag=deliver.delivery_tag)
            messages.append((body, properties))

    app = BaseApplication(BaseConfig())
    app.add(
        'mq',
        Pika(
            PikaConfig(url=rabbitmq_url),
            [
                lambda: TestPubChg(PikaChannelConfig()),
                lambda: TestCnsChg(PikaChannelConfig()),
            ],
        ),
    )
    await app.start()
    mq: Pika = app.get('mq')  # type: ignore

    await mq.channel('pub').send(b'testmsg', '1')

    await wait_for(lambda: len(messages) > 0)

    assert messages[0][1].headers['x-death'][0]['count'] == 1
    assert repr(messages[0][1].headers['x-death'][0]['count']) == '1L'

    await app.stop()
