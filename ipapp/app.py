import asyncio
import logging
import signal
import time
from typing import Dict, Optional, Callable

from .error import PrepareError, GracefulExit
from .logger import Logger
from .misc import ctx_app_set

logger = logging.getLogger('ipapp')


def _raise_graceful_exit():  # pragma: no cover
    raise GracefulExit()


class Component(object):
    app: 'Application'

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


class Application(object):
    def __init__(self, on_start: Optional[Callable] = None) -> None:
        ctx_app_set(self)
        self.loop = asyncio.get_event_loop()
        self._components: Dict[str, Component] = {}
        self._stop_deps: dict = {}
        self._stopped: list = []
        self.logger: Logger = Logger(self)
        self.on_start: Optional[Callable] = on_start
        self._version = ''
        self._build_stamp: float = 0.
        self._start_stamp: Optional[float] = None

    @property
    def version(self) -> str:
        return self._version

    @property
    def build_stamp(self) -> float:
        return self._build_stamp

    @property
    def start_stamp(self) -> float:
        return self._start_stamp

    def add(self, name: str, comp: Component,
            stop_after: list = None):
        if not isinstance(comp, Component):
            raise UserWarning()
        if name in self._components:
            raise UserWarning()
        if stop_after:
            for cmp in stop_after:
                if cmp not in self._components:
                    raise UserWarning('Unknown component %s' % cmp)
        comp.loop = self.loop
        comp.app = self
        self._components[name] = comp
        self._stop_deps[name] = stop_after

    def get(self, name: str) -> Optional[Component]:
        if name in self._components:
            return self._components[name]
        return None

    def log_err(self, err, *args, **kwargs):
        if not err:
            return
        if isinstance(err, BaseException):
            logging.exception(err, *args, **kwargs)
        else:
            logging.error(err, *args, **kwargs)

    def log_warn(self, warn, *args, **kwargs):
        logging.warning(warn, *args, **kwargs)

    def log_info(self, info, *args, **kwargs):
        logging.info(info, *args, **kwargs)

    def log_debug(self, debug, *args, **kwargs):
        logging.debug(debug, *args, **kwargs)

    async def _stop_logger(self):
        self.log_info("Shutting down tracer")
        await self.logger.stop()

    def run(self) -> int:
        try:
            try:
                self.loop.run_until_complete(self.start())
            except PrepareError as e:
                self.log_err(e)
                return 1
            except KeyboardInterrupt:  # pragma: no cover
                return 1

            try:
                self.loop.add_signal_handler(signal.SIGINT,
                                             _raise_graceful_exit)
                self.loop.add_signal_handler(signal.SIGTERM,
                                             _raise_graceful_exit)
            except NotImplementedError:  # pragma: no cover
                # add_signal_handler is not implemented on Windows
                pass

            try:
                self.loop.run_forever()
            except GracefulExit:  # pragma: no cover
                pass

            return 0
        finally:
            self.loop.run_until_complete(self.stop())
            if hasattr(self.loop, 'shutdown_asyncgens'):
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    async def start(self):
        ctx_app_set(self)
        self.log_info('Configuring logger')
        await self.logger.start()

        self.log_info('Prepare for start')

        await asyncio.gather(*[comp.prepare()
                               for comp in self._components.values()],
                             loop=self.loop)

        self.log_info('Starting...')
        self._start_stamp = time.time()
        await asyncio.gather(*[comp.start()
                               for comp in self._components.values()],
                             loop=self.loop)

        self.log_info('Running...')

    async def stop(self):
        self.log_info('Shutting down...')
        for comp_name in self._components:
            await self._stop_comp(comp_name)
        await self._stop_logger()
        await self.loop.shutdown_asyncgens()

    async def _stop_comp(self, name):
        if name in self._stopped:
            return
        if name in self._stop_deps and self._stop_deps[name]:
            for dep_name in self._stop_deps[name]:
                await self._stop_comp(dep_name)
        await self._components[name].stop()
        self._stopped.append(name)

    async def health(self) -> Dict[str, Optional[BaseException]]:
        result: Dict[str, Optional[BaseException]] = {}
        for name, cmp in self._components.items():
            try:
                await cmp.health()
                result[name] = None
            except BaseException as err:
                result[name] = err
        return result
