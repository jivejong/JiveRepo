"""Apply serving/sql/views.sql to the local Postgres (doc 05 "Serving (Phase 7)"; the panel views of doc 07 Phase 7).

    serving\\.venv\\Scripts\\python.exe -m serving.views [--database NAME]

One transaction; the statements are CREATE OR REPLACE, so running it twice is safe. Prints the number of views in schema serving.
"""
import argparse
from pathlib import Path

from serving import config

VIEWS_SQL = Path(__file__).resolve().parent / "sql" / "views.sql"


def apply(conn):
    """Apply the views on an open connection and commit; returns the view count of schema serving."""
    with conn.cursor() as cur:
        cur.execute(VIEWS_SQL.read_text(encoding="utf-8"))
        cur.execute("SELECT count(*) FROM information_schema.views WHERE table_schema = 'serving'")
        count = cur.fetchone()[0]
    conn.commit()
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(prog="serving.views")
    parser.add_argument("--database", default=None)
    args = parser.parse_args(argv)
    env = config.load_env()
    with config.connect(env, args.database) as conn:
        print(f"serving views applied: {apply(conn)} views")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
