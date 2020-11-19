import time
import uuid
from asyncio import Future, wait_for
from datetime import datetime, timezone
from functools import wraps

import asyncpg
import pytest

from ipapp import BaseApplication, BaseConfig
from ipapp.db.pg import Postgres
from ipapp.logger import Span
from ipapp.task.db import (
    CREATE_TABLE_QUERY,
    STATUS_CANCELED,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_SUCCESSFUL,
    Retry,
    TaskManager,
    TaskManagerConfig,
    TaskRegistry,
)


async def connect(postgres_url) -> asyncpg.Connection:
    conn = await asyncpg.connect(postgres_url)
    await Postgres._conn_init(conn)
    return conn


async def prepare(postgres_url, with_trace_id: bool = False) -> str:
    test_schema_name = 'testdbtm'
    conn = await connect(postgres_url)
    await conn.execute('DROP SCHEMA IF EXISts %s CASCADE' % test_schema_name)
    await conn.execute('CREATE SCHEMA %s' % test_schema_name)
    if not with_trace_id:
        await conn.execute(CREATE_TABLE_QUERY.format(schema=test_schema_name))
        await conn.execute(
            'ALTER TABLE %s.task DROP COLUMN trace_id' % test_schema_name
        )
        await conn.execute(
            'ALTER TABLE %s.task DROP COLUMN trace_span_id' % test_schema_name
        )
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


@pytest.mark.parametrize(
    "with_trace_id",
    [True, False],
)
async def test_success(loop, postgres_url: str, with_trace_id: bool):
    test_schema_name = await prepare(postgres_url, with_trace_id=with_trace_id)

    fut = Future()

    reg = TaskRegistry()

    @reg.task()
    async def test(arg):
        fut.set_result(arg)
        return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            reg,
            TaskManagerConfig(
                db_url=postgres_url,
                db_schema=test_schema_name,
                create_database_objects=True,
            ),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    await tm.schedule(test, {'arg': 123}, eta=time.time() + 3)
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


@pytest.mark.parametrize(
    "with_trace_id",
    [True, False],
)
async def test_reties_success(loop, postgres_url: str, with_trace_id: bool):
    test_schema_name = await prepare(postgres_url, with_trace_id=with_trace_id)

    fut = Future()

    class Api:
        attempts = 0

    reg = TaskRegistry()

    @reg.task(max_retries=2, retry_delay=0.2)
    async def test(arg):
        Api.attempts += 1
        if Api.attempts <= 2:
            raise Retry(Exception('Attempt %s' % Api.attempts))

        fut.set_result(arg)
        return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            reg,
            TaskManagerConfig(
                db_url=postgres_url,
                db_schema=test_schema_name,
                create_database_objects=True,
            ),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    await tm.schedule(test, {'arg': 234})

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


@pytest.mark.parametrize(
    "with_trace_id",
    [True, False],
)
async def test_reties_error(loop, postgres_url: str, with_trace_id: bool):
    test_schema_name = await prepare(postgres_url, with_trace_id=with_trace_id)

    fut = Future()

    class Api:
        attempts = 0

    reg = TaskRegistry()

    @reg.task(name='someTest')
    async def test(arg):

        Api.attempts += 1
        if Api.attempts <= 2:
            raise Retry(Exception('Attempt %s' % Api.attempts))

        fut.set_result(arg)
        return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            reg,
            TaskManagerConfig(
                db_url=postgres_url,
                db_schema=test_schema_name,
                create_database_objects=True,
            ),
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


@pytest.mark.parametrize(
    "with_trace_id",
    [True, False],
)
async def test_tasks_by_ref(loop, postgres_url: str, with_trace_id: bool):
    test_schema_name = await prepare(postgres_url, with_trace_id=with_trace_id)

    fut = Future()
    reg = TaskRegistry()

    @reg.task()
    async def test(arg):
        fut.set_result(arg)
        return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            reg,
            TaskManagerConfig(
                db_url=postgres_url,
                db_schema=test_schema_name,
                create_database_objects=True,
            ),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    ref = str(uuid.uuid4())

    await tm.schedule(test, {'arg': 123}, eta=time.time() + 3, reference=ref)
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


@pytest.mark.parametrize(
    "with_trace_id",
    [True, False],
)
async def test_task_cancel(loop, postgres_url: str, with_trace_id: bool):
    test_schema_name = await prepare(postgres_url, with_trace_id=with_trace_id)

    fut = Future()

    reg = TaskRegistry()

    @reg.task()
    async def test123(arg):
        fut.set_result(arg)
        return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            reg,
            TaskManagerConfig(
                db_url=postgres_url,
                db_schema=test_schema_name,
                create_database_objects=True,
            ),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    task_id = await tm.schedule(test123, {'arg': 123}, eta=time.time() + 600)

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


