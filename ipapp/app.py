import asyncio
import logging
import signal
import time
from typing import Any, Dict, List, Optional, Union

from .autoreload import _reload
from .component import Component
from .config import BaseConfig
from .error import GracefulExit, PrepareError
from .logger import Logger
from .misc import ctx_app_set

logger = logging.getLogger('ipapp')


RESTART = object()


def _raise_graceful_exit() -> None:  # pragma: no cover
    raise GracefulExit()


class BaseApplication(object):
    def __init__(self, cfg: BaseConfig) -> None:
        ctx_app_set(self)
        self.cfg = cfg
        self.loop = asyncio.get_event_loop()
        self._components: Dict[str, Component] = {}
        self._stop_deps: Dict[str, List[str]] = {}
        self._stopped: List[str] = []
        self.logger: Logger = Logger(self)
        self._version = ''
        self._build_stamp: float = 0.0
        self._start_stamp: Optional[float] = None
        self._shutdown_fut: asyncio.Future = asyncio.Future(loop=self.loop)

    @property
    def version(self) -> str:
        return self._version

    @property
    def build_stamp(self) -> float:
        return self._build_stamp

    @property
    def start_stamp(self) -> Optional[float]:
        return self._start_stamp

    def add(
        self,
        name: str,
        comp: Component,
        stop_after: Optional[List[str]] = None,
    ) -> None:
        if not isinstance(comp, Component):
            raise UserWarning()
        if name in self._components:
            raise UserWarning()
        if stop_after is not None:
            for cmp in stop_after:
                if cmp not in self._components:
                    raise UserWarning('Unknown component %s' % cmp)
        comp.loop = self.loop
        comp.app = self
        self._components[name] = comp
        self._stop_deps[name] = stop_after or []

    def get(self, name: str) -> Optional[Component]:
        if name in self._components:
            return self._components[name]
        return None

    def log_err(
        self, err: Union[str, BaseException], *args: Any, **kwargs: Any
    ) -> None:
        if not err:
            return
        if isinstance(err, BaseException):
            has_tb = (
                hasattr(err, '__traceback__') and err.__traceback__ is not None
            )
            if not has_tb:
                # for RPC
                if hasattr(err, 'trace') and isinstance(err.trace, str):  # type: ignore
                    logging.error(err.trace, *args, **kwargs)  # type: ignore
                    return

            logging.exception(err, *args, **kwargs)
        else:
            logging.error(err, *args, **kwargs)

    def log_warn(self, warn: str, *args: Any, **kwargs: Any) -> None:
        logging.warning(warn, *args, **kwargs)

    def log_info(self, info: str, *args: Any, **kwargs: Any) -> None:
        logging.info(info, *args, **kwargs)

    def log_debug(self, debug: str, *args: Any, **kwargs: Any) -> None:
        logging.debug(debug, *args, **kwargs)

    async def _stop_logger(self) -> None:
        self.log_info("Shutting down tracer")
        await self.logger.stop()

    def run(self) -> int:
        try:
            try:
                self.loop.run_until_complete(self.start())
            except PrepareError as e:
                self.log_err(e)
                return 1
            except (KeyboardInterrupt, GracefulExit):  # pragma: no cover
                return 1

            try:
                self.loop.add_signal_handler(
                    signal.SIGINT, _raise_graceful_exit
                )
                self.loop.add_signal_handler(
                    signal.SIGTERM, _raise_graceful_exit
                )
            except NotImplementedError:  # pragma: no cover
                # add_signal_handler is not implemented on Windows
                pass

            try:
                self.loop.run_until_complete(
                    asyncio.wait([self._shutdown_fut], loop=self.loop)
                )
                print('*' * 80)
                print(self._shutdown_fut.result())
            except GracefulExit:  # pragma: no cover
                pass

            return 0
        finally:
            try:
                self.loop.run_until_complete(self.stop())
                if hasattr(self.loop, 'shutdown_asyncgens'):
                    self.loop.run_until_complete(
                        self.loop.shutdown_asyncgens()
                    )
                self.loop.close()
            finally:
                if (
                    self._shutdown_fut.done()
                    and self._shutdown_fut.result() is RESTART
                ):
                    _reload()

    async def start(self) -> None:
        ctx_app_set(self)
        self.log_info('Configuring logger')
        await self.logger.start()

        self.log_info('Prepare for start')

        await asyncio.gather(
            *[comp.prepare() for comp in self._components.values()],
            loop=self.loop,
        )

        self.log_info('Starting...')
        self._start_stamp = time.time()
        await asyncio.gather(
            *[comp.start() for comp in self._components.values()],
            loop=self.loop,
        )

        self.log_info('Running...')

    async def stop(self) -> None:
        self.log_info('Shutting down...')
        for comp_name in self._components:
            await self._stop_comp(comp_name)
        await self._stop_logger()
        await self.loop.shutdown_asyncgens()

    def shutdown(self) -> None:
        self._shutdown_fut.set_result(None)

    def restart(self) -> None:
        self._shutdown_fut.set_result(RESTART)

    async def _stop_comp(self, name: str) -> None:
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
