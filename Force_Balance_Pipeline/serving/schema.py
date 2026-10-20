"""Apply serving/sql/schema.sql to the local Postgres (doc 05 "Serving (Phase 7)").

    serving\\.venv\\Scripts\\python.exe -m serving.schema [--database NAME] [--recreate]

One transaction; the statements are idempotent, so running it twice is safe. Prints the number of tables in schema serving.
--recreate drops schema serving (views included) and applies schema.sql again in the same transaction; it refuses (exit 2, nothing
changed) when serving.publish_log holds a 'live' row. Re-apply serving.views afterwards.
"""
import argparse
from pathlib import Path

from serving import config

SCHEMA_SQL = Path(__file__).resolve().parent / "sql" / "schema.sql"


class Refused(Exception):
    """--recreate would destroy live data."""


def apply(conn):
    """Apply the schema on an open connection and commit; returns the table count of schema serving."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'serving' AND table_type = 'BASE TABLE'")
        count = cur.fetchone()[0]
    conn.commit()
    return count


def recreate(conn):
    """Drop schema serving and apply schema.sql in one transaction; raises Refused (nothing changed) if a live publish exists."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('serving.publish_log') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("SELECT count(*) FROM serving.publish_log WHERE kind = 'live'")
            if cur.fetchone()[0]:
                conn.rollback()
                raise Refused("serving.publish_log has a live row; --recreate would destroy live data")
        cur.execute("DROP SCHEMA IF EXISTS serving CASCADE")
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'serving' AND table_type = 'BASE TABLE'")
        count = cur.fetchone()[0]
    conn.commit()
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(prog="serving.schema")
    parser.add_argument("--database", default=None)
    parser.add_argument("--recreate", action="store_true")
    args = parser.parse_args(argv)
    env = config.load_env()
    with config.connect(env, args.database) as conn:
        if args.recreate:
            try:
                print(f"serving schema recreated: {recreate(conn)} tables")
            except Refused as e:
                print(f"serving.schema: refused: {e}")
                return 2
        else:
            print(f"serving schema applied: {apply(conn)} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
