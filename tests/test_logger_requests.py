import json
from typing import List

import asyncpg

from ipapp.app import BaseApplication, BaseConfig
from ipapp.http import HttpSpan
from ipapp.logger.adapters.requests import RequestsAdapter, RequestsConfig


async def get_requests(
    postgres_url: str, table_name: str, trace_id: str
) -> List[asyncpg.Record]:
    conn = await asyncpg.connect(postgres_url)
    query = (
        'SELECT * FROM {table_name} ' 'WHERE trace_id=$1 ORDER BY id'
    ).format(table_name=table_name)
    res = await conn.fetch(query, trace_id)
    await conn.close()
    return res


async def test_success(loop, postgres_url: str):
    table_name = '_requests_log_table'
    max_hdrs_length = 5
    max_body_length = 4

    cfg = RequestsConfig(
        dsn=postgres_url,
        db_table_name=table_name,
        max_hdrs_length=max_hdrs_length,
        max_body_length=max_body_length,
        send_max_count=2,
    )
    adapter = RequestsAdapter(cfg)
    app = BaseApplication(BaseConfig())
    app.logger.add(adapter)
    await app.start()

    req_url = 'http://host:port/'
    method = 'POST'
    req_hdrs = '123456'
    req_body = '123456789'
    resp_hdrs = 'h2'
    resp_body = 'b2'
    status_code = 200
    error = 'e1'
    tags = {'t1': '1', 't2': '2'}

    with app.logger.span_new(kind=HttpSpan.KIND_SERVER, cls=HttpSpan) as span:
        span.annotate4adapter(
            app.logger.ADAPTER_REQUESTS, HttpSpan.ANN_REQUEST_HDRS, req_hdrs
        )
        span.annotate4adapter(
            app.logger.ADAPTER_REQUESTS, HttpSpan.ANN_REQUEST_BODY, req_body
        )
        span.annotate4adapter(
            app.logger.ADAPTER_REQUESTS, HttpSpan.ANN_RESPONSE_HDRS, resp_hdrs
        )
        span.annotate4adapter(
            app.logger.ADAPTER_REQUESTS, HttpSpan.ANN_RESPONSE_BODY, resp_body
        )
        span.error(Exception(error))
        span.set_tag4adapter(
            app.logger.ADAPTER_REQUESTS,
            HttpSpan.TAG_HTTP_STATUS_CODE,
            status_code,
        )
        span.set_tag4adapter(
            app.logger.ADAPTER_REQUESTS, HttpSpan.TAG_HTTP_URL, req_url
        )
        span.set_tag4adapter(
            app.logger.ADAPTER_REQUESTS, HttpSpan.TAG_HTTP_METHOD, method
        )
        for k, v in tags.items():
            span.tag(k, v)

        with span.new_child(
            name='request2', kind=HttpSpan.KIND_CLIENT, cls=HttpSpan
        ) as span2:
            with span2.new_child(name='nonrequest'):
                pass

    await app.stop()

    rows = await get_requests(postgres_url, table_name, span.trace_id)
    assert len(rows) == 2
    row = rows[1]
    assert row['stamp_begin'].timestamp() == round(span.start_stamp, 6)
    assert row['stamp_end'].timestamp() == round(span.finish_stamp, 6)
    assert not row['is_out']
    assert row['url'] == req_url
    assert row['method'] == method
    assert row['req_hdrs'] == req_hdrs[:max_hdrs_length]
    assert row['req_body'] == req_body[:max_body_length]
    assert row['resp_hdrs'] == resp_hdrs[:max_hdrs_length]
    assert row['resp_body'] == resp_body[:max_body_length]
    assert row['status_code'] == status_code
    assert row['error'] == error
    assert json.loads(row['tags']) == tags

    row = rows[0]
    assert row['stamp_begin'].timestamp() == round(span2.start_stamp, 6)
    assert row['stamp_end'].timestamp() == round(span2.finish_stamp, 6)
    assert row['is_out']
    assert row['url'] is None
    assert row['method'] is None
    assert row['req_hdrs'] is None
    assert row['req_body'] is None
    assert row['resp_hdrs'] is None
    assert row['resp_body'] is None
    assert row['status_code'] is None
    assert row['error'] is None
    assert row['tags'] is None
