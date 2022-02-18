"""Test Oracle Database session invalidation
 How to build Oracle Database container image:
     https://github.com/oracle/docker-images/tree/main/OracleDatabase/SingleInstance

 Oracle Database Express Edition (18c):
     https://www.oracle.com/database/technologies/xe-downloads.html

 To build container:
     * download Oracle Database XE to docker-images/OracleDatabase/SingleInstance/dockerfiles
     * execute:
         > cd docker-images/OracleDatabase/SingleInstance/dockerfiles
         > ./buildContainerImage.sh -x -v 18.4.0

 Instant Client libraries:
     https://www.oracle.com/database/technologies/instant-client/linux-x86-64-downloads.html

 Run container for prepare to testing (mount oracle db data to oracle_db directory):
 > docker run --name oracle_db -p 1522:1521 -p 5500:5500 \
     -e ORACLE_PWD=11111111 -v oracle_db:/opt/oracle/ordata oracle/database:18.4.0-xe

 Execute:
 > LD_LIBRARY_PATH=path_to_instantclient_libraries pytest -v tests/test_db_oracle.py
 """
import cx_Oracle
import asyncio
import functools

import pytest

from ipapp import BaseApplication, BaseConfig
from ipapp.db.oracle import Oracle, OracleConfig

oracle_user = 'system'
oracle_pwd = '11111111'
oracle_host = 'localhost:1521'


def oracle_presence():
    try:
        with cx_Oracle.connect(
            user=oracle_user,
            password=oracle_pwd,
            dsn=oracle_host,
        ) as conn:
            cur = conn.cursor()
            cur.execute('SELECT 1 FROM DUAL')
            return True
    except cx_Oracle.DatabaseError as e:
        print(e)
        return False


oracle_presence = pytest.mark.skipif(
    not oracle_presence(),
    reason="requires oracle database presence with (user={user}, password={password}, dsn={host})".format(
        user=oracle_user, password=oracle_pwd, host=oracle_host
    ),
)


def set_initial_package(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE OR REPLACE PACKAGE talip_test
            IS
                global_var NUMBER := 10;
                PROCEDURE inner_test_proc;
            END;
            """
        )

        cursor.execute(
            """
            CREATE OR REPLACE PACKAGE BODY talip_test
            IS
                PROCEDURE inner_test_proc
                    IS
                BEGIN
                    global_var := global_var + 1;
                    DBMS_OUTPUT.put_line('Variable =' || global_var);
                END;
            END;
            """
        )

        cursor.execute(
            """
            CREATE OR REPLACE PROCEDURE outer_test_proc
            AS
                err VARCHAR2(1024);
            BEGIN
                talip_test.inner_test_proc;
            END;
            """
        )


def invalidate_package(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE OR REPLACE PACKAGE talip_test
            IS
                global_var NUMBER := 10;
                global_var2 NUMBER := 10;
                PROCEDURE inner_test_proc;
            END;
            """
        )

        cursor.execute(
            """
            CREATE OR REPLACE PACKAGE BODY talip_test
            IS
                PROCEDURE inner_test_proc
                    IS
                BEGIN
                    global_var := global_var + 1;
                    DBMS_OUTPUT.put_line('Variable =' || global_var);
                END;
            END;
            """
        )


def invalidate_package_with_error_proc(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE OR REPLACE PACKAGE talip_test
            IS
                global_var NUMBER := 10;
                global_var2 NUMBER := 10;
                global_var3 NUMBER := 10;
                PROCEDURE inner_test_proc;
            END;
            """
        )

        cursor.execute(
            """
            CREATE OR REPLACE PACKAGE BODY talip_test
            IS
                PROCEDURE inner_test_proc
                    IS
                BEGIN
                    global_var := global_var + 1;
                    DBMS_OUTPUT.put_line('Variable =' || global_var);
                    raise NO_DATA_FOUND;
                END;
            END;
            """
        )


@pytest.fixture()
async def oracle_connection():
    loop = asyncio.get_event_loop()
    connection = await loop.run_in_executor(
        None,
        functools.partial(
            cx_Oracle.connect,
            user=oracle_user,
            password=oracle_pwd,
            dsn=oracle_host,
        ),
    )
    await loop.run_in_executor(
        None,
        functools.partial(set_initial_package, connection),
    )
    yield connection
    await loop.run_in_executor(
        None,
        connection.close,
    )


async def startup() -> [BaseApplication, Oracle]:
    app = BaseApplication(BaseConfig())
    app.add(
        'db',
        Oracle(
            OracleConfig(
                user=oracle_user,
                password=oracle_pwd,
                dsn=oracle_host,
            )
        ),
    )
    await app.start()
    db: Oracle = app.get('db')  # type: ignore
    return app, db


@oracle_presence
async def test_regular_method_should_fail(loop, oracle_connection):
    app, db = await startup()

    async with db.connection() as conn:
        async with conn.cursor() as curs:

            await curs.callproc(
                'outer_test_proc',
                [],
            )

            # invalidate session
            await loop.run_in_executor(
                None,
                functools.partial(invalidate_package, oracle_connection),
            )

            with pytest.raises(cx_Oracle.DatabaseError):
                await curs.callproc(
                    'outer_test_proc',
                    [],
                )

    await app.stop()


@oracle_presence
async def test_retry_should_work(loop, oracle_connection):
    app, db = await startup()

    async with db.connection() as conn:
        async with conn.cursor() as curs:

            await curs.callproc_retry(
                'outer_test_proc',
                [],
            )

            # invalidate session
            await loop.run_in_executor(
                None,
                functools.partial(invalidate_package, oracle_connection),
            )

            await curs.callproc_retry(
                'outer_test_proc',
                [],
            )

    await app.stop()


@oracle_presence
async def test_refresh_should_work(loop, oracle_connection):
    app, db = await startup()

    async with db.connection() as conn:
        async with conn.cursor() as curs:

            await curs.callproc_refresh(
                'outer_test_proc',
                [],
            )

            # invalidate session
            await loop.run_in_executor(
                None,
                functools.partial(invalidate_package, oracle_connection),
            )

            await curs.callproc_refresh(
                'outer_test_proc',
                [],
            )

    await app.stop()


@oracle_presence
async def test_retry_should_escalate_error(loop, oracle_connection):
    app, db = await startup()

    async with db.connection() as conn:
        async with conn.cursor() as curs:

            await curs.callproc_retry(
                'outer_test_proc',
                [],
            )

            # invalidate session
            await loop.run_in_executor(
                None,
                functools.partial(
                    invalidate_package_with_error_proc, oracle_connection
                ),
            )

            with pytest.raises(cx_Oracle.DatabaseError):
                await curs.callproc_retry(
                    'outer_test_proc',
                    [],
                )

    await app.stop()


@oracle_presence
async def test_refresh_should_escalate_error(loop, oracle_connection):
    app, db = await startup()

    async with db.connection() as conn:
        async with conn.cursor() as curs:

            await curs.callproc_refresh(
                'outer_test_proc',
                [],
            )

            # invalidate session
            await loop.run_in_executor(
                None,
                functools.partial(
                    invalidate_package_with_error_proc, oracle_connection
                ),
            )

            with pytest.raises(cx_Oracle.DatabaseError):
                await curs.callproc_refresh(
                    'outer_test_proc',
                    [],
                )

    await app.stop()
