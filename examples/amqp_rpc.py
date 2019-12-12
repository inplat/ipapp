import logging
import sys

from iprpc import method

from ipapp import BaseApplication, BaseConfig, main
from ipapp.mq.pika import Pika, PikaConfig
from ipapp.rpc.mq.pika import RpcServerChannel, RpcServerChannelConfig


class Config(BaseConfig):
    amqp: PikaConfig
    amqp_rpc: RpcServerChannelConfig


class Api:
    @method()
    async def test(self) -> str:
        print('EXEC')
        1 / 0
        return 'OK'


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add(
            'amqp',
            Pika(cfg.amqp, [lambda: RpcServerChannel(Api(), cfg.amqp_rpc)]),
        )


if __name__ == "__main__":
    """
    Usage:

APP_AMQP_URL=amqp://guest:guest@localhost:9004/ \
APP_AMQP_RPC_QUEUE=rpcqueue \
APP_LOG_ZIPKIN_ENABLED=1 \
APP_LOG_ZIPKIN_ADDR=http://127.0.0.1:9002/api/v2/spans \
python -m examples.amqp_rpc

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
