import asyncio
from typing import Any, Callable, List, Tuple

from async_timeout import timeout as async_timeout

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
        with async_timeout(timeout):
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
            self, body: bytes, deliver: Deliver, proprties: Properties
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
