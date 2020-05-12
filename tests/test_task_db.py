import time
import uuid
from asyncio import Future, wait_for
from datetime import datetime, timezone

import asyncpg

from ipapp import BaseApplication, BaseConfig
from ipapp.db.pg import Postgres
from ipapp.rpc import method
from ipapp.task.db import (
    CREATE_TABLE_QUERY,
    STATUS_CANCELED,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_SUCCESSFUL,
    Retry,
    TaskManager,
    TaskManagerConfig,
)


async def connect(postgres_url) -> asyncpg.Connection:
    conn = await asyncpg.connect(postgres_url)
    await Postgres._conn_init(conn)
    return conn


async def prepare(postgres_url) -> str:
    test_schema_name = 'testdbtm'
    conn = await connect(postgres_url)
    await conn.execute('DROP SCHEMA IF EXISts %s CASCADE' % test_schema_name)
    await conn.execute('CREATE SCHEMA %s' % test_schema_name)
    await conn.execute(CREATE_TABLE_QUERY.format(schema=test_schema_name))
    await conn.close()
    return test_schema_name


async def get_tasks_pending(postgres_url, schema) -> list:
    conn = await connect(postgres_url)
    res = await conn.fetch(
        'SELECT * FROM %s.task_pending ORDER BY id' % schema
    )
    await conn.close()
    return res


async def get_tasks_arch(postgres_url, schema) -> list:
    conn = await connect(postgres_url)
    res = await conn.fetch('SELECT * FROM %s.task_arch ORDER BY id' % schema)
    await conn.close()
    return res


async def get_tasks_by_reference(postgres_url, schema, reference) -> list:
    conn = await connect(postgres_url)
    res = await conn.fetch(
        'SELECT * FROM %s.task WHERE reference=$1 ORDER BY id' % schema,
        reference,
    )
    await conn.close()
    return res


async def get_tasks_log(postgres_url, schema) -> list:
    conn = await connect(postgres_url)
    res = await conn.fetch('SELECT * FROM %s.task_log ORDER BY id' % schema)
    await conn.close()
    return res


async def wait_no_pending(postgres_url, schema):
    start = time.time()
    while time.time() < start + 10:
        tasks = await get_tasks_pending(postgres_url, schema)
        if len(tasks) == 0:
            return
    raise TimeoutError()


async def test_success(postgres_url):
    test_schema_name = await prepare(postgres_url)

    fut = Future()

    class Api:
        @method()
        async def test(self, arg):
            fut.set_result(arg)
            return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            Api(),
            TaskManagerConfig(db_url=postgres_url, db_schema=test_schema_name),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    await tm.schedule(Api.test, {'arg': 123}, eta=time.time() + 3)
    tasks = await get_tasks_pending(postgres_url, test_schema_name)
    assert len(tasks) == 1
    assert tasks[0]['name'] == 'test'
    assert tasks[0]['params'] == {'arg': 123}
    assert tasks[0]['eta'] > datetime.now(tz=timezone.utc)
    assert tasks[0]['last_stamp'] < datetime.now(tz=timezone.utc)
    assert tasks[0]['status'] == STATUS_PENDING
    assert tasks[0]['retries'] is None

    res = await wait_for(fut, 10)
    assert res == 123

    tasks = await get_tasks_arch(postgres_url, test_schema_name)
    assert len(tasks) == 1
    assert tasks[0]['name'] == 'test'
    assert tasks[0]['params'] == {'arg': 123}
    assert tasks[0]['eta'] < datetime.now(tz=timezone.utc)
    assert tasks[0]['last_stamp'] < datetime.now(tz=timezone.utc)
    assert tasks[0]['status'] == STATUS_SUCCESSFUL
    assert tasks[0]['retries'] == 0

    logs = await get_tasks_log(postgres_url, test_schema_name)
    assert len(logs) == 1
    assert logs[0]['eta'] < datetime.now(tz=timezone.utc)
    assert logs[0]['started'] < datetime.now(tz=timezone.utc)
    assert logs[0]['finished'] < datetime.now(tz=timezone.utc)
    assert logs[0]['result'] == 123
    assert logs[0]['error'] is None
    assert logs[0]['traceback'] is None

    assert len(await get_tasks_pending(postgres_url, test_schema_name)) == 0

    await app.stop()


