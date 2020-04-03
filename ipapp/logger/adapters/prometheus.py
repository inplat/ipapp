import re
from typing import Dict

from prometheus_client import Histogram, start_http_server
from pydantic import Field

import ipapp.logger  # noqa

from ...misc import dict_merge
from ..span import Span
from ._abc import AbcAdapter, AbcConfig, AdapterConfigurationError

LabelsCfg = Dict[str, Dict[str, str]]

RE_P8S_METRIC_NAME = re.compile(r'[^a-zA-Z0-9_]')

DEFAULT_LE = (
    '0.005,0.01,0.025,0.05,0.075,0.1,0.25,0.5,0.75,'
    '1.0,2.5,5.0,7.5,10.0,60.0,Inf'
)

DEFAULT_HISTOGRAM_LABELS: LabelsCfg = {  # {name: {label: tag}, }
    'http_in': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'route': 'http.route',
        'host': 'http.host',
        'method': 'http.method',
        'status_code': 'http.status_code',
        'error': 'error.class',
    },
    'http_out': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'host': 'http.host',
        'method': 'http.method',
        'status_code': 'http.status_code',
        'error': 'error.class',
    },
    'db_connection': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
        'free': 'db.pool.free',
    },
    'db_xact_commited': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
    },
    'db_xact_reverted': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
    },
    'db_execute': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
        'query': 'db.query',
    },
    'db_callproc': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
        'query': 'db.proc',
    },
    'db_callfunc': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
        'query': 'db.func',
    },
    'db_prepare': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
        'query': 'db.query',
    },
    'db_execute_prepared': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
        'query': 'db.query',
    },
    'db_fetch': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
        'query': 'db.query',
    },
    'rpc_in': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
        'method': 'rpc.method',
        'code': 'rpc.code',
    },
    'rpc_out': {
        'le': DEFAULT_LE,  # le mapping to quantiles
        'error': 'error.class',
        'method': 'rpc.method',
        'code': 'rpc.code',
    },
}
DEFAULT_HISTOGRAM_DOCS = {
    'http_in': 'Incoming HTTP request',
    'http_out': 'Outgoing HTTP request',
}


class PrometheusConfig(AbcConfig):
    addr: str = Field(
        "0.0.0.0", description="Адрес публикации Prometheus метрик",  # nosec
    )
    port: int = Field(9213, description="Порт публикации Prometheus метрик")
    hist_labels: LabelsCfg = {}
    hist_docs: Dict[str, str] = {}


class PrometheusAdapter(AbcAdapter):
    name = 'prometheus'
    cfg: PrometheusConfig

    def __init__(self, cfg: PrometheusConfig) -> None:
        self.cfg = cfg
        self.p8s_hists: Dict[str, Histogram] = {}
        self.p8s_hist_labels: LabelsCfg = {}
        self.p8s_hist_docs: Dict[str, str] = {}

    async def start(self, logger: 'ipapp.logger.Logger') -> None:
        self.p8s_hists = {}  # Histograms

        self.p8s_hist_labels = dict_merge(
            DEFAULT_HISTOGRAM_LABELS, self.cfg.hist_labels
        )
        self.p8s_hist_docs = dict_merge(
            DEFAULT_HISTOGRAM_DOCS, self.cfg.hist_docs
        )

        for hist_name, labels_cfg in self.p8s_hist_labels.items():
            buckets = Histogram.DEFAULT_BUCKETS
            labelnames = []
            if labels_cfg:
                for label in labels_cfg.keys():
                    if label == 'le':
                        buckets = labels_cfg['le'].split(',')
                    else:
                        labelnames.append(label)

            doc = self.p8s_hist_docs.get(hist_name) or hist_name
            self.p8s_hists[hist_name] = Histogram(
                hist_name, doc, labelnames=labelnames, buckets=buckets
            )
        start_http_server(self.cfg.port, self.cfg.addr)

    def handle(self, span: Span) -> None:
        if self.cfg is None:
            raise AdapterConfigurationError(
                '%s is not configured' % self.__class__.__name__
            )

        name = span.get_name4adapter(self.name)
        tags = span.get_tags4adapter(self.name)

        if name in self.p8s_hists:
            hist: Histogram = self.p8s_hists[name]
            labels_cfg = self.p8s_hist_labels.get(name)
            labels = {}
            if labels_cfg:
                for label, tag in labels_cfg.items():
                    if label != 'le':
                        tag_val = tags.get(tag)
                        labels[label] = tag_val or ''
            if labels:
                hist = hist.labels(**labels)
            hist.observe(span.duration)

    async def stop(self) -> None:
        if self.cfg is None:
            raise AdapterConfigurationError(
                '%s is not configured' % self.__class__.__name__
            )
