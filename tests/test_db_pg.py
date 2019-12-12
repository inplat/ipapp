import asyncpg

from ipapp import BaseApplication, BaseConfig
from ipapp.db.pg import Postgres, PostgresConfig


async def test_base(postgres_url):
    app = BaseApplication(BaseConfig())
    app.add(
        'db',
        Postgres(
            PostgresConfig(url=postgres_url, log_result=True, log_query=True)
        ),
    )
    await app.start()
    db: Postgres = app.get('db')  # type: ignore

    res = await db.execute('SELECT $1::int as a', 10, query_name='db1')
    assert 'SELECT 1' == res

    res = await db.query_one('SELECT $1::int as a', 10, query_name='db2')
    assert isinstance(res, asyncpg.Record)
    assert res['a'] == 10

    # json
    res = await db.query_one('SELECT $1::json as a', {'a': 1})
    assert isinstance(res, asyncpg.Record)
    assert res['a'] == {'a': 1}

    # jsonb
    res = await db.query_one('SELECT $1::jsonb as a', {'a': 1})
    assert isinstance(res, asyncpg.Record)
    assert res['a'] == {'a': 1}

    res = await db.query_one('SELECT 1 as a WHERE FALSE')
    assert res is None

    res = await db.query_all('SELECT $1::int as a', 10, query_name='db3')
    assert isinstance(res, list)
    assert len(res) == 1
    assert isinstance(res[0], asyncpg.Record)
    assert res[0]['a'] == 10

    res = await db.query_all('SELECT 1 as a WHERE FALSE')
    assert isinstance(res, list)
    assert len(res) == 0

    await app.stop()


async def test_xact(postgres_url):
    app = BaseApplication(BaseConfig())
    app.add(
        'db',
        Postgres(
            PostgresConfig(url=postgres_url, log_result=True, log_query=True)
        ),
    )
    await app.start()
    db: Postgres = app.get('db')  # type: ignore

    async with db.connection() as conn:
        await conn.execute('CREATE TEMPORARY TABLE _test_xact(id int)')
        try:
            async with conn.xact():
                await conn.execute('INSERT INTO _test_xact(id) VALUES (12)')
                r = await conn.query_one('SELECT COUNT(*) c FROM _test_xact')
                assert r['c'] == 1
                raise Exception('rollback')
        except Exception as err:
            assert str(err) == 'rollback', err

        r = await conn.query_one('SELECT COUNT(*) c FROM _test_xact')
        assert r['c'] == 0


async def test_prepare(postgres_url):
    app = BaseApplication(BaseConfig())
    app.add(
        'db',
        Postgres(
            PostgresConfig(url=postgres_url, log_result=True, log_query=True)
        ),
    )
    await app.start()
    db: Postgres = app.get('db')  # type: ignore

    async with db.connection() as conn:
        st = await conn.prepare('SELECT $1::int as a', query_name='db4')
        row = await st.query_one(10)
        assert row['a'] == 10

        rows = await st.query_all(20)
        assert len(rows) == 1
        assert rows[0]['a'] == 20

        st2 = await conn.prepare('SELECT $1::text as a')
        row = await st2.query_one('30')
        assert row['a'] == '30'

        rows = await st2.query_all('30')
        assert len(rows) == 1
        assert rows[0]['a'] == '30'
