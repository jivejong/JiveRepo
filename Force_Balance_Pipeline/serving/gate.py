"""The publish gate (doc 05 "Serving (Phase 7)": "Gate"): pull only when Job 1 has a newer successful run than the last live publish.

decide() is pure. latest and last_ok are (run_id, end) or None; end is an aware datetime. last_live_ok() reads serving.publish_log:
a 'fixture' row or a failed or skipped row never counts.
"""


def decide(now_utc, latest, last_ok):
    """One of "skip_quota_hour", "skip_no_success", "skip_no_new_run", "publish", checked in that order."""
    if now_utc.hour == 3:
        return "skip_quota_hour"
    if latest is None:
        return "skip_no_success"
    if last_ok is None:
        return "publish"
    if latest[0] == last_ok[0] or latest[1] <= last_ok[1]:
        return "skip_no_new_run"
    return "publish"


def last_live_ok(conn):
    """(job1_run_id, job1_run_end) of the newest 'live' publish_log row with status 'ok', or None."""
    row = conn.execute("SELECT job1_run_id, job1_run_end FROM serving.publish_log WHERE kind = 'live' AND status = 'ok' "
                       "AND job1_run_id IS NOT NULL ORDER BY started_at DESC, publish_id DESC LIMIT 1").fetchone()
    return None if row is None else (row[0], row[1])
