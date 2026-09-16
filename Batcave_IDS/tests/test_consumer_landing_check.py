"""services.consumer.landing_check — built against synthetic parquet fixtures
(no broker), so the DuckDB queries can be checked without running a corpus.
"""

from datetime import UTC, datetime

import pyarrow.parquet as pq

from services.consumer.landing_check import report
from services.consumer.writer import write_parquet


def _land(tmp_path, event_kind, rows):
    write_parquet(tmp_path, (event_kind, "2026-09-16", "13"), rows)


def _request(event_id, session_id, path, run_id, offset):
    return {
        "event_id": event_id,
        "event_kind": "request",
        "run_id": run_id,
        "session_id": session_id,
        "received_at": datetime(2026, 9, 16, 13, 0, tzinfo=UTC).isoformat(),
        "http_method": "GET",
        "path": path,
        "path_tier": 1,
        "body_bytes": 0,
        "status_returned": 200,
        "response_time_ms": 1.0,
        "headers": "{}",
        "kafka_partition": 0,
        "kafka_offset": offset,
        "landed_at": datetime(2026, 9, 16, 13, 0, 1, tzinfo=UTC).isoformat(),
    }


def _run(run_id, villain_slug, offset):
    return {
        "event_id": f"run-{run_id}",
        "event_kind": "attack_run",
        "run_id": run_id,
        "session_id": f"s-{run_id}",
        "received_at": datetime(2026, 9, 16, 13, 0, tzinfo=UTC).isoformat(),
        "villain_slug": villain_slug,
        "started_at": datetime(2026, 9, 16, 13, 0, tzinfo=UTC).isoformat(),
        "ended_at": datetime(2026, 9, 16, 13, 1, tzinfo=UTC).isoformat(),
        "duration_s": 60.0,
        "requests_sent": 1,
        "attempts_made": 1,
        "max_stage_reached": 1,
        "run_outcome": "stalled",
        "pathologies_enabled": [],
        "timing_compression_factor": 1.0,
        "kafka_partition": 1,
        "kafka_offset": offset,
        "landed_at": datetime(2026, 9, 16, 13, 0, 1, tzinfo=UTC).isoformat(),
    }


def test_replayed_duplicate_and_twoface_duplicate_are_counted_separately(tmp_path, capsys):
    """The two populations the checkpoint must distinguish, in one corpus."""
    _land(
        tmp_path,
        "request",
        [
            # A replayed/delivery duplicate: identical event_id.
            _request("e1", "s-croc", "/login", "run-croc", offset=0),
            _request("e1", "s-croc", "/login", "run-croc", offset=5),
            # Two-Face: same session+path, distinct event_ids.
            _request("e2", "s-tf", "/vault", "run-tf", offset=1),
            _request("e3", "s-tf", "/vault", "run-tf", offset=2),
        ],
    )
    _land(tmp_path, "attempt", [])
    _land(
        tmp_path,
        "attack_run",
        [_run("run-croc", "386-killer-croc", 0), _run("run-tf", "678-two-face", 1)],
    )

    ok = report(tmp_path, check_topic=False)
    out = capsys.readouterr().out

    assert "same event_id, >1 row (delivery dup + replay):     1" in out
    assert "Two-Face, same (session, path), distinct event_ids:      1" in out
    assert ok is True


def test_a_retrying_non_twoface_villain_does_not_inflate_the_twoface_count(tmp_path, capsys):
    """The bug this test guards: 'same session+path, distinct event_ids' also
    matches ordinary retries (Croc has the highest retry_ratio, docs/03) - the
    Two-Face count must be scoped to Two-Face's villain_slug, not just the shape."""
    _land(
        tmp_path,
        "request",
        [
            _request("e1", "s-croc", "/login", "run-croc", offset=0),
            _request("e2", "s-croc", "/login", "run-croc", offset=1),  # a real retry
            _request("e3", "s-croc", "/login", "run-croc", offset=2),  # another retry
        ],
    )
    _land(tmp_path, "attempt", [])
    _land(tmp_path, "attack_run", [_run("run-croc", "386-killer-croc", 0)])

    report(tmp_path, check_topic=False)
    out = capsys.readouterr().out

    assert "Two-Face, same (session, path), distinct event_ids:      0" in out


