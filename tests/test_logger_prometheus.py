import pytest
from aiohttp import ClientSession

from ipapp import Application
from ipapp.logger.adapters import (AdapterConfigurationError,
                                   PrometheusAdapter, PrometheusConfig)


async def test_success(unused_tcp_port):
    port = unused_tcp_port
    app = Application()
    app.logger.add(PrometheusConfig(port=port, hist_labels={
        'test_span': {
            'le': '1.1,Inf',  # le mapping to quantiles
            'tag1': 'some_tag1',
        }
    }))
    await app.start()

    with app.logger.span_new(name='test_span') as span:
        pass
    with app.logger.span_new(name='test_span') as span2:
        span2.tag('some_tag1', '123')

    with app.logger.span_new(name='test_span') as span3:
        span3.tag('some_tag1', '123')

    async with ClientSession() as sess:
        resp = await sess.get('http://127.0.0.1:%d/' % port)
        txt = await resp.text()

    assert 'test_span_bucket{le="1.1",tag1=""} 1.0' in txt
    assert 'test_span_bucket{le="+Inf",tag1=""} 1.0' in txt
    assert 'test_span_count{tag1=""} 1.0' in txt
    assert 'test_span_bucket{le="1.1",tag1="123"} 2.0' in txt
    assert 'test_span_bucket{le="+Inf",tag1="123"} 2.0' in txt
    assert 'test_span_count{tag1="123"} 2.0' in txt

    assert ('test_span_sum{tag1=""} %s' % span.duration) in txt
    assert ('test_span_sum{tag1="123"} %s' % (span2.duration
                                              + span3.duration)) in txt

    await app.stop()


async def test_errors():
    app = Application()
    lgr = app.logger
    adapter = PrometheusAdapter()
    with pytest.raises(AdapterConfigurationError):
        adapter.handle(lgr.span_new())

    with pytest.raises(AdapterConfigurationError):
        await adapter.stop()
