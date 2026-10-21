"""Agent configuration: constants, the table names for each target and the .env.agent loader.

Implements doc 06 "Runtime" (AGENT_EPOCH, MAX_INCIDENTS_PER_RUN, credentials in .env.agent) and doc 03 "gold.deployment"
(Prod: force.gold.deployment; Dev: force.dev_jivejong.deployment). Table names are the only identifiers that reach SQL text,
and they come only from TARGETS after tables() has checked each one against TABLE_NAME.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT / ".env.agent"                   # gitignored

AGENT_EPOCH = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
MAX_INCIDENTS_PER_RUN = 5
POLL_LIMIT = 50                    # undecided incidents one poll reads (doc 06 "Human-first window"); at most MAX_INCIDENTS_PER_RUN are decided
JOB1_ID = 692051683030737

TABLE_NAME = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+\.[a-z0-9_]+$")

_TABLES = ("disturbance", "deployment", "reading", "sector", "jedi", "starship")


def _names(schema):
    return {
        "disturbance": f"force.{schema}.gold_disturbance",
        "deployment": f"force.{schema}.deployment",
        "reading": f"force.{schema}.gold_sector_reading",
        "sector": f"force.{schema}.dim_sector",
        "jedi": f"force.{schema}.dim_jedi",
        "starship": f"force.{schema}.dim_starship",
    }


TARGETS = {
    "prod": _names("gold"),
    "dev": _names("dev_jivejong"),
}

ENV_KEYS = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID", "GEMINI_API_KEY", "INFERENCE_MODEL")
# The S4 dead-man's-switch ping URL (doc 05, "Monitoring"), named like the bridge's BRIDGE_PING_S1_URL. Credential-like: never logged.
PING_KEY = "AGENT_PING_S4_URL"
RUN_ENV_KEYS = ENV_KEYS + (PING_KEY,)             # what the cycle runner needs (agent.run); the eval needs only the Gemini keys
ALLOWED_KEYS = RUN_ENV_KEYS                        # the whole of .env.agent


class ConfigError(Exception):
    """A configuration problem. Messages name a key or a file line, never a value."""


def tables(target):
    """The validated table names for a target ("prod" or "dev"). Raises ValueError for an unknown target or a bad name."""
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; expected one of {sorted(TARGETS)}")
    names = TARGETS[target]
    if set(names) != set(_TABLES):
        raise ValueError(f"target {target!r} must define exactly {list(_TABLES)}")
    for key, name in names.items():
        if not isinstance(name, str) or not TABLE_NAME.match(name):
            raise ValueError(f"table name for {key!r} is not catalog.schema.table in lowercase: {name!r}")
    return dict(names)


def load_env(path=DEFAULT_ENV_FILE, required=ENV_KEYS):
    """KEY=VALUE lines of .env.agent, # comments, optional quotes. The keys in `required` (default: all of ENV_KEYS) must be set
    and non-empty; a key outside ALLOWED_KEYS is refused."""
    path = Path(path)
    values = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{path.name} line {number}: expected KEY=VALUE")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in ALLOWED_KEYS:
            raise ConfigError(f"{path.name} line {number}: {key} is not allowed; this file holds only " + ", ".join(ALLOWED_KEYS))
        if key in values:
            raise ConfigError(f"{path.name} line {number}: {key} is set twice")
        values[key] = value
    for key in required:
        if not values.get(key):
            raise ConfigError(f"{path.name}: {key} is missing or empty")
    return values
