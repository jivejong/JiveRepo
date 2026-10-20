"""Job 1's latest successful run, for the publish gate (doc 05 "Serving (Phase 7)": "Gate"). Read-only; costs no warehouse time.

agent.jobs has only job1_active (no module-level HTTP helper), so this calls files_api.http_request directly, as the agent does.
Error messages carry no URL, host or token.
"""
import json
import sys
from datetime import datetime, timezone

from serving.config import ROOT

sys.path.insert(0, str(ROOT / "ingest" / "bridge"))      # files_api: the one outbound HTTP path

import files_api  # noqa: E402


class JobsApiError(Exception):
    """The Jobs API answer could not be used."""


def _succeeded(run):
    state = run.get("state") if isinstance(run.get("state"), dict) else {}
    if state.get("result_state"):
        return state["result_state"] == "SUCCESS"
    status = run.get("status") if isinstance(run.get("status"), dict) else {}
    details = status.get("termination_details") if isinstance(status.get("termination_details"), dict) else {}
    return details.get("code") == "SUCCESS"


def latest_successful_run(env, job_id, *, opener=None):
    """(run_id, end_time as an aware UTC datetime) of the newest successful run of the job, or None if none of the last 25 finished runs
    succeeded. Raises JobsApiError on a non-200, bad JSON or an unexpected shape."""
    kwargs = {} if opener is None else {"opener": opener}
    try:
        url = f"{files_api.normalise_host(env['DATABRICKS_HOST'])}/api/2.1/jobs/runs/list?job_id={int(job_id)}&completed_only=true&limit=25"
        status, body = files_api.http_request("GET", url, headers={"Authorization": f"Bearer {env['DATABRICKS_TOKEN']}"}, **kwargs)
    except files_api.TransportError as e:
        raise JobsApiError(f"the Jobs API could not be reached ({e})") from None
    if status != 200:
        raise JobsApiError(f"the Jobs API answered HTTP {status}")
    try:
        data = json.loads(body)
    except ValueError:
        raise JobsApiError("the Jobs API answer is not JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        raise JobsApiError("the Jobs API answer has no runs list")
    best = None
    for run in data["runs"]:
        if not isinstance(run, dict) or not _succeeded(run):
            continue
        end, run_id = run.get("end_time"), run.get("run_id")
        if not isinstance(end, int) or isinstance(end, bool) or not isinstance(run_id, int) or end <= 0:
            continue
        if best is None or end > best[1]:
            best = (run_id, end)
    if best is None:
        return None
    return best[0], datetime.fromtimestamp(best[1] / 1000, tz=timezone.utc)
