"""Jobs API check: is Job 1 running right now? (doc 06 "Runtime"). It costs no warehouse time.

Any failure answers "yes, skip" so the cycle is skipped safely rather than run against a warehouse Job 1 may be using.
"""
import json
import sys

from agent.config import JOB1_ID, ROOT

sys.path.insert(0, str(ROOT / "ingest" / "bridge"))      # files_api: the one outbound HTTP path

import files_api  # noqa: E402


def job1_active(host, token, *, opener):
    """(True, "job1_running"), (False, "idle") or (True, "jobs_api_error")."""
    try:
        url = f"{files_api.normalise_host(host)}/api/2.1/jobs/runs/list?job_id={JOB1_ID}&active_only=true"
        status, body = files_api.http_request("GET", url, headers={"Authorization": f"Bearer {token}"}, opener=opener)
        if status != 200:
            return True, "jobs_api_error"
        data = json.loads(body)
        if not isinstance(data, dict):
            return True, "jobs_api_error"
        runs = data.get("runs")
        if isinstance(runs, list) and runs:
            return True, "job1_running"
        return False, "idle"
    except Exception:
        return True, "jobs_api_error"
