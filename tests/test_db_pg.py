import asyncpg

from ipapp import BaseApplication, BaseConfig
from ipapp.db.pg import Postgres, PostgresConfig


async def test_base(loop, postgres_url):
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

    result = list()
    a = [10, 15, 105]
    b = ['dd', 'a', '7i']
    async with db.connection() as conn:
        cursor = conn.cursor(
            # fmt: off
            "SELECT"
            ""  " UNNEST($1::int[]) as a"
            ""  ",UNNEST($2::varchar[]) as b",
            # fmt: on
            a,
            b,
            prefetch=2,
            query_name='db6',
        )
        async with conn.xact():
            async for res in cursor:
                result.append(res)
                assert isinstance(res, asyncpg.Record)
                assert res['a'] == a[len(result) - 1]
                assert res['b'] == b[len(result) - 1]
    assert len(result) == 3

    await app.stop()


async def test_statement_cache_size(loop, postgres_url):
    app = BaseApplication(BaseConfig())
    app.add(
        'db',
        Postgres(
            PostgresConfig(
                url=postgres_url,
                log_result=True,
                log_query=True,
                statement_cache_size=0,
            )
        ),
    )
    await app.start()
    db: Postgres = app.get('db')  # type: ignore
    async with db.connection() as conn:
        assert conn._conn._stmt_cache._max_size == 0
    await app.stop()


async def test_xact(loop, postgres_url):
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


async def test_prepare(loop, postgres_url):
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

        result = list()
        a = [10, 15, 105]
        b = ['dd', 'a', '7i']
        st3 = await conn.prepare(
            # fmt: off
            "SELECT"
            ""  " UNNEST($1::int[]) as a"
            ""  ",UNNEST($2::varchar[]) as b",
            # fmt: on
            query_name='db7',
        )
        async with conn.xact():
            async for res in st3.cursor(a, b, prefetch=2):
                result.append(res)
                assert isinstance(res, asyncpg.Record)
                assert res['a'] == a[len(result) - 1]
                assert res['b'] == b[len(result) - 1]
        assert len(result) == 3
