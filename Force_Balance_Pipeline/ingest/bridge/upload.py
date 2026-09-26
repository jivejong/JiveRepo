"""The upload core shared by the live bridge and the backfill (docs 02, 04): retry, backoff and 409
semantics, plus the restart-safe pieces the backfill needs. Standard library only.

Two kinds of upload, one difference in what a 409 (the path already exists) means:

  live (deterministic=False): the file name has a fresh random ULID per batch.
    * a 409 after an attempt whose outcome is unknown (network error, HTTP 5xx) means THAT attempt landed:
      the batch counts as uploaded.
    * a 409 with no unknown attempt (a first attempt, or after a 429) is a real name collision: the batch
      is re-keyed with a new ULID and tried again (at most 3 times).
  backfill (deterministic=True): the file name is derived from the data, so the same file always has the
    same path. A 409 on ANY attempt means it is already there (an earlier attempt, or an earlier run that
    was interrupted): skip it, never re-key. That is what makes a restarted backfill upload produce no
    duplicate files.

The backfill also needs its dt=/hh= prefix to be stable across restarts. The prefix is ingest time (doc 02),
so a naive "now" at each run would put a restarted upload's files in a different directory under the same
file name, and Auto Loader would load them twice. fixed_prefix() assigns the prefix once and keeps it in a
state file; it refuses to reuse the prefix for a different backfill (a different run_id).
"""
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from files_api import CONFLICT, SUCCESS, AuthError, TransportError

RETRY_UNKNOWN_OUTCOME = (500, 502, 503, 504)  # the server may have written the file before failing
RETRY_NOT_PROCESSED = (429,)                  # rejected before processing: the file was not written
DEFAULT_BACKOFF = (1, 2, 4, 8, 16)
MAX_REKEYS = 3

UPLOADED, ALREADY_LANDED, FAILED = "uploaded", "already_landed", "failed"


@dataclass
class Result:
    outcome: str
    relpath: str
    attempts: int = 0
    retries: int = 0
    rekeys: int = 0
    landed_before_retry: bool = False  # a 409 that followed an attempt of this same upload
    reason: str = ""


def upload_file(uploader, relpath, data, *, deterministic, rekey=None, max_attempts=5, backoff=DEFAULT_BACKOFF,
                sleep=time.sleep, log=print):
    """Upload one file and return a Result. `rekey()` (live only) returns a new relpath after a real collision."""
    attempt = retries = rekeys = 0
    unknown_outcome = False
    while True:
        attempt += 1
        status = None
        try:
            status = uploader.put(relpath, data)
        except (TransportError, AuthError) as e:
            log(f"upload: attempt {attempt} of {relpath} failed: {e}")
            if isinstance(e, TransportError):
                unknown_outcome = True
        if status in SUCCESS:
            return Result(UPLOADED, relpath, attempt, retries, rekeys)
        if status == CONFLICT:
            if deterministic or unknown_outcome:
                return Result(ALREADY_LANDED, relpath, attempt, retries, rekeys, landed_before_retry=unknown_outcome)
            rekeys += 1
            if rekey is None or rekeys > MAX_REKEYS:
                return Result(FAILED, relpath, attempt, retries, rekeys, reason="name collision persisted after 3 new ULIDs")
            relpath = rekey()
            unknown_outcome = False
            continue
        if status in RETRY_UNKNOWN_OUTCOME:
            unknown_outcome = True
        elif status is not None and status not in RETRY_NOT_PROCESSED:
            return Result(FAILED, relpath, attempt, retries, rekeys, reason=f"HTTP {status}")  # permanent
        if attempt >= max_attempts:
            reason = f"gave up after {attempt} attempts" + (f" (last HTTP {status})" if status else "")
            return Result(FAILED, relpath, attempt, retries, rekeys, reason=reason)
        retries += 1
        sleep(backoff[min(attempt - 1, len(backoff) - 1)])


# ---- backfill ---------------------------------------------------------------------------------------
class PrefixConflict(Exception):
    """The saved dt/hh belongs to a different backfill; uploading this one under it would skip its files."""


def fixed_prefix(state_path, now, run_id):
    """(dt, hh) for a backfill upload, assigned at first use and reused by every restart of the SAME run.
    run_id identifies the data (for example the manifest's content hash)."""
    state_path = Path(state_path)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["run_id"] != run_id:
            raise PrefixConflict(f"{state_path} holds the prefix of backfill run {state['run_id']}, not {run_id}. "
                                 "A regenerated backfill must not reuse it (its files would be skipped as already "
                                 "landed); remove the state file only after clearing the landed files")
        return state["dt"], state["hh"]
    moment = datetime.fromtimestamp(now, timezone.utc)
    state = {"run_id": run_id, "dt": f"{moment:%Y-%m-%d}", "hh": f"{moment:%H}",
             "assigned_utc": moment.strftime("%Y-%m-%dT%H:%M:%SZ")}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_name(state_path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, state_path)
    return state["dt"], state["hh"]


def backfill_relpath(dt, hh, source_id, ulid):
    return f"dt={dt}/hh={hh}/{source_id}-{ulid}.ndjson"


@dataclass
class TreeSummary:
    uploaded: int = 0
    already_landed: int = 0
    failed: int = 0
    retries: int = 0


def upload_tree(uploader, items, *, max_attempts=5, backoff=DEFAULT_BACKOFF, sleep=time.sleep, log=print,
                progress=None):
    """Upload (relpath, data) pairs with deterministic names. `data` may be bytes or a callable returning
    bytes (so a large backfill is read one file at a time). Safe to rerun after any interruption: files
    that already landed come back 409 and are skipped. Failures are counted and the run continues."""
    summary = TreeSummary()
    for relpath, data in items:
        payload = data() if callable(data) else data
        result = upload_file(uploader, relpath, payload, deterministic=True, max_attempts=max_attempts,
                             backoff=backoff, sleep=sleep, log=log)
        summary.retries += result.retries
        if result.outcome == UPLOADED:
            summary.uploaded += 1
        elif result.outcome == ALREADY_LANDED:
            summary.already_landed += 1
        else:
            summary.failed += 1
            log(f"upload: gave up on {relpath} ({result.reason})")
        if progress:
            progress(relpath, result, summary)
    return summary
