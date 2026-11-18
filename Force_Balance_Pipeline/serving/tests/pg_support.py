"""Test helper: the throwaway database force_serving_test on the local serving Postgres (never force_serving).

Part of the serving tests (doc 05 "Serving (Phase 7)"). The database is created if missing; fresh_schema() drops schema serving and
applies serving/sql/schema.sql again, which is what each test class does in setUpClass.
"""
from serving import config, schema

TEST_DB = "force_serving_test"


def connect_test():
    env = config.load_env()
    with config.connect(env) as admin:
        admin.autocommit = True
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone():
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    return config.connect(env, TEST_DB)


class RecordingCursor:
    """Wraps a psycopg cursor and appends (sql, params) of every execute to `log`."""

    def __init__(self, cursor, log):
        self._cursor, self._log = cursor, log

    def execute(self, sql, params=None, **kwargs):
        self._log.append((str(sql), params))
        return self._cursor.execute(sql, params, **kwargs)

    def executemany(self, sql, params_seq, **kwargs):
        self._log.append((str(sql), "many"))
        return self._cursor.executemany(sql, params_seq, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cursor.__exit__(*exc)


class RecordingConn:
    """Wraps a psycopg connection: every statement run through execute() or a cursor is appended to `log` as (sql, params), in order."""

    def __init__(self, conn):
        self._conn, self.log = conn, []

    def execute(self, sql, params=None, **kwargs):
        self.log.append((str(sql), params))
        return self._conn.execute(sql, params, **kwargs)

    def cursor(self, *args, **kwargs):
        return RecordingCursor(self._conn.cursor(*args, **kwargs), self.log)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def fresh_schema(conn):
    conn.execute("DROP SCHEMA IF EXISTS serving CASCADE")
    conn.commit()
    schema.apply(conn)
