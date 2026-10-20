"""Apply serving/sql/schema.sql to the local Postgres (doc 05 "Serving (Phase 7)").

    serving\\.venv\\Scripts\\python.exe -m serving.schema [--database NAME]

One transaction; the statements are idempotent, so running it twice is safe. Prints the number of tables in schema serving.
"""
import argparse
from pathlib import Path

from serving import config

SCHEMA_SQL = Path(__file__).resolve().parent / "sql" / "schema.sql"


def apply(conn):
    """Apply the schema on an open connection and commit; returns the table count of schema serving."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'serving' AND table_type = 'BASE TABLE'")
        count = cur.fetchone()[0]
    conn.commit()
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(prog="serving.schema")
    parser.add_argument("--database", default=None)
    args = parser.parse_args(argv)
    env = config.load_env()
    with config.connect(env, args.database) as conn:
        print(f"serving schema applied: {apply(conn)} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
