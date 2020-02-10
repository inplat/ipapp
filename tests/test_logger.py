from typing import List

import pytest

from ipapp import BaseApplication, BaseConfig, Span
from ipapp.logger import Logger
from ipapp.logger.adapters import AbcAdapter
from ipapp.misc import ctx_span_get


async def test_logger_invalid_adapter():

    app = BaseApplication(BaseConfig())
    lgr = app.logger
    with pytest.raises(UserWarning):
        lgr.add(object())


async def test_logger_adapter():
    class TestAdapter(AbcAdapter):
        started = False
        stopped = False
        handled: List[Span] = []

        async def start(self, logger: 'Logger'):
            self.started = True

        def handle(self, span: Span):
            self.handled.append(span)

        async def stop(self):
            self.stopped = True

    app = BaseApplication(BaseConfig())
    lgr = app.logger
    lgr.add(TestAdapter())

    adapter = lgr.adapters[0]
    assert isinstance(adapter, TestAdapter)

    await lgr.start()
    assert adapter.started

    with lgr.span_new() as span:
        span.tag('tag', 'abc')

    await lgr.stop()
    assert adapter.stopped

    assert len(adapter.handled) == 1
    assert span.start_stamp is not None
    assert span.finish_stamp is not None
    assert 'tag' in span.tags
    assert span.tags['tag'] == 'abc'


async def test_span_to_hdrs_non_skippable():
    app = BaseApplication(BaseConfig())
    lgr = app.logger

    with lgr.span_new() as span:
        assert span.to_headers() == {
            'X-B3-TraceId': span.trace_id,
            'X-B3-SpanId': span.id,
            'X-B3-Flags': '0',
            'X-B3-Sampled': '1',
        }

        with span.new_child() as span2:
            assert span2.to_headers() == {
                'X-B3-TraceId': span2.trace_id,
                'X-B3-SpanId': span2.id,
                'X-B3-ParentSpanId': span2.parent_id,
                'X-B3-Flags': '0',
                'X-B3-Sampled': '1',
            }


async def test_span_to_hdrs_skippable():
    app = BaseApplication(BaseConfig())
    lgr = app.logger

    with lgr.span_new() as span:
        span.skip()
        assert span.to_headers() == {
            'X-B3-TraceId': span.trace_id,
            'X-B3-SpanId': span.id,
            'X-B3-Flags': '0',
            'X-B3-Sampled': '0',
        }

        with span.new_child() as span2:
            assert span2.to_headers() == {
                'X-B3-TraceId': span2.trace_id,
                'X-B3-SpanId': span2.id,
                'X-B3-ParentSpanId': span2.parent_id,
                'X-B3-Flags': '0',
                'X-B3-Sampled': '0',
            }


async def test_span_from_hdrs_non_skippable():
    app = BaseApplication(BaseConfig())
    lgr = app.logger

    hdrs = {
        'X-B3-TraceId': 'f3d33b325b9458f1a18de5f226ea54d7',
        'X-B3-SpanId': 'ed8753e2153a20df',
        'X-B3-ParentSpanId': 'e8063f805ae1580f',
        'X-B3-Flags': '0',
        'X-B3-Sampled': '1',
    }

    with lgr.span_from_headers(hdrs) as span:
        assert span.trace_id == 'f3d33b325b9458f1a18de5f226ea54d7'
        assert span.id is not None
        assert span.parent_id == 'ed8753e2153a20df'
        assert not span._skip


async def test_span_from_hdrs_skippable():
    app = BaseApplication(BaseConfig())
    lgr = app.logger

    hdrs = {
        'X-B3-TraceId': 'f3d33b325b9458f1a18de5f226ea54d8',
        'X-B3-SpanId': 'ed8753e2153a20df',
        'X-B3-Sampled': '0',
    }

    with lgr.span_from_headers(hdrs) as span:
        assert span.trace_id == 'f3d33b325b9458f1a18de5f226ea54d8'
        assert span.id is not None
        assert span.parent_id == 'ed8753e2153a20df'
        assert span._skip


async def test_span_from_hdrs_new_1():
    app = BaseApplication(BaseConfig())
    lgr = app.logger

    with lgr.span_from_headers({}) as span:
        assert span.trace_id is not None
        assert span.id is not None
        assert span.parent_id is None
        assert not span._skip


