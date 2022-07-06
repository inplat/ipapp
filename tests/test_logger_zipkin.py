from typing import Dict, List, Optional

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import BaseModel

from ipapp import BaseApplication, BaseConfig
from ipapp.db.pg import PgSpan, Postgres, PostgresConfig
from ipapp.logger import Span
from ipapp.logger.adapters import AdapterConfigurationError
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.misc import json_encode


class EndpointModel(BaseModel):
    serviceName: str


class Annotation(BaseModel):
    value: str
    timestamp: int


class SpanModel(BaseModel):
    traceId: str
    name: str
    parentId: Optional[str]
    id: str
    timestamp: int
    duration: int
    debug: bool
    shared: bool
    tags: Dict[str, str]
    annotations: List[Annotation]
    localEndpoint: EndpointModel
    remoteEndpoint: EndpointModel


class ZipkinServer:
    def __init__(self):
        self.app = web.Application()
        self.app.router.add_post('/api/v2/spans', self.tracer_handle)
        self.server: Optional[TestServer] = None
        self.addr: Optional[str] = None
        self.spans: List[SpanModel] = []
        self.err: Optional[Exception] = None

    async def __aenter__(self):
        self.server = TestServer(self.app, port=None)
        await self.server.start_server(loop=self.app.loop)
        self.addr = 'http://127.0.0.1:%d/api/v2/spans' % self.server.port
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.server.close()
        if self.err is not None:
            raise self.err

    async def tracer_handle(self, request):
        try:
            for item in await request.json():
                self.spans.append(SpanModel(**item))
            return web.Response(text='', status=201)
        except Exception as err:
            self.err = err


async def test_success(
    loop,
):
    async with ZipkinServer() as zs:
        cfg = ZipkinConfig(name='123', addr=zs.addr)
        adapter = ZipkinAdapter(cfg)
        app = BaseApplication(BaseConfig())
        app.logger.add(adapter)
        lgr = app.logger
        await lgr.start()

        with lgr.span_new(name='t1', kind=Span.KIND_SERVER) as sp:
            sp.tag('tag', 'abc')
            sp.annotate('k1', 'val1', ts=123456)

            with pytest.raises(Exception):
                with sp.new_child('t2', Span.KIND_CLIENT):
                    raise Exception()

        with lgr.span_new(kind=Span.KIND_SERVER) as sp3:
            sp3.set_name4adapter(lgr.ADAPTER_ZIPKIN, '111')
            sp3.annotate4adapter(
                lgr.ADAPTER_ZIPKIN,
                PgSpan.ANN_RESULT,
                json_encode({'result': '123'}),
                ts=122211000000,
            )
            sp3.set_tag4adapter(
                lgr.ADAPTER_ZIPKIN,
                PgSpan.TAG_QUERY_NAME,
                'get_paym',
            )

        await lgr.stop()

    assert len(zs.spans) == 3

    span: SpanModel = zs.spans[1]
    span2: SpanModel = zs.spans[0]
    span3: SpanModel = zs.spans[2]
    assert span.name == 't1'
    assert span.tags == {'tag': 'abc'}
    assert span.annotations == [
        Annotation(value='val1', timestamp=123456000000)
    ]
    assert span.duration > 0
    assert span.timestamp > 0
    assert not span.debug
    assert span.shared

    assert span2.name == 't2'
    assert span2.tags == {
        'error': 'true',
        'error.class': 'Exception',
        'error.message': '',
    }

    assert 'raise Exception()' in span2.annotations[0].value

    assert span3.name == '111'
    assert span3.tags == {PgSpan.TAG_QUERY_NAME: 'get_paym'}
    assert span3.annotations == [
        Annotation(value='{"result": "123"}', timestamp=122211000000000000)
    ]


async def test_errors(
    loop,
):
    app = BaseApplication(BaseConfig())
    lgr = app.logger
    cfg = ZipkinConfig(name='123')
    adapter = ZipkinAdapter(cfg)

    with pytest.raises(AdapterConfigurationError):
        adapter.handle(lgr.span_new())

    with pytest.raises(AdapterConfigurationError):
        await adapter.stop()


@pytest.mark.parametrize(
    ['use_64bit_trace_id', 'trace_id_string_length'],
    [
        (True, 16),
        (False, 32),
    ],
)
async def test_zipkin_trace_id_size_settings(
    loop, use_64bit_trace_id: bool, trace_id_string_length: int
):
    app = BaseApplication(BaseConfig())
    lgr = app.logger
    cfg = ZipkinConfig(name='123', use_64bit_trace_id=use_64bit_trace_id)
    lgr.add(ZipkinAdapter(cfg))
    await lgr.start()
    with lgr.span_new(name='test_span') as span:
        assert len(span.trace_id) == trace_id_string_length


class Spans(BaseModel):
    span_db_connect: SpanModel
    span_xact: SpanModel
    span_conn: SpanModel
    span_cursor: SpanModel


_map_spans = {
    'db::connect': 'span_db_connect',
    'db::connection': 'span_conn',
    'db::query_one (select)': 'span_select',
    'db::xact (commited)': 'span_xact',
    'db::cursor (cur_log)': 'span_cursor',
}


