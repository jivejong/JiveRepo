"""services.console.finale — the finale's background pipeline state
machine. The pipeline's real work (a real dbt build subprocess, a real
triage call) is verified live against the real stack (Phase 10's
checkpoint), the same posture `services/triage/__main__.py`'s own
orchestration has never had a dedicated unit test - what's checked here
deterministically is the part a live run can't easily force on demand:
that a stage which never completes actually terminates as `failed`
instead of polling forever, with a real, non-empty degraded script.
"""

import subprocess
import threading
import time

import pytest

import services.console.finale as finale


class _FakeProducer:
    def produce(self, topic, key, value, callback=None):
        pass

    def poll(self, timeout=0):
        pass


def test_finale_result_is_terminal_only_on_ready_or_failed():
    assert finale.FinaleResult(state="pending").terminal is False
    assert finale.FinaleResult(state="ingesting").terminal is False
    assert finale.FinaleResult(state="transforming").terminal is False
    assert finale.FinaleResult(state="scoring").terminal is False
    assert finale.FinaleResult(state="attributing").terminal is False
    assert finale.FinaleResult(state="ready").terminal is True
    assert finale.FinaleResult(state="failed").terminal is True


def test_a_session_that_never_lands_fails_rather_than_polling_forever(monkeypatch, tmp_path):
    """The real failure mode this stage exists to catch: bound the ingest
    wait to something a test can actually wait out, point it at a warehouse
    file with no attack_run directory at all (a real case, not contrived -
    a fresh clone before anything has landed), and confirm the pipeline
    reaches `failed` with a degraded script rather than hanging."""
    monkeypatch.setattr(finale, "INGEST_TIMEOUT_S", 0.3)
    monkeypatch.setattr(finale, "INGEST_POLL_INTERVAL_S", 0.1)
    monkeypatch.setattr(finale, "WAREHOUSE_PATH", tmp_path / "warehouse.duckdb")
    monkeypatch.setattr(finale, "REPO_ROOT", tmp_path)

    results = []
    finale.run_finale_pipeline(
        session_id="never-lands",
        run_id="r1",
        kafka_producer=_FakeProducer(),
        kafka_topic="attack.events",
        llm_client=None,
        on_state_change=results.append,
    )

    assert [r.state for r in results] == ["ingesting", "failed"]
    final = results[-1]
    assert final.terminal is True
    assert "never landed" in final.reason
    assert len(final.lines) > 0
    assert all(line["attributed_villain_slug"] is None for line in final.lines)


def test_concurrent_finales_never_run_the_warehouse_phases_at_once(monkeypatch, tmp_path):
    """The 2026-10-08 collision (docs/09): two sessions finishing seconds
    apart each started a finale, and their dbt runs overlapped on the
    single-writer warehouse, so the second failed. Two finales started
    together must take the warehouse phases one at a time."""
    monkeypatch.setattr(finale, "WAREHOUSE_PATH", tmp_path / "warehouse.duckdb")
    monkeypatch.setattr(finale, "_session_landed", lambda con, session_id: True)

    active = 0
    max_active = 0
    counter_lock = threading.Lock()

    def fake_dbt_run():
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.3)
        with counter_lock:
            active -= 1
        raise RuntimeError("stop after dbt")

    monkeypatch.setattr(finale, "_run_dbt_build", fake_dbt_run)

    finals = {}

    def run(session_id):
        def record(result):
            finals[session_id] = result

        finale.run_finale_pipeline(
            session_id=session_id,
            run_id="r",
            kafka_producer=_FakeProducer(),
            kafka_topic="attack.events",
            llm_client=None,
            on_state_change=record,
        )

    threads = [threading.Thread(target=run, args=(sid,)) for sid in ("first", "second")]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10)

    assert max_active == 1
    assert {r.state for r in finals.values()} == {"failed"}
    assert all("stop after dbt" in r.reason for r in finals.values())
    # Released on every path, so a later finale isn't left waiting.
    assert finale._warehouse_lock.acquire(blocking=False)
    finale._warehouse_lock.release()


def test_a_finale_waiting_too_long_for_the_warehouse_fails_as_busy(monkeypatch, tmp_path):
    """Waiting for another finale is bounded, like every other stage."""
    monkeypatch.setattr(finale, "WAREHOUSE_PATH", tmp_path / "warehouse.duckdb")
    monkeypatch.setattr(finale, "_session_landed", lambda con, session_id: True)
    monkeypatch.setattr(finale, "WAREHOUSE_LOCK_TIMEOUT_S", 0.2)

    results = []
    assert finale._warehouse_lock.acquire(timeout=1)
    try:
        finale.run_finale_pipeline(
            session_id="waits",
            run_id="r",
            kafka_producer=_FakeProducer(),
            kafka_topic="attack.events",
            llm_client=None,
            on_state_change=results.append,
        )
    finally:
        finale._warehouse_lock.release()

    assert [r.state for r in results] == ["ingesting", "transforming", "failed"]
    assert "warehouse busy" in results[-1].reason


def test_dbt_failure_reason_includes_dbt_stdout(monkeypatch):
    """dbt writes its errors to stdout. Keeping only stderr left the
    2026-10-08 failures with an empty reason."""
    esc = chr(27)
    stdout = esc + "[0m14:16:07  Encountered an error:" + chr(10) + "IO Error: Could not set lock"
    completed = subprocess.CompletedProcess(args=[], returncode=2, stdout=stdout, stderr="")
    monkeypatch.setattr(finale.subprocess, "run", lambda *args, **kwargs: completed)

    with pytest.raises(RuntimeError) as excinfo:
        finale._run_dbt_build()

    message = str(excinfo.value)
    assert "exit 2" in message
    assert "Could not set lock" in message
    assert esc not in message
