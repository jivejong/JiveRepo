"""Create gold.deployment from agent/sql/create_deployment.sql (doc 03, "gold.deployment"; doc 06, "Runtime").

    edge\\.venv\\Scripts\\python.exe -m agent.ddl --target {dev,prod} [--env PATH]

The table is agent-owned, not a dbt model. The only substitution is {deployment}, taken from agent.config.tables(); the statement runs
once through StatementClient.write and is idempotent (CREATE TABLE IF NOT EXISTS).
"""
import argparse
import sys
import time
import urllib.request

from agent.config import ROOT, ConfigError, load_env, tables
from agent.dbsql import StatementClient

CREATE_DEPLOYMENT_SQL = ROOT / "agent" / "sql" / "create_deployment.sql"
DDL_ENV_KEYS = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID")


def render(template, target):
    """The template with {deployment} replaced by the validated table name of the target. Nothing else is substituted."""
    return template.replace("{deployment}", tables(target)["deployment"])


def create_deployment_table(target, db, template_path=CREATE_DEPLOYMENT_SQL):
    """Run the DDL once; returns the table name."""
    sql = render(template_path.read_text(encoding="utf-8"), target)
    db.write(sql, {})
    return tables(target)["deployment"]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent.ddl")
    parser.add_argument("--target", required=True, choices=("dev", "prod"))
    parser.add_argument("--env", default=str(ROOT / ".env.agent"))
    args = parser.parse_args(argv)
    try:
        env = load_env(args.env, required=DDL_ENV_KEYS)
    except ConfigError as e:
        print(f"configuration problem: {e}")
        return 2
    db = StatementClient(env["DATABRICKS_HOST"], env["DATABRICKS_TOKEN"], env["DATABRICKS_WAREHOUSE_ID"],
                         opener=urllib.request.urlopen, sleep=time.sleep, monotonic=time.monotonic)
    name = create_deployment_table(args.target, db)
    print(f"{name}: created or already exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