async def test_span_from_hdrs_new_2():
    app = BaseApplication(BaseConfig())
    lgr = app.logger

    with lgr.span_from_headers(None) as span:
        assert span.trace_id is not None
        assert span.id is not None
        assert span.parent_id is None
        assert not span._skip


async def test_span_from_hdrs_1():
    app = BaseApplication(BaseConfig())
    lgr = app.logger

    hdrs = {'X-B3-TraceId': 'f3d33b325b9458f1a18de5f226ea54d8'}
    with lgr.span_from_headers(hdrs) as span:
        assert span.trace_id == 'f3d33b325b9458f1a18de5f226ea54d8'
        assert span.id is not None
        assert span.parent_id is None
        assert not span._skip


async def test_span_from_hdrs_2():
    app = BaseApplication(BaseConfig())
    lgr = app.logger

    hdrs = {'X-B3-TraceId': ''}
    with lgr.span_from_headers(hdrs) as span:
        assert span.trace_id is not None
        assert span.trace_id != ''
        assert span.id is not None
        assert span.parent_id is None
        assert not span._skip


async def test_span_skip():
    class TestAdapter(AbcAdapter):
        started = False
        stopped = False
        handled: List[Span] = []

        async def start(self, logger: 'Logger'):
            self.started = True

        def handle(self, span: Span):
            self.handled.append(span)

        async def stop(self):
            self.stopped = True

    app = BaseApplication(BaseConfig())
    lgr = app.logger
    lgr.add(TestAdapter())

    adapter = lgr.adapters[0]
    assert isinstance(adapter, TestAdapter)

    await lgr.start()
    assert adapter.started

    with lgr.span_new() as span:
        with span.new_child():
            pass
        span.skip()
        with span.new_child():
            pass

    assert len(adapter.handled) == 0

    await lgr.stop()
    assert adapter.stopped

    assert len(adapter.handled) == 0


