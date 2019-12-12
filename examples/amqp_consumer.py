import logging
import sys

from ipapp import BaseApplication, BaseConfig, main
from ipapp.mq.pika import (
    Deliver,
    Pika,
    PikaChannel,
    PikaChannelConfig,
    PikaConfig,
    Properties,
)


class ConsumerConfig(PikaChannelConfig):
    queue: str = 'test'


class Config(BaseConfig):
    amqp: PikaConfig
    consumer: ConsumerConfig


class ConsumerChannel(PikaChannel):
    cfg: ConsumerConfig

    async def prepare(self) -> None:
        await self.queue_declare(self.cfg.queue, durable=True)
        await self.consume(self.cfg.queue, self._message)

    async def _message(
        self, body: bytes, deliver: Deliver, proprties: Properties
    ) -> None:
        await self.ack(deliver.delivery_tag)
        print('MESSAGE', body)
        print()


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add(
            'amqp', Pika(cfg.amqp, [lambda: ConsumerChannel(cfg.consumer)])
        )


if __name__ == "__main__":
    """
    Usage:

APP_AMQP_URL=amqp://guest:guest@localhost:9004/ \
APP_CONSUMER_QUEUE=myqueue \
APP_LOG_ZIPKIN_ENABLED=1 \
APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
python -m examples.amqp_consumer

    """
    logging.basicConfig(level=logging.INFO)

    import os

    print(
        '\n'.join(
            [
                '%s=%s' % (k, v)
                for k, v in os.environ.items()
                if k.startswith('APP_')
            ]
        )
    )

    main(sys.argv, '0', App, Config)
