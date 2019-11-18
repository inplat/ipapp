from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseSettings

import ipapp.logger  # noqa
from ..span import Span
from ...error import ConfigurationError


class AdapterConfigurationError(ConfigurationError):
    pass


class AbcConfig(BaseSettings):
    pass


class AbcAdapter(ABC):
    name: str = ''
    cfg: Optional[AbcConfig] = None

    @abstractmethod
    async def start(self, logger: 'ipapp.logger.Logger', cfg: AbcConfig):
        pass

    @abstractmethod
    def handle(self, span: Span):
        pass

    @abstractmethod
    async def stop(self):
        pass


__all__ = [
    "AbcConfig",
    "AbcAdapter",

]