async def test_db_cursor_span(loop, postgres_url: str):
    app = BaseApplication(BaseConfig())

    db_config = PostgresConfig(
        url=postgres_url,
        log_query=True,
        log_result=True,
    )
    db = Postgres(db_config)
    app.add('db', db)

    async with ZipkinServer() as zs:
        cfg = ZipkinConfig(name='123', addr=zs.addr)
        z_adapter = ZipkinAdapter(cfg)
        app.logger.add(z_adapter)
        await app.start()

        res_cursor = list()
        db = app.get('db')  # type: Postgres

        sql_cursor = (
            # fmt: off
            "SELECT"
            ""  " UNNEST($1::int[]) as a"
            ""  ",UNNEST($2::varchar[]) as b"
            # fmt: on
        )
        a = [10, 15, 105]
        b = ['dd', 'a', '7i']

        async with db.connection() as conn:
            async with conn.xact():
                cur = conn.cursor(
                    sql_cursor, a, b, prefetch=2, query_name='cur_log'
                )
                async for res in cur:
                    res_cursor.append(res)
                    assert res['a'] == a[len(res_cursor) - 1]
                    assert res['b'] == b[len(res_cursor) - 1]
        await app.stop()

    assert len(zs.spans) == 4

    spans = Spans(**{_map_spans[_span.name]: _span for _span in zs.spans})

    assert spans.span_xact.parentId == spans.span_conn.id

    assert spans.span_cursor.parentId == spans.span_xact.id
    assert spans.span_cursor.tags['db.query'] == 'cur_log'
    assert spans.span_cursor.annotations[1].value == json_encode(
        {"query": sql_cursor}
    )
    assert spans.span_cursor.annotations[2].value == json_encode(
        {"query_params": "[[10, 15, 105], [\"dd\", \"a\", \"7i\"]]"}
    )
    assert spans.span_cursor.annotations[3].value == json_encode(
        {"result": '3 rows'}
    )


async def test_db_cursor_span_breaked(loop, postgres_url: str):
    app = BaseApplication(BaseConfig())

    db_config = PostgresConfig(
        url=postgres_url,
        log_query=True,
        log_result=True,
    )
    db = Postgres(db_config)
    app.add('db', db)

    async with ZipkinServer() as zs:
        cfg = ZipkinConfig(name='123', addr=zs.addr)
        z_adapter = ZipkinAdapter(cfg)
        app.logger.add(z_adapter)
        await app.start()

        res_cursor = list()
        db = app.get('db')  # type: Postgres

        sql_cursor = (
            # fmt: off
            "SELECT"
            ""  " UNNEST($1::int[]) as a"
            ""  ",UNNEST($2::varchar[]) as b"
            # fmt: on
        )
        a = [10, 15, 105]
        b = ['dd', 'a', '7i']

        async with db.connection() as conn:
            async with conn.xact():
                cur = conn.cursor(
                    sql_cursor, a, b, prefetch=2, query_name='cur_log'
                )
                async for res in cur:
                    res_cursor.append(res)
                    assert res['a'] == a[len(res_cursor) - 1]
                    assert res['b'] == b[len(res_cursor) - 1]
                    break
        await app.stop()

    assert len(zs.spans) == 4

    spans = Spans(**{_map_spans[_span.name]: _span for _span in zs.spans})

    assert spans.span_xact.parentId == spans.span_conn.id

    assert spans.span_cursor.parentId == spans.span_xact.id
    assert spans.span_cursor.tags['db.query'] == 'cur_log'
    assert spans.span_cursor.tags['error'] == 'true'
    assert spans.span_cursor.tags['error.class'] == 'GeneratorExit'
    assert spans.span_cursor.annotations[1].value == json_encode(
        {"query": sql_cursor}
    )
    assert spans.span_cursor.annotations[2].value == json_encode(
        {"query_params": "[[10, 15, 105], [\"dd\", \"a\", \"7i\"]]"}
    )
    assert spans.span_cursor.annotations[3].value == json_encode(
        {"result": '1 rows'}
    )


async def test_db_cursor_prepared_breaked(loop, postgres_url: str):
    app = BaseApplication(BaseConfig())

    db_config = PostgresConfig(
        url=postgres_url,
        log_query=True,
        log_result=True,
    )
    db = Postgres(db_config)
    app.add('db', db)

    async with ZipkinServer() as zs:
        cfg = ZipkinConfig(name='123', addr=zs.addr)
        z_adapter = ZipkinAdapter(cfg)
        app.logger.add(z_adapter)
        await app.start()

        db = app.get('db')  # type: Postgres

        sql_cursor = (
            # fmt: off
            "SELECT"
            ""  " UNNEST($1::int[]) as a"
            ""  ",UNNEST($2::varchar[]) as b"
            # fmt: on
        )
        a = [10, 15, 105]
        b = ['dd', 'a', '7i']

        async with db.connection() as conn:
            st = await conn.prepare(
                sql_cursor,
                query_name='cur_prepared_breaked',
            )
            async with conn.xact():
                cur = st.cursor(a, b, prefetch=2)
                async for _ in cur:
                    break

        await app.stop()

    span_cursor = zs.spans[-1]
    assert span_cursor.tags['error'] == 'true'
    assert span_cursor.tags['db.query'] == 'cur_prepared_breaked'
    assert span_cursor.annotations[3].value == json_encode(
        {"result": '1 rows'}
    )