def test_a_duplicated_attack_run_row_does_not_fan_out_the_villain_join(tmp_path, capsys):
    """The bug found by hand during the restart exercise: attack_run itself can
    be duplicated by a replay, and joining request.run_id straight to a
    duplicated attack_run row multiplies every request's count by the number of
    attack_run copies. The villain lookup must DISTINCT run_id first."""
    _land(
        tmp_path,
        "request",
        [
            _request("e2", "s-tf", "/vault", "run-tf", offset=1),
            _request("e3", "s-tf", "/vault", "run-tf", offset=2),
        ],
    )
    _land(tmp_path, "attempt", [])
    # run-tf's attack_run landed twice (a replay), same run_id both times.
    _land(
        tmp_path,
        "attack_run",
        [_run("run-tf", "678-two-face", 1), _run("run-tf", "678-two-face", 99)],
    )

    report(tmp_path, check_topic=False)
    out = capsys.readouterr().out

    # Without the DISTINCT, this pair would be counted twice (fanned out by
    # the two attack_run copies) and the request row-count line would also
    # double. One villain-scoped pair, not two.
    assert "Two-Face, same (session, path), distinct event_ids:      1" in out


def test_quarantine_survival_check_flags_when_nothing_landed_after_it(tmp_path, capsys):
    """The inverse of the real result: if nothing landed with a higher offset
    on the same partition, the consumer did NOT continue - must fail loudly."""
    _land(tmp_path, "request", [_request("e1", "s1", "/x", "run-1", offset=0)])
    _land(tmp_path, "attempt", [])
    _land(tmp_path, "attack_run", [_run("run-1", "60-bane", 0)])

    quarantine_dir = tmp_path / "quarantine" / "undeserializable"
    quarantine_dir.mkdir(parents=True)
    # A quarantined offset with nothing after it on partition 7 - no request
    # or attempt row anywhere carries kafka_partition=7.
    (quarantine_dir / "partition=7-offset=999.json").write_text(
        '{"kafka_partition": 7, "kafka_offset": 999}', encoding="utf-8"
    )

    ok = report(tmp_path, check_topic=False)
    out = capsys.readouterr().out

    assert "NOTHING LANDED AFTER IT" in out
    assert ok is False


def test_nothing_landed_is_a_clean_failure_not_a_crash(tmp_path):
    assert report(tmp_path, check_topic=False) is False


def test_partition_mismatch_is_detected(tmp_path, capsys):
    """A row whose dt/hour don't match its received_at - guards the check
    itself against the DuckDB local-timezone trap (see module docstring)."""
    row = _request("e1", "s1", "/x", "run-1", offset=0)
    row["received_at"] = datetime(2026, 9, 16, 20, 0, tzinfo=UTC).isoformat()  # not hour=13
    _land(tmp_path, "request", [row])
    _land(tmp_path, "attempt", [])
    _land(tmp_path, "attack_run", [_run("run-1", "60-bane", 0)])

    ok = report(tmp_path, check_topic=False)
    out = capsys.readouterr().out

    assert "rows whose dt/hour disagree with received_at: 1" in out
    assert ok is False


def test_landed_rows_are_read_through_duckdb_hive_partitioning(tmp_path):
    """Confidence check that the fixtures actually exercise real parquet +
    DuckDB, not just in-memory rows."""
    _land(tmp_path, "request", [_request("e1", "s1", "/x", "run-1", offset=0)])
    files = list((tmp_path / "raw" / "request").rglob("part-*.parquet"))
    assert len(files) == 1
    assert pq.read_table(files[0]).num_rows == 1