async def test_span():
    class TestAdapter(AbcAdapter):
        name = 'test_adapter'
        started = False
        stopped = False
        handled: List[Span] = []

        async def start(self, logger: 'Logger'):
            self.started = True

        def handle(self, span: Span):
            self.handled.append(span)

        async def stop(self):
            self.stopped = True

    app = BaseApplication(BaseConfig())
    lgr = app.logger
    lgr.add(TestAdapter())

    adapter = lgr.adapters[0]
    assert isinstance(adapter, TestAdapter)

    await lgr.start()
    assert adapter.started

    exc = Exception('Err')
    exc2 = Exception('Err2')

    span = Span(lgr, '1234')
    assert span.duration == 0

    with lgr.span_new('t1', kind=Span.KIND_SERVER) as span:
        dur1 = span.duration
        assert dur1 > 0
        assert span.kind == span.KIND_SERVER
        assert span.name == 't1'
        with span.new_child(name='t2', kind=Span.KIND_CLIENT) as span2:
            assert span2.kind == span.KIND_CLIENT
            assert span2.name == 't2'
            assert span2.parent_id == span.id
            assert span2.parent is span

            try:
                raise exc
            except Exception as err:
                span2.error(err)

            try:
                with span2.new_child(
                    name='t3', kind=Span.KIND_CLIENT
                ) as span3:
                    assert span3.parent_id == span2.id
                    assert span3.parent is span2
                    raise exc2
            except Exception as err2:
                assert err2 is exc2

        assert span.duration > dur1

        span.set_name4adapter(TestAdapter.name, 'tt1')
        span.annotate('k1', '1', ts=1)
        span.annotate('k1', '2', ts=2)
        span.annotate4adapter(TestAdapter.name, 'k2', '2', ts=2)
        span.annotate4adapter(TestAdapter.name, 'k2', '3', ts=3)

        span.tag('tag1', '11')
        span.set_tag4adapter(TestAdapter.name, 'tag2', '22')
        span.set_tag4adapter(TestAdapter.name, 'tag3', '33')

    await lgr.stop()
    assert adapter.stopped

    assert len(adapter.handled) == 3

    span = adapter.handled[2]
    span2 = adapter.handled[1]
    span3 = adapter.handled[0]
    assert span.kind == span.KIND_SERVER
    assert span2.kind == span.KIND_CLIENT

    assert span.duration > 0

    # name
    assert span.name == 't1'
    assert span2.name == 't2'
    assert span.get_name4adapter(TestAdapter.name) == 'tt1'
    assert span.get_name4adapter(TestAdapter.name, merge=False) == 'tt1'
    assert span2.get_name4adapter(TestAdapter.name) == 't2'
    assert span2.get_name4adapter(TestAdapter.name, merge=False) is None

    # tags
    assert span.tags == {'tag1': '11'}
    assert span.get_tags4adapter(TestAdapter.name) == {
        'tag1': '11',
        'tag2': '22',
        'tag3': '33',
    }
    assert span.get_tags4adapter(TestAdapter.name, merge=False) == {
        'tag2': '22',
        'tag3': '33',
    }
    assert span.get_tags4adapter('unknown') == {'tag1': '11'}
    assert span.get_tags4adapter('unknown', merge=False) == {}

    # annotations
    assert span.annotations == {'k1': [('1', 1), ('2', 2)]}
    assert span.get_annotations4adapter(TestAdapter.name) == {
        'k1': [('1', 1), ('2', 2)],
        'k2': [('2', 2), ('3', 3)],
    }
    assert span.get_annotations4adapter(TestAdapter.name, merge=False) == {
        'k2': [('2', 2), ('3', 3)]
    }
    assert span.get_annotations4adapter('unknown...') == {
        'k1': [('1', 1), ('2', 2)]
    }
    assert span.get_annotations4adapter('unknown...', merge=False) == {}

    # log error
    assert span2.tags == {
        'error': 'true',
        'error.class': 'Exception',
        'error.message': 'Err',
    }
    assert 'traceback' in span2.annotations
    assert len(span2.annotations['traceback']) == 1
    assert isinstance(span2.annotations['traceback'][0][0], str)
    assert len(span2.annotations['traceback'][0][0]) > 0
    assert span2.get_error() is exc

    assert span3.tags == {
        'error': 'true',
        'error.class': 'Exception',
        'error.message': 'Err2',
    }
    assert 'traceback' in span3.annotations
    assert len(span3.annotations['traceback']) == 1
    assert isinstance(span3.annotations['traceback'][0][0], str)
    assert len(span3.annotations['traceback'][0][0]) > 0
    assert span3.get_error() is exc2

    assert 'Span' in str(span)
    assert 'ms' in str(span)


async def test_span_ctx():
    app = BaseApplication(BaseConfig())
    lgr = app.logger

    assert ctx_span_get() is None
    with lgr.span_new() as span1:
        assert ctx_span_get() is span1
        with span1.new_child() as span2:
            assert ctx_span_get() is span2
        assert ctx_span_get() is span1
    assert ctx_span_get() is None


async def test_trap():
    app = BaseApplication(BaseConfig())
    lgr = app.logger

    class ExSpan(Span):
        pass

    assert ctx_span_get() is None

    with app.logger.capture_span(ExSpan) as trap:
        with pytest.raises(UserWarning):
            assert trap.span is None
        with lgr.span_new(name='t1') as span1:
            with span1.new_child(name='t2', cls=ExSpan):
                pass
    assert trap.span is not None
    assert trap.span.name == 't2'


async def test_logger_span_callback():
    class TestAdapter(AbcAdapter):
        started = False
        stopped = False
        handled: List[Span] = []

        async def start(self, logger: 'Logger'):
            self.started = True

        def handle(self, span: Span):
            self.handled.append(span)

        async def stop(self):
            self.stopped = True

    def replace_tags(span: Span) -> None:
        for tag in span.tags.keys():
            span.tag(tag, '***')

    app = BaseApplication(BaseConfig())
    lgr = app.logger
    lgr.add(TestAdapter())

    adapter = lgr.adapters[0]
    assert isinstance(adapter, TestAdapter)
    lgr.add_before_handle_cb(replace_tags)

    await lgr.start()
    assert adapter.started

    with lgr.span_new() as span:
        span.tag('tag', 'abc')

    await lgr.stop()
    assert adapter.stopped

    assert len(adapter.handled) == 1
    assert span.start_stamp is not None
    assert span.finish_stamp is not None
    assert 'tag' in span.tags
    assert span.tags['tag'] == '***'
