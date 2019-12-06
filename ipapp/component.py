import asyncio
import logging

import ipapp.app  # noqa

from .error import GracefulExit

logger = logging.getLogger('ipapp')


def _raise_graceful_exit() -> None:  # pragma: no cover
    raise GracefulExit()


class Component(object):
    app: 'ipapp.app.BaseApplication'
    loop: asyncio.AbstractEventLoop

    async def prepare(self) -> None:
        raise NotImplementedError()

    async def start(self) -> None:
        raise NotImplementedError()

    async def stop(self) -> None:
        raise NotImplementedError()

    async def health(self) -> None:
        """
        Raises exception if not healthy
        :raises: Exception
        """
        raise NotImplementedError()
