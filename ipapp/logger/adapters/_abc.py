from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, Field

import ipapp.logger  # noqa

from ...error import ConfigurationError
from ..span import Span


class AdapterConfigurationError(ConfigurationError):
    pass


class AbcConfig(BaseModel):
    enabled: bool = Field(False, description="Включение логгера")


class AbcAdapter(ABC):
    name: str = ''
    cfg: Optional[AbcConfig] = None

    @abstractmethod
    async def start(self, logger: 'ipapp.logger.Logger') -> None:
        pass

    @abstractmethod
    def handle(self, span: Span) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass


__all__ = [
    "AbcConfig",
    "AbcAdapter",
]
