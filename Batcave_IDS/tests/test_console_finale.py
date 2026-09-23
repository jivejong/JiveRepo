"""services.console.finale — the finale's background pipeline state
machine. The pipeline's real work (a real dbt build subprocess, a real
triage call) is verified live against the real stack (Phase 10's
checkpoint), the same posture `services/triage/__main__.py`'s own
orchestration has never had a dedicated unit test - what's checked here
deterministically is the part a live run can't easily force on demand:
that a stage which never completes actually terminates as `failed`
instead of polling forever, with a real, non-empty degraded script.
"""

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
