"""Serving configuration: the .env.serving loader, the Postgres connection and the prod tables the puller reads.

Implements doc 05 "Serving (Phase 7)". .env.serving has its own loader because agent.config.load_env rejects any key outside the
agent's list. Messages name a key, never a value. The source table names are checked at import with the agent's own pattern.
"""
from pathlib import Path

from agent.config import TABLE_NAME

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT / ".env.serving"                 # gitignored

SERVING_ENV_KEYS = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID", "POSTGRES_PASSWORD", "SERVING_PING_S5_URL")

PG = {"host": "127.0.0.1", "port": 5433, "user": "force_serving", "dbname": "force_serving"}

SOURCES = {
    "sector_reading": "force.gold.gold_sector_reading",
    "sector_baseline": "force.gold.gold_sector_baseline",
    "disturbance": "force.gold.gold_disturbance",
    "deployment": "force.gold.deployment",
    "dim_sector": "force.gold.dim_sector",
    "dim_jedi": "force.gold.dim_jedi",
    "dim_starship": "force.gold.dim_starship",
    "probe_reading": "force.silver.silver_probe_reading",
    "rejects": "force.silver.silver_rejects",
    "source_health": "force.silver.silver_source_health",
}


class ConfigError(Exception):
    """A configuration problem. Messages name a key or a file line, never a value."""


def validate_source_name(name):
    """The name if it is catalog.schema.table in lowercase (the agent's pattern), else ConfigError."""
    if not isinstance(name, str) or not TABLE_NAME.match(name):
        raise ConfigError("a source table name is not catalog.schema.table in lowercase")
    return name


for _name in SOURCES.values():
    validate_source_name(_name)


def load_env(path=DEFAULT_ENV_FILE, required=("POSTGRES_PASSWORD",)):
    """KEY=VALUE lines; blank lines and lines starting with # are skipped. An unknown key or a missing required key raises
    ConfigError naming the key only."""
    values = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"line {number}: expected KEY=VALUE")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in SERVING_ENV_KEYS:
            raise ConfigError(f"line {number}: {key} is not an allowed key")
        values[key] = value
    for key in required:
        if not values.get(key):
            raise ConfigError(f"{key} is missing or empty")
    return values


def connect(env, database=None):
    """A psycopg connection to the local serving Postgres; `database` overrides the database name."""
    import psycopg
    return psycopg.connect(host=PG["host"], port=PG["port"], user=PG["user"], dbname=database or PG["dbname"],
                           password=env["POSTGRES_PASSWORD"])
