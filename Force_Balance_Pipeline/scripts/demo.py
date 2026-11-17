#!/usr/bin/env python3
"""make demo: the dashboard on display fixtures, in an isolated Postgres and on its own ports (doc 07, Phase 8 decision P8-2).

Standard library only, so the same recipe works with make on Windows and on Linux.

    python scripts/demo.py up      start the demo database, load the fixtures, build the dashboard, serve it (foreground)
    python scripts/demo.py down    stop the demo database (the volume is kept)
    python scripts/demo.py clean   down, then delete the volume, .demo/ and .env.demo

The demo owns its own Docker Compose project (fbp-demo), container, volume and database, Postgres on 127.0.0.1:5434 and
the backend on 127.0.0.1:8082. Deploy is off: the database is not the live one and holds fixture rows. It reads no
credential file of the live system and makes no warehouse, Gemini or MQTT call. The serving command-line entry points are
not used, because they are tied to the live database and its credential file; the demo calls the same functions with its own
connection (schema.recreate, views.apply, fixtures.load, server.make_server), under the venv in .demo/venv.

Exit codes: 0 ok, 1 a check failed (message on stderr), 2 a child process failed.
"""
import os
import secrets
import shutil
import socket
import subprocess
import sys
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "infra" / "demo" / "compose.yml"
ENV_FILE = ROOT / ".env.demo"
DEMO_DIR = ROOT / ".demo"
VENV_DIR = DEMO_DIR / "venv"
DASHBOARD = ROOT / "dashboard"
PROJECT = "fbp-demo"
CONTAINER = "fbp-demo-postgres"
PG_HOST, PG_PORT, PG_USER, PG_DB = "127.0.0.1", 5434, "force", "force_serving_demo"
WEB_PORT = 8082
PASSWORD_VAR = "DEMO_POSTGRES_PASSWORD"
DROP_PREFIXES = ("DATABRICKS_", "GEMINI_", "AGENT_", "SERVING_PING", "BUNDLE_")


class Fail(Exception):
    """A check failed; the message goes to stderr and the exit code is 1."""


def port_free(port, host="127.0.0.1"):
    """True when nothing accepts a connection on host:port."""
    try:
        with socket.create_connection((host, port), timeout=1):
            return False
    except OSError:
        return True


def ensure_env_file(path):
    """The demo password. Creates `path` with a new random one when it is missing; an existing file is never rewritten."""
    path = Path(path)
    if not path.exists():
        with open(path, "w", encoding="ascii", newline="") as f:
            f.write("POSTGRES_PASSWORD=" + secrets.token_urlsafe(32) + "\n")
    for line in path.read_text(encoding="ascii").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "POSTGRES_PASSWORD" and value.strip():
            return value.strip()
    raise Fail(f"{path.name} has no POSTGRES_PASSWORD line; delete it (make demo-clean) and run make demo again")


