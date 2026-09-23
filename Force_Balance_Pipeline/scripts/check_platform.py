#!/usr/bin/env python3
"""Phase 0 platform checks for Force_Balance_Pipeline (docs 00, 05, 07).

Answers the three open questions from doc 00:
  Q1  Does the `dbt` job task type run from Git on Free Edition?
  Q2  Are Unity Catalog external locations to GCS permitted?
  Q3  Does `streaming_table` build and refresh on this workspace with the pinned adapter?

Subcommands:
  preflight   read-only checks (env, CLI, auth, warehouse id, catalog, pin)
  q1          dbt task from Git via `databricks jobs submit`
  q2          manual: UI steps + read-only evidence; `q2 --verify` re-checks afterwards
  q3          streaming_table build + refresh (requires q1 to have passed)
  local       prints steps for a repo-local .venv dbt run against the dev target
  cleanup     drops force.phase0 (or only the streaming probe with --streaming-only)

Standard library only. Every outbound HTTP call goes through http_request(), which always
sends the project User-Agent (the default Python-urllib UA gets Cloudflare 1010 / 403).
The Databricks CLI sends its own User-Agent.

Workspace writes are confined to the schema force.phase0. Nothing here is fabricated:
anything that cannot be retrieved is reported as such, with where to look by hand.
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# --- constants ---------------------------------------------------------------------------

UA = "Force_Balance_Pipeline/0.1 (+https://github.com/jivejong/JiveRepo/tree/main/Force_Balance_Pipeline)"

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO_ROOT / "warehouse" / "dbt" / "requirements.txt"
STATE_FILE = REPO_ROOT / ".phase0_state.json"

CATALOG = "force"
SCHEMA = "phase0"
VOLUME = "stream_src"
STREAM_MODEL = "phase0_streaming_check"
STREAM_TABLE = f"{CATALOG}.{SCHEMA}.{STREAM_MODEL}"
VOLUME_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
STREAM_VAR = "enable_streaming_check"
# This project lives in a subfolder of the JiveRepo repo, so the dbt task's project directory
# is relative to the Git repo root, not to Force_Balance_Pipeline/. Doc 05 assumes a standalone
# repo and says `warehouse/dbt`; override with --project-dir if that ever changes.
DEFAULT_PROJECT_DIR = "Force_Balance_Pipeline/warehouse/dbt"
ENV_KEY = "dbt_env"
HTTP_PATH_RE = re.compile(r"^/sql/1\.0/warehouses/([0-9a-fA-F]+)$")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# Rows the two landing files carry; q3 compares the table's row count against these.
BATCH_1 = [{"id": i, "label": f"row{i}", "value": i * 10} for i in range(1, 6)]
BATCH_2 = [{"id": i, "label": f"row{i}", "value": i * 10} for i in range(6, 9)]


class CheckError(Exception):
    """A check could not be performed; the message says why."""


def say(msg=""):
    print(msg, flush=True)


def banner(title):
    say()
    say("=" * 78)
    say(title)
    say("=" * 78)


# --- config and HTTP ---------------------------------------------------------------------

def load_dotenv():
    """Load REPO_ROOT/.env into os.environ without overriding real environment variables."""
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        if val and key.strip() not in os.environ:
            os.environ[key.strip()] = val


def config():
    """Return validated Databricks settings from the environment. Raises CheckError."""
    missing = [k for k in ("DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN")
               if not os.environ.get(k)]
    if missing:
        raise CheckError(f"missing environment variables: {', '.join(missing)} (see .env.example)")
    host = os.environ["DATABRICKS_HOST"].strip().rstrip("/")
    if not host.startswith("http"):
        host = "https://" + host
    http_path = os.environ["DATABRICKS_HTTP_PATH"].strip()
    m = HTTP_PATH_RE.match(http_path)
    if not m:
        raise CheckError(
            f"DATABRICKS_HTTP_PATH={http_path!r} does not match /sql/1.0/warehouses/<id>; "
            "copy it from the SQL warehouse's Connection details tab")
    return {"host": host, "token": os.environ["DATABRICKS_TOKEN"],
            "http_path": http_path, "warehouse_id": m.group(1)}


def http_request(method, url, *, headers=None, data=None, timeout=60):
    """The only outbound HTTP path in this script. Always sends the explicit User-Agent."""
    hdrs = {"User-Agent": UA}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        if e.code == 403 and b"1010" in body:
            say("  note: 403 'error code: 1010' is a Cloudflare client-fingerprint block, not egress.")
        return e.code, body


def db_api(cfg, method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"}
    status, raw = http_request(method, cfg["host"] + path, headers=headers, data=body)
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = {"_raw": raw[:500].decode("utf-8", "replace")}
    return status, parsed


def sql(cfg, statement, timeout_s=600):
    """Run one statement on the warehouse via the SQL Statement Execution API; return rows."""
    status, body = db_api(cfg, "POST", "/api/2.0/sql/statements/", {
        "warehouse_id": cfg["warehouse_id"], "statement": statement,
        "wait_timeout": "30s", "on_wait_timeout": "CONTINUE"})
    if status != 200:
        raise CheckError(f"statement API HTTP {status}: {body}")
    deadline = time.time() + timeout_s
    while body.get("status", {}).get("state") in ("PENDING", "RUNNING"):
        if time.time() > deadline:
            raise CheckError(f"statement timed out: {statement}")
        time.sleep(3)
        status, body = db_api(cfg, "GET", f"/api/2.0/sql/statements/{body['statement_id']}")
    state = body.get("status", {})
    if state.get("state") != "SUCCEEDED":
        err = state.get("error", {})
        raise CheckError(f"SQL failed [{err.get('error_code')}]: {err.get('message')}\n  {statement}")
    return body.get("result", {}).get("data_array") or []


def upload_file(cfg, volume_path, content):
    """PUT a file into a UC volume through the Files API."""
    url = f"{cfg['host']}/api/2.0/fs/files{volume_path}?overwrite=true"
    status, raw = http_request("PUT", url, data=content, headers={
        "Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/octet-stream"})
    if status not in (200, 201, 204):
        raise CheckError(f"Files API upload to {volume_path} failed: HTTP {status} {raw[:300]!r}")


# --- CLI ---------------------------------------------------------------------------------

def cli_path():
    return shutil.which("databricks")


def cli_env(cfg):
    env = dict(os.environ)
    env["DATABRICKS_HOST"] = cfg["host"]
    env["DATABRICKS_TOKEN"] = cfg["token"]
    return env


def cli(cfg, *args):
    exe = cli_path()
    if not exe:
        raise CheckError("Databricks CLI not found on PATH; run `preflight` for install steps")
    proc = subprocess.run([exe, *args], capture_output=True, text=True, env=cli_env(cfg))
    if proc.returncode != 0:
        raise CheckError(f"`databricks {' '.join(args)}` failed (exit {proc.returncode}):\n"
                         f"{proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def cli_json(cfg, *args):
    out = cli(cfg, *args, "-o", "json")
    try:
        return json.loads(out)
    except ValueError:
        raise CheckError(f"non-JSON CLI output for `databricks {' '.join(args)}`:\n{out[:500]}")


def print_cli_install_steps():
    say("  Databricks CLI is not installed. Install it yourself (I do not install it):")
    say("    Windows (user scope):  winget install Databricks.DatabricksCLI")
    say("    or see https://docs.databricks.com/aws/en/dev-tools/cli/install")
    say("  Then confirm with:  databricks --version")
    say("  Auth: this script exports DATABRICKS_HOST / DATABRICKS_TOKEN from .env to the CLI,")
    say("  so no `databricks auth login` is needed unless you prefer OAuth.")


# --- pinned version, cloud inference, state ---------------------------------------------

def pinned_requirement():
    if not REQUIREMENTS.exists():
        raise CheckError(f"{REQUIREMENTS} not found")
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.lower().startswith("dbt-databricks"):
            return line
    raise CheckError(f"no dbt-databricks line in {REQUIREMENTS}")


def infer_cloud(host):
    h = host.lower()
    if "azuredatabricks.net" in h:
        return "azure"
    if ".gcp.databricks.com" in h:
        return "gcp"
    if ".cloud.databricks.com" in h:
        return "aws"
    return "unknown"


def read_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def write_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# --- dbt task runner (shared by q1 and q3) ----------------------------------------------

def find_key(obj, key):
    """First value for `key` anywhere in a nested JSON structure, else None."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def build_submit_payload(cfg, run_name, commands, git_url, branch, env_version, project_dir):
    spec = {"dependencies": [pinned_requirement()]}
    if env_version:
        spec["environment_version"] = env_version
    return {
        "run_name": run_name,
        "git_source": {"git_url": git_url, "git_provider": "gitHub", "git_branch": branch},
        "environments": [{"environment_key": ENV_KEY, "spec": spec}],
        "tasks": [{
            "task_key": "dbt",
            "environment_key": ENV_KEY,
            "dbt_task": {
                "project_directory": project_dir,
                "commands": commands,
                "catalog": CATALOG,
                "schema": SCHEMA,
                "warehouse_id": cfg["warehouse_id"],
                "source": "GIT",
            },
        }],
    }


