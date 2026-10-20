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


def fresh_schema(conn):
    conn.execute("DROP SCHEMA IF EXISTS serving CASCADE")
    conn.commit()
    schema.apply(conn)