def child_env(extra=None):
    """A copy of the environment without any key that could carry a live credential, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES)}
    env.update(extra or {})
    return env


def venv_python():
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(args, cwd, env=None):
    """Run a child process (a list of arguments, never a shell); a failure exits the program with code 2."""
    try:
        subprocess.run([str(a) for a in args], cwd=str(cwd), env=env or child_env(), check=True)
    except subprocess.CalledProcessError as e:
        print(f"demo: a command failed (exit {e.returncode}): {Path(str(args[0])).name} {str(args[1]) if len(args) > 1 else ''}",
              file=sys.stderr)
        raise SystemExit(2)


def compose(*args, password=None):
    cmd = ["docker", "compose", "-p", PROJECT, "-f", COMPOSE_FILE]
    if ENV_FILE.exists():
        cmd += ["--env-file", ENV_FILE]
    # an exported POSTGRES_PASSWORD would override the env file, so it is always set explicitly
    run(cmd + list(args), ROOT, child_env({"POSTGRES_PASSWORD": password or "unused"}))


def demo_db_running():
    out = subprocess.run(["docker", "ps", "--filter", f"name=^{CONTAINER}$", "--format", "{{.Names}}"],
                         capture_output=True, text=True, cwd=str(ROOT), env=child_env())
    return out.returncode == 0 and CONTAINER in out.stdout


def check_prerequisites():
    if sys.version_info < (3, 10):
        raise Fail("Python 3.10 or later is required")
    tools = {}
    for name in ("docker", "node", "npm"):
        found = shutil.which(name)           # on Windows this finds npm.cmd; it is run by its full path, not through a shell
        if not found:
            raise Fail(f"{name} is not on PATH")
        tools[name] = found
    return tools


def cmd_up():
    tools = check_prerequisites()
    for port in (PG_PORT, WEB_PORT):
        if not port_free(port) and not (port == PG_PORT and demo_db_running()):
            raise Fail(f"port {port} on 127.0.0.1 is already in use")
    password = ensure_env_file(ENV_FILE)
    compose("up", "-d", "--wait", password=password)
    py = venv_python()
    if not py.exists():
        DEMO_DIR.mkdir(exist_ok=True)
        run([sys.executable, "-m", "venv", VENV_DIR], ROOT)
    run([py, "-m", "pip", "install", "-q", "-r", ROOT / "serving" / "requirements.txt"], ROOT)
    inner = child_env({PASSWORD_VAR: password})
    run([py, Path(__file__).resolve(), "_load"], ROOT, inner)
    run([tools["npm"], "ci"], DASHBOARD)
    run([tools["npm"], "run", "build"], DASHBOARD)
    print(f"Dashboard: http://127.0.0.1:{WEB_PORT}/  (Ctrl+C stops it; make demo-down stops the database)", flush=True)
    try:
        run([py, Path(__file__).resolve(), "_serve"], ROOT, inner)
    except KeyboardInterrupt:
        pass


def cmd_down():
    check_docker()
    compose("down", password=ensure_env_file(ENV_FILE) if ENV_FILE.exists() else None)


def cmd_clean():
    check_docker()
    compose("down", "-v", password=ensure_env_file(ENV_FILE) if ENV_FILE.exists() else None)
    shutil.rmtree(DEMO_DIR, ignore_errors=True)
    if ENV_FILE.exists():
        ENV_FILE.unlink()


def check_docker():
    if not shutil.which("docker"):
        raise Fail("docker is not on PATH")


# ---- the two steps below run under the venv's python (psycopg is installed there), from the Force_Balance_Pipeline folder
def demo_connect(env, database=None):
    import psycopg
    return psycopg.connect(host=PG_HOST, port=PG_PORT, user=PG_USER, dbname=database or PG_DB, password=env["POSTGRES_PASSWORD"])


def inner_env():
    password = os.environ.get(PASSWORD_VAR)
    if not password:
        raise Fail(f"{PASSWORD_VAR} is not set; run this through make demo")
    return {"POSTGRES_PASSWORD": password}


def cmd_load():
    sys.path.insert(0, str(ROOT))
    from serving import fixtures, schema, views
    now = fixtures.parse_now(None)
    with demo_connect(inner_env()) as conn:
        print(f"demo: serving schema recreated: {schema.recreate(conn)} tables")
        print(f"demo: serving views applied: {views.apply(conn)} views")
        counts = fixtures.load(conn, now, reset=True)
    print("demo: fixtures loaded for now=" + now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
          + ", " + ", ".join(f"{k}={v}" for k, v in counts.items()))


def cmd_serve():
    sys.path.insert(0, str(ROOT))
    from serving import server
    server.configure_logging(DEMO_DIR / "dashboard.log")
    srv = server.make_server(PG_DB, WEB_PORT, env=inner_env(), connect=demo_connect)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


COMMANDS = {"up": cmd_up, "down": cmd_down, "clean": cmd_clean, "_load": cmd_load, "_serve": cmd_serve}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print("usage: demo.py up | down | clean", file=sys.stderr)
        return 1
    try:
        COMMANDS[argv[0]]()
    except Fail as e:
        print(f"demo: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