def collect_dbt_lines(cfg, task_run_id):
    """Best-effort: pull dbt log lines from the run's output/artifacts. Returns (lines, notes)."""
    wanted = ("Running with dbt=", "Registered adapter", "Done. PASS=")
    found, notes = [], []
    try:
        out = cli_json(cfg, "jobs", "get-run-output", str(task_run_id))
    except CheckError as e:
        return found, [f"get-run-output failed: {e}"]
    texts = [str(out[k]) for k in ("logs", "error", "error_trace") if out.get(k)]
    dbt_out = out.get("dbt_output") or {}
    link = dbt_out.get("artifacts_link")
    if link:
        headers = dict(dbt_out.get("artifacts_headers") or {})
        status, raw = http_request("GET", link, headers=headers, timeout=120)
        if status == 200:
            try:
                with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
                    for m in tf.getmembers():
                        if m.isfile() and m.size < 20_000_000 and m.name.endswith((".log", ".txt")):
                            texts.append(tf.extractfile(m).read().decode("utf-8", "replace"))
            except tarfile.TarError as e:
                notes.append(f"artifacts were not a readable tar archive: {e}")
        else:
            notes.append(f"artifacts download returned HTTP {status}")
    else:
        notes.append("run output carried no dbt artifacts link")
    for text in texts:
        for line in text.splitlines():
            if any(w in line for w in wanted) and line.strip() not in found:
                found.append(line.strip())
    return found, notes


