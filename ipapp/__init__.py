__version__ = '0.0.1'

from . import app, error
from .app import Application
from .component import Component
from .logger import Span

__all__ = [
    'app',
    'error',
    'Component',
    'Application',
    'Span',
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