async def test_reties_success(postgres_url):
    test_schema_name = await prepare(postgres_url)

    fut = Future()

    class Api:
        attempts = 0

        @method()
        async def test(self, arg):
            Api.attempts += 1
            if Api.attempts <= 2:
                raise Retry(Exception('Attempt %s' % Api.attempts))

            fut.set_result(arg)
            return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            Api(),
            TaskManagerConfig(db_url=postgres_url, db_schema=test_schema_name),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    await tm.schedule(Api.test, {'arg': 234}, max_retries=2, retry_delay=0.2)

    res = await wait_for(fut, 10)
    assert res == 234

    logs = await get_tasks_log(postgres_url, test_schema_name)
    assert len(logs) == 3

    assert logs[0]['eta'] < datetime.now(tz=timezone.utc)
    assert logs[0]['started'] < datetime.now(tz=timezone.utc)
    assert logs[0]['finished'] < datetime.now(tz=timezone.utc)
    assert logs[0]['result'] is None
    assert logs[0]['error'] == "Attempt 1"
    assert logs[0]['traceback'] is not None

    assert logs[1]['eta'] < datetime.now(tz=timezone.utc)
    assert logs[1]['started'] < datetime.now(tz=timezone.utc)
    assert logs[1]['finished'] < datetime.now(tz=timezone.utc)
    assert logs[1]['result'] is None
    assert logs[1]['error'] == "Attempt 2"
    assert logs[1]['traceback'] is not None

    assert logs[2]['eta'] < datetime.now(tz=timezone.utc)
    assert logs[2]['started'] < datetime.now(tz=timezone.utc)
    assert logs[2]['finished'] < datetime.now(tz=timezone.utc)
    assert logs[2]['result'] == 234
    assert logs[2]['error'] is None
    assert logs[2]['traceback'] is None

    assert len(await get_tasks_pending(postgres_url, test_schema_name)) == 0

    await app.stop()


async def test_reties_error(postgres_url):
    test_schema_name = await prepare(postgres_url)

    fut = Future()

    class Api:
        attempts = 0

        @method(name='someTest')
        async def test(self, arg):

            Api.attempts += 1
            if Api.attempts <= 2:
                raise Retry(Exception('Attempt %s' % Api.attempts))

            fut.set_result(arg)
            return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            Api(),
            TaskManagerConfig(db_url=postgres_url, db_schema=test_schema_name),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    await tm.schedule(
        'someTest',
        {'arg': 345},
        eta=datetime.now(tz=timezone.utc),
        max_retries=1,
        retry_delay=0.2,
    )

    await wait_no_pending(postgres_url, test_schema_name)

    tasks = await get_tasks_arch(postgres_url, test_schema_name)
    assert len(tasks) == 1
    assert tasks[0]['name'] == 'someTest'
    assert tasks[0]['params'] == {'arg': 345}
    assert tasks[0]['eta'] < datetime.now(tz=timezone.utc)
    assert tasks[0]['last_stamp'] < datetime.now(tz=timezone.utc)
    assert tasks[0]['status'] == STATUS_ERROR
    assert tasks[0]['retries'] == 1

    logs = await get_tasks_log(postgres_url, test_schema_name)
    assert len(logs) == 2

    assert logs[0]['eta'] < datetime.now(tz=timezone.utc)
    assert logs[0]['started'] < datetime.now(tz=timezone.utc)
    assert logs[0]['finished'] < datetime.now(tz=timezone.utc)
    assert logs[0]['result'] is None
    assert logs[0]['error'] == "Attempt 1"
    assert logs[0]['traceback'] is not None

    assert logs[1]['eta'] < datetime.now(tz=timezone.utc)
    assert logs[1]['started'] < datetime.now(tz=timezone.utc)
    assert logs[1]['finished'] < datetime.now(tz=timezone.utc)
    assert logs[1]['result'] is None
    assert logs[1]['error'] == "Attempt 2"
    assert logs[1]['traceback'] is not None

    assert len(await get_tasks_pending(postgres_url, test_schema_name)) == 0

    await app.stop()


async def test_tasks_by_ref(postgres_url):
    test_schema_name = await prepare(postgres_url)

    fut = Future()

    class Api:
        @method()
        async def test(self, arg):
            fut.set_result(arg)
            return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            Api(),
            TaskManagerConfig(db_url=postgres_url, db_schema=test_schema_name),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    ref = str(uuid.uuid4())

    await tm.schedule(
        Api.test, {'arg': 123}, eta=time.time() + 3, reference=ref
    )
    tasks = await get_tasks_by_reference(postgres_url, test_schema_name, ref)
    assert len(tasks) == 1
    assert tasks[0]['name'] == 'test'
    assert tasks[0]['params'] == {'arg': 123}
    assert tasks[0]['status'] == STATUS_PENDING

    res = await wait_for(fut, 10)
    assert res == 123

    tasks = await get_tasks_by_reference(postgres_url, test_schema_name, ref)
    assert len(tasks) == 1
    assert tasks[0]['name'] == 'test'
    assert tasks[0]['status'] == STATUS_SUCCESSFUL

    await app.stop()


async def test_task_cancel(postgres_url):
    test_schema_name = await prepare(postgres_url)

    fut = Future()

    class Api:
        @method()
        async def test123(self, arg):
            fut.set_result(arg)
            return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            Api(),
            TaskManagerConfig(db_url=postgres_url, db_schema=test_schema_name),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    task_id = await tm.schedule(
        Api.test123, {'arg': 123}, eta=time.time() + 600
    )

    tasks = await get_tasks_pending(postgres_url, test_schema_name)
    assert len(tasks) == 1

    await tm.cancel(task_id)

    tasks = await get_tasks_pending(postgres_url, test_schema_name)
    assert len(tasks) == 0

    tasks = await get_tasks_arch(postgres_url, test_schema_name)

    assert len(tasks) == 1
    assert tasks[0]['name'] == 'test123'
    assert tasks[0]['status'] == STATUS_CANCELED

    await app.stop()