def run_dbt_task(cfg, name, commands, git_url, branch, env_version, project_dir):
    """Submit a one-time dbt_task run from Git, wait, and return an evidence dict."""
    payload = build_submit_payload(cfg, name, commands, git_url, branch, env_version, project_dir)
    say(f"  submitting: {name}")
    say(f"  project:    {project_dir}")
    say(f"  commands:   {commands}")
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "submit.json"
        f.write_text(json.dumps(payload), encoding="utf-8")
        out = cli(cfg, "jobs", "submit", "--no-wait", "--json", f"@{f}", "-o", "json")
    m = re.search(r'"run_id"\s*:\s*(\d+)', out)
    if not m:
        raise CheckError(f"could not read run_id from submit output:\n{out[:500]}")
    run_id = int(m.group(1))
    say(f"  run_id: {run_id}; polling every 15s")
    while True:
        run = cli_json(cfg, "jobs", "get-run", str(run_id))
        state = run.get("state", {})
        if state.get("life_cycle_state") in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            break
        time.sleep(15)
    start, end = run.get("start_time"), run.get("end_time")
    tasks = run.get("tasks") or [{}]
    lines, notes = collect_dbt_lines(cfg, tasks[0].get("run_id", run_id))
    return {
        "run_id": run_id,
        "run_page_url": run.get("run_page_url"),
        "life_cycle_state": state.get("life_cycle_state"),
        "result_state": state.get("result_state"),
        "state_message": state.get("state_message"),
        "duration_s": round((end - start) / 1000, 1) if start and end else None,
        "commit_sha": find_key(run, "used_commit"),
        "dbt_lines": lines,
        "notes": notes,
        "ok": state.get("result_state") == "SUCCESS",
    }


