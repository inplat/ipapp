__version__ = '0.0.0'
__build_stamp__ = 0

from . import app, error
from .app import BaseApplication
from .cli import main
from .component import Component
from .config import BaseConfig
from .logger import Span

__all__ = [
    'app',
    'error',
    'Component',
    'BaseApplication',
    'Span',
    'BaseConfig',
    'main',
]

for mod in (
    "ipapp.logger",
    "ipapp.logger.adapters.prometheus",
    "ipapp.logger.adapters.requests",
    "ipapp.logger.adapters.sentry",
    "ipapp.logger.adapters.zipkin",
    "ipapp.http",
    "ipapp.http.client",
    "ipapp.http.server",
    "ipapp.mq.pika",
):
    try:
        __import__(mod)
    except ImportError:
        pass