@pytest.mark.parametrize(
    "with_trace_id",
    [True, False],
)
async def test_task_crontab(loop, postgres_url: str, with_trace_id: bool):
    test_schema_name = await prepare(postgres_url, with_trace_id=with_trace_id)

    fut = Future()
    count = []
    reg = TaskRegistry()

    @reg.task(crontab='* * * * * * *')
    async def test123():
        count.append(None)
        if len(count) == 2:
            fut.set_result(111)

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            reg,
            TaskManagerConfig(
                db_url=postgres_url,
                db_schema=test_schema_name,
                create_database_objects=True,
            ),
        ),
    )
    await app.start()

    res = await wait_for(fut, 10)
    assert res == 111

    tasks = await get_tasks_arch(postgres_url, test_schema_name)
    assert len(tasks) == 2

    logs = await get_tasks_log(postgres_url, test_schema_name)
    assert len(logs) == 2

    assert len(await get_tasks_pending(postgres_url, test_schema_name)) == 0

    await app.stop()


@pytest.mark.parametrize(
    "with_trace_id",
    [True, False],
)
async def test_task_crontab_with_date_attr(
    loop, postgres_url: str, with_trace_id: bool
):
    test_schema_name = await prepare(postgres_url, with_trace_id=with_trace_id)

    fut = Future()
    count = []

    reg = TaskRegistry()

    @reg.task(crontab='* * * * * * *', crontab_date_attr='date')
    async def test123(date: datetime):
        count.append(date)
        if len(count) == 2:
            fut.set_result(111)

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            reg,
            TaskManagerConfig(
                db_url=postgres_url,
                db_schema=test_schema_name,
                create_database_objects=True,
            ),
        ),
    )
    await app.start()

    res = await wait_for(fut, 10)
    assert res == 111

    assert count[0] < count[1]

    tasks = await get_tasks_arch(postgres_url, test_schema_name)
    assert len(tasks) == 2

    logs = await get_tasks_log(postgres_url, test_schema_name)
    assert len(logs) == 2

    assert len(await get_tasks_pending(postgres_url, test_schema_name)) == 0

    await app.stop()


@pytest.mark.parametrize(
    "with_trace_id",
    [True, False],
)
async def test_decorator(loop, postgres_url: str, with_trace_id: bool):
    test_schema_name = await prepare(postgres_url, with_trace_id=with_trace_id)

    fut = Future()

    reg = TaskRegistry()

    def dec(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            assert kwargs['arg'] == 123
            return await func(*args, **kwargs)

        return wrapper

    @reg.task()
    @dec
    async def test(arg):
        fut.set_result(arg)
        return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            reg,
            TaskManagerConfig(
                db_url=postgres_url,
                db_schema=test_schema_name,
                create_database_objects=True,
            ),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    await tm.schedule(test, {'arg': 123}, eta=time.time() + 3)
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


@pytest.mark.parametrize(
    "with_trace_id",
    [True, False],
)
async def test_propagate_trace(loop, postgres_url: str, with_trace_id: bool):
    test_schema_name = await prepare(postgres_url, with_trace_id=with_trace_id)

    fut = Future()

    reg = TaskRegistry()

    @reg.task()
    async def test(arg):
        fut.set_result(arg)
        return arg

    app = BaseApplication(BaseConfig())
    app.add(
        'tm',
        TaskManager(
            reg,
            TaskManagerConfig(
                db_url=postgres_url,
                db_schema=test_schema_name,
                create_database_objects=False,
            ),
        ),
    )
    await app.start()
    tm: TaskManager = app.get('tm')  # type: ignore

    with app.logger.span_new():
        with app.logger.capture_span(Span) as trap:
            if with_trace_id:
                await tm.schedule(
                    test,
                    {'arg': 123},
                    eta=time.time() + 3,
                    propagate_trace=True,
                )
            else:
                # колонки с trace_id нет в БД, поэтому будет ошибка с propagate_trace=True
                with pytest.raises(asyncpg.exceptions.UndefinedColumnError):
                    await tm.schedule(
                        test,
                        {'arg': 123},
                        eta=time.time() + 3,
                        propagate_trace=True,
                    )
                # stop test
                return
            trace = [trap.span.trace_id, trap.span.id]
    tasks = await get_tasks_pending(postgres_url, test_schema_name)
    assert len(tasks) == 1
    assert tasks[0]['name'] == 'test'
    assert tasks[0]['params'] == {'arg': 123}
    assert tasks[0]['eta'] > datetime.now(tz=timezone.utc)
    assert tasks[0]['last_stamp'] < datetime.now(tz=timezone.utc)
    assert tasks[0]['status'] == STATUS_PENDING
    assert tasks[0]['retries'] is None
    assert tasks[0]['trace_id'] == trace[0]
    assert tasks[0]['trace_span_id'] == trace[1]

    res = await wait_for(fut, 10)
    assert res == 123