def print_run_evidence(ev):
    say("  --- evidence (paste this block back) ---")
    say(f"  run_id:        {ev['run_id']}")
    say(f"  run page:      {ev['run_page_url']}")
    say(f"  state:         {ev['life_cycle_state']} / {ev['result_state']}")
    say(f"  state message: {ev['state_message']}")
    say(f"  duration (s):  {ev['duration_s']}")
    say(f"  commit SHA:    {ev['commit_sha'] or 'NOT RETURNED by the API'}")
    if ev["dbt_lines"]:
        for line in ev["dbt_lines"]:
            say(f"  log: {line}")
    else:
        say("  dbt log lines: NOT RETRIEVABLE via API. Copy them from the run page: open the dbt")
        say("    task > Output/Logs, and paste the lines containing 'Running with dbt=' and")
        say("    'Registered adapter'.")
    for n in ev["notes"]:
        say(f"  note: {n}")
    say("  --- end evidence ---")


# --- subcommands -------------------------------------------------------------------------

def cmd_preflight(args):
    banner("preflight (read-only)")
    failures = 0

    def check(label, fn):
        nonlocal failures
        try:
            detail = fn()
            say(f"  [ ok ] {label}" + (f": {detail}" if detail else ""))
            return True
        except Exception as e:  # report every check even if one fails
            failures += 1
            say(f"  [FAIL] {label}: {e}")
            return False

    cfg = {}

    def _cfg():
        cfg.update(config())
        return f"host={cfg['host']} warehouse_id={cfg['warehouse_id']}"

    have_cfg = check("environment + DATABRICKS_HTTP_PATH shape", _cfg)
    if have_cfg:
        check("host cloud (inference from hostname, not evidence of permissions)",
              lambda: infer_cloud(cfg["host"]))
    check("pinned requirement", pinned_requirement)

    if cli_path():
        check("databricks CLI installed", lambda: subprocess.run(
            [cli_path(), "--version"], capture_output=True, text=True).stdout.strip())
        if have_cfg:
            check("databricks CLI authenticated",
                  lambda: "user " + str(cli_json(cfg, "current-user", "me").get("userName")))
    else:
        failures += 1
        say("  [FAIL] databricks CLI installed: not found on PATH")
        print_cli_install_steps()

    if have_cfg:
        def _catalog():
            rows = sql(cfg, "SHOW CATALOGS")
            if CATALOG not in {str(r[0]).lower() for r in rows}:
                raise CheckError(
                    f"catalog {CATALOG!r} does not exist. Create it yourself (doc 05):\n"
                    f"    CREATE CATALOG IF NOT EXISTS {CATALOG};\n"
                    f"    CREATE SCHEMA IF NOT EXISTS {CATALOG}.raw;  -- .bronze .silver .gold likewise\n"
                    "    Stopping; this script never creates the catalog.")
            return f"catalog {CATALOG!r} exists"
        check(f"catalog {CATALOG}", _catalog)
    say()
    say("preflight: " + ("all checks passed" if not failures else f"{failures} check(s) FAILED"))
    return 1 if failures else 0


def cmd_q1(args):
    banner("Q1: does the dbt job task run from Git on Free Edition?")
    if not args.git_url or not args.branch:
        say("  --git-url and --branch are required. The dbt task pulls from GitHub, so first:")
        say("    1. push the repo containing Force_Balance_Pipeline/warehouse/dbt/ to GitHub yourself")
        say("    2. re-run:  python scripts/check_platform.py q1 --git-url <https url> --branch <branch>")
        say("       e.g. --git-url https://github.com/jivejong/JiveRepo --branch main")
        return 2
    cfg = config()
    say("  ensuring schema exists (the only pre-run workspace write)")
    sql(cfg, f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    ev = run_dbt_task(cfg, "phase0-q1-dbt-from-git", ["dbt deps", "dbt build"],
                      args.git_url, args.branch, args.env_version, args.project_dir)
    print_run_evidence(ev)
    say("  expected dbt summary if healthy: PASS=9 TOTAL=9 (1 seed + 2 models + 6 tests)")
    if ev["ok"]:
        write_state({"q1": {"run_id": ev["run_id"], "git_url": args.git_url, "branch": args.branch,
                            "env_version": args.env_version, "project_dir": args.project_dir,
                            "commit_sha": ev["commit_sha"]}})
        say("  RESULT: run SUCCEEDED. q3 is now unlocked (state saved to .phase0_state.json).")
        say("  Verify independently:  SELECT count(*) FROM force.phase0.phase0_model_b;  -- expect 5")
        return 0
    say("  RESULT: run did NOT succeed. Paste the evidence block back; the doc 07 fallback is a")
    say("  Python task shelling out to dbt.")
    return 1


def cmd_q2(args):
    banner("Q2: are Unity Catalog external locations to GCS permitted?")
    cfg = config()
    say(f"  host cloud (inference from hostname only): {infer_cloud(cfg['host'])}")
    creds = cli_json(cfg, "storage-credentials", "list") if cli_path() else None
    locs = cli_json(cfg, "external-locations", "list") if cli_path() else None
    if creds is None:
        print_cli_install_steps()
        say("  (read-only evidence skipped: no CLI)")
    else:
        def rows(x):
            return x if isinstance(x, list) else (x or {}).get("external_locations", []) or []
        gcs = [l for l in rows(locs) if str(l.get("url", "")).startswith("gs://")]
        say(f"  storage credentials listed: {len(rows(creds))}")
        say(f"  external locations listed:  {len(rows(locs))}")
        say(f"  of which gs:// locations:   {len(gcs)}")
        for l in gcs:
            say(f"    - {l.get('name')}: {l.get('url')}")
    if args.verify:
        if creds is None:
            return 1
        say("  RESULT (verify): " + (
            "a gs:// external location EXISTS. Paste this output back." if gcs else
            "no gs:// external location exists. Either it was refused or not attempted; paste the "
            "exact UI error text as evidence."))
        return 0 if gcs else 1
    say()
    say("  This cannot be answered by the CLI without creating metastore-level objects (storage")
    say("  credential + external location), which are outside force.phase0, so it is manual:")
    say("    1. Have a GCS bucket ready (gs://<bucket>/<path>).")
    say("    2. In the workspace UI: Catalog > External Data (gear/'External Data') >")
    say("       Credentials > Create credential. Pick the Google Cloud option if offered.")
    say("       Copy the exact error or the missing option, if any.")
    say("    3. External Locations > Create external location: URL = gs://<bucket>/<path>,")
    say("       select the credential, then run 'Test connection' if it lets you create one.")
    say("    4. Copy the exact success state or error text (a screenshot is fine).")
    say("    5. Verify:  python scripts/check_platform.py q2 --verify")
    say("  If you created objects, remove them yourself afterwards; cleanup does not touch them.")
    return 0


def cmd_q3(args):
    banner("Q3: does streaming_table build and refresh on this workspace?")
    cfg = config()
    state = read_state().get("q1")
    if not state:
        say("  q3 requires q1 to have passed. Run q1 first.")
        return 2
    run = cli_json(cfg, "jobs", "get-run", str(state["run_id"]))
    if run.get("state", {}).get("result_state") != "SUCCESS":
        say(f"  q1 run {state['run_id']} is not SUCCESS on the workspace. Re-run q1.")
        return 2
    git_url = args.git_url or state["git_url"]
    branch = args.branch or state["branch"]
    env_version = args.env_version or state.get("env_version")
    project_dir = args.project_dir if args.project_dir != DEFAULT_PROJECT_DIR else \
        state.get("project_dir", DEFAULT_PROJECT_DIR)
    say(f"  source: {git_url} @ {branch}  (phase0_streaming_check.sql must already be pushed)")

    if sql(cfg, f"SHOW TABLES IN {CATALOG}.{SCHEMA} LIKE '{STREAM_MODEL}'"):
        say(f"  {STREAM_TABLE} already exists. Run `cleanup --streaming-only` first for a clean test.")
        return 2
    sql(cfg, f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME}")
    if sql(cfg, f"LIST '{VOLUME_DIR}'"):
        say(f"  {VOLUME_DIR} is not empty. Run `cleanup --streaming-only` first.")
        return 2

    build = ["dbt build", "--select", STREAM_MODEL, "--vars", f'\'{{"{STREAM_VAR}": true}}\'']
    build_cmd = " ".join(build)  # a single command string; the var is quoted for dbt

    def landing(name, rows):
        upload_file(cfg, f"{VOLUME_DIR}/{name}", "\n".join(json.dumps(r) for r in rows).encode() + b"\n")
        say(f"  uploaded {VOLUME_DIR}/{name} ({len(rows)} rows) via Files API")

    def count():
        return int(sql(cfg, f"SELECT count(*) FROM {STREAM_TABLE}")[0][0])

    results = {}
    landing("batch_1.ndjson", BATCH_1)
    ev1 = run_dbt_task(cfg, "phase0-q3-streaming-build-1", [build_cmd], git_url, branch,
                       env_version, project_dir)
    print_run_evidence(ev1)
    if not ev1["ok"]:
        say("  RESULT: build 1 did NOT succeed; streaming_table not shown to work. Paste evidence.")
        return 1
    results["count_after_build_1"] = count()
    say(f"  rows after build 1: {results['count_after_build_1']} (expect {len(BATCH_1)})")

    say("  capturing pipeline ID for the streaming table (looking for pipeline properties)")
    pipeline_lines = []
    for stmt in (f"DESCRIBE EXTENDED {STREAM_TABLE}", f"SHOW TBLPROPERTIES {STREAM_TABLE}"):
        for row in sql(cfg, stmt):
            text = " | ".join(str(c) for c in row)
            if re.search("pipeline", text, re.I):
                pipeline_lines.append(text)
    ids = sorted({u for line in pipeline_lines for u in UUID_RE.findall(line)})
    for line in pipeline_lines:
        say(f"  pipeline-related line: {line}")
    say(f"  pipeline ID(s): {', '.join(ids) if ids else 'NOT FOUND in table metadata'}")
    if not ids:
        say("    look in the workspace UI: Jobs & Pipelines, or the table's Overview in Catalog.")

    landing("batch_2.ndjson", BATCH_2)
    ev2 = run_dbt_task(cfg, "phase0-q3-streaming-build-2", [build_cmd], git_url, branch,
                       env_version, project_dir)
    print_run_evidence(ev2)
    if not ev2["ok"]:
        say("  RESULT: build 2 (refresh) did NOT succeed. Paste evidence.")
        return 1
    results["count_after_build_2"] = count()
    say(f"  rows after build 2: {results['count_after_build_2']} "
        f"(expect {len(BATCH_1) + len(BATCH_2)})")
    grew = results["count_after_build_2"] == len(BATCH_1) + len(BATCH_2) and \
        results["count_after_build_1"] == len(BATCH_1)
    say("  RESULT: " + (
        "streaming_table built, and the second build picked up the second file (count grew)."
        if grew else
        "counts did not match expectations; streaming_table refresh NOT demonstrated. Paste output."))
    say("  Note: a YES here does not move Auto Loader into dbt; that is a later decision.")
    return 0 if grew else 1


def cmd_local(args):
    banner("local: dbt against the dev target from a repo-local .venv (you run these)")
    say("  Nothing here is global. The venv, profiles.yml and dbt logs stay inside the repo and")
    say("  are gitignored. Run from the repo root in PowerShell, with .env filled in:")
    say()
    say("    py -3 -m venv .venv")
    say("    .\\.venv\\Scripts\\Activate.ps1")
    say("    pip install -r warehouse/dbt/requirements.txt")
    say("    dbt --version                                  # record the adapter version")
    say()
    say("    # load .env into this shell (DATABRICKS_HOST without https:// for the dbt profile)")
    say("    Get-Content .env | ForEach-Object { if ($_ -match '^\\s*([^#=]+?)\\s*=\\s*(.*)$') "
        "{ Set-Item \"env:$($matches[1])\" $matches[2] } }")
    say()
    say("    if (-not (Test-Path warehouse\\dbt\\profiles.yml)) { "
        "copy warehouse\\dbt\\profiles.yml.example warehouse\\dbt\\profiles.yml }")
    say("    # edit warehouse\\dbt\\profiles.yml: replace dev_<yourname> with e.g. dev_yourname")
    say("    cd warehouse\\dbt")
    say()
    say("    # GATE 1 - the streaming toggle. Run both and compare the model lists:")
    say("    dbt ls --resource-type model --profiles-dir .")
    say("    dbt ls --resource-type model --profiles-dir . --vars \"{enable_streaming_check: true}\"")
    say("    # Expected: 2 models, then 3 (the third is phase0_streaming_check).")
    say("    # If both lists have the same count, STOP: the enable toggle is broken and")
    say("    # q1 must NOT be run. Paste both outputs back.")
    say()
    say("    dbt debug --target dev --profiles-dir .")
    say("    dbt build --target dev --exclude phase0.streaming --profiles-dir .")
    say()
    say("  (--profiles-dir . is added because the profile lives in the repo, not ~/.dbt. The")
    say("  streaming model is already disabled by default; the exclude is belt and braces.)")
    say("  The --vars value is written as YAML flow, {enable_streaming_check: true}, rather than")
    say("  JSON: Windows PowerShell 5.1 strips the double quotes from '{\"a\": true}' before dbt")
    say("  sees it, which would make a working toggle look broken. dbt reads --vars as YAML.")
    say()
    say("  Verify: dbt debug ends with 'All checks passed!'; dbt build ends with")
    say("    Done. PASS=9 WARN=0 ERROR=0 SKIP=0 TOTAL=9   (1 seed + 2 models + 6 tests)")
    say("  and in the workspace:  SELECT count(*) FROM force.dev_<yourname>.phase0_model_b;  -- 5")
    say("  Paste the `dbt --version`, both `dbt ls`, `dbt debug` and `dbt build` output back.")
    say()
    say("  Cleanup: `cleanup` does NOT drop force.dev_<yourname>. Drop it yourself:")
    say("    DROP SCHEMA IF EXISTS force.dev_<yourname> CASCADE;")
    return 0


def cmd_cleanup(args):
    banner("cleanup")
    cfg = config()
    if args.streaming_only:
        stmts = [f"DROP TABLE IF EXISTS {STREAM_TABLE}",
                 f"DROP VOLUME IF EXISTS {CATALOG}.{SCHEMA}.{VOLUME}"]
    else:
        stmts = [f"DROP SCHEMA IF EXISTS {CATALOG}.{SCHEMA} CASCADE"]
    say("  will run:")
    for s in stmts:
        say(f"    {s}")
    if not args.yes and input("  type 'yes' to proceed: ").strip().lower() != "yes":
        say("  aborted")
        return 1
    for s in stmts:
        sql(cfg, s)
        say(f"  done: {s}")
    if not args.streaming_only and STATE_FILE.exists():
        STATE_FILE.unlink()
        say("  removed .phase0_state.json (q3 will require q1 again)")
    say("  Not touched: force.dev_<yourname> (local dbt runs) and any external location or")
    say("  storage credential from Q2. Remove those yourself.")
    return 0


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight").set_defaults(fn=cmd_preflight)
    for name, fn in (("q1", cmd_q1), ("q3", cmd_q3)):
        sp = sub.add_parser(name)
        sp.add_argument("--git-url", help="https URL of the pushed GitHub repo")
        sp.add_argument("--branch", help="branch that contains the dbt project")
        sp.add_argument("--project-dir", default=DEFAULT_PROJECT_DIR,
                        help="dbt project directory relative to the Git repo root "
                        f"(default: {DEFAULT_PROJECT_DIR})")
        sp.add_argument("--env-version", help="serverless environment_version (not documented "
                        "in the sources I checked; omitted if not given)")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("q2")
    sp.add_argument("--verify", action="store_true")
    sp.set_defaults(fn=cmd_q2)
    sub.add_parser("local").set_defaults(fn=cmd_local)
    sp = sub.add_parser("cleanup")
    sp.add_argument("--streaming-only", action="store_true")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_cleanup)
    args = p.parse_args()
    try:
        sys.exit(args.fn(args))
    except CheckError as e:
        say(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
