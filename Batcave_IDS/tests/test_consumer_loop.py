"""services.consumer.consumer — the poll loop, driven by a fake broker.

The load-bearing test here is the flush-then-commit ORDER. A consumer that
commits first and writes after passes every other test in this file and loses a
batch on every crash, so the order is asserted directly (via an operation log)
and the real proof is the kill-mid-batch exercise in docs/exercises.md.
"""

import json
from datetime import UTC, datetime

import pytest

from services.consumer import consumer as consumer_module
from services.consumer.consumer import LandingConsumer


class _FakeMessage:
    def __init__(self, value, partition=0, offset=0, key=b"s1", error=None):
        self._value = value
        self._partition = partition
        self._offset = offset
        self._key = key
        self._error = error

    def value(self):
        return self._value

    def partition(self):
        return self._partition

    def offset(self):
        return self._offset

    def key(self):
        return self._key

    def error(self):
        return self._error


class _FakeConsumer:
    """Returns queued messages, then None forever (the poll-timeout case)."""

    def __init__(self, messages, log=None):
        self.messages = list(messages)
        self.commits = 0
        self.closed = False
        self.log = log if log is not None else []

    def poll(self, timeout):
        return self.messages.pop(0) if self.messages else None

    def commit(self, asynchronous=True):
        self.commits += 1
        self.log.append("commit")

    def close(self):
        self.closed = True


def _event(event_id="e1", event_kind="request", received_at="2026-09-15T13:00:00Z", **extra):
    payload = {
        "event_id": event_id,
        "event_kind": event_kind,
        "session_id": "s1",
        "received_at": received_at,
        "schema_version": "v1",
        "http_method": "GET",
        "path": "/x",
        "path_tier": 1,
        "body_bytes": 0,
        "status_returned": 200,
        "response_time_ms": 1.0,
        "headers": "{}",
    }
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


def _drain(landing):
    """Run the loop until the fake broker is empty, then stop."""
    while landing.consumer.messages:
        message = landing.consumer.poll(1.0)
        if message is not None:
            landing.handle(message)
        if landing.should_flush():
            landing.flush_and_commit()
    landing.flush_and_commit()


# -- the ordering invariant -----------------------------------------------


def test_every_write_completes_before_the_offset_commit(tmp_path, monkeypatch):
    """The invariant: all Parquet and quarantine writes land, THEN commit."""
    log = []
    real_parquet = consumer_module.write_parquet
    real_quarantine = consumer_module.write_quarantine

    def logged_parquet(*args, **kwargs):
        log.append("write_parquet")
        return real_parquet(*args, **kwargs)

    def logged_quarantine(*args, **kwargs):
        log.append("write_quarantine")
        return real_quarantine(*args, **kwargs)

    monkeypatch.setattr(consumer_module, "write_parquet", logged_parquet)
    monkeypatch.setattr(consumer_module, "write_quarantine", logged_quarantine)

    messages = [
        _FakeMessage(_event("e1"), offset=0),
        _FakeMessage(b"\x00\x01not-json\xff", offset=1),
        _FakeMessage(_event("e2", event_kind="attempt"), offset=2),
    ]
    landing = LandingConsumer(_FakeConsumer(messages, log), tmp_path)
    _drain(landing)

    assert log[-1] == "commit", "commit must be the last operation in a flush"
    assert log.count("commit") == 1
    assert "write_parquet" in log and "write_quarantine" in log
    assert all(entry != "commit" for entry in log[:-1])


def test_a_failed_write_prevents_the_commit(tmp_path, monkeypatch):
    """Failure mode must be duplicate data on replay, never lost data. If the
    write raises, the offset must NOT advance."""

    def exploding_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(consumer_module, "write_parquet", exploding_write)

    fake = _FakeConsumer([_FakeMessage(_event(), offset=0)])
    landing = LandingConsumer(fake, tmp_path)
    landing.handle(fake.poll(1.0))

    with pytest.raises(OSError):
        landing.flush_and_commit()
    assert fake.commits == 0, "offsets committed despite a failed flush - data would be lost"


def test_nothing_buffered_means_no_commit_and_no_empty_file(tmp_path):
    fake = _FakeConsumer([])
    landing = LandingConsumer(fake, tmp_path)
    landing.flush_and_commit()

    assert fake.commits == 0
    assert not (tmp_path / "raw").exists()


# -- flush triggers --------------------------------------------------------


def test_flush_fires_on_the_message_threshold(tmp_path):
    landing = LandingConsumer(_FakeConsumer([]), tmp_path, flush_max_messages=3)
    for offset in range(2):
        landing.handle(_FakeMessage(_event(f"e{offset}"), offset=offset))
    assert landing.should_flush() is False

    landing.handle(_FakeMessage(_event("e2"), offset=2))
    assert landing.should_flush() is True


def test_flush_fires_on_the_time_interval(tmp_path):
    now = [100.0]
    landing = LandingConsumer(
        _FakeConsumer([]),
        tmp_path,
        flush_max_messages=5_000,
        flush_interval_s=30.0,
        clock=lambda: now[0],
    )
    landing.handle(_FakeMessage(_event(), offset=0))
    assert landing.should_flush() is False

    now[0] += 30.0
    assert landing.should_flush() is True


def test_the_message_threshold_counts_across_event_kinds(tmp_path):
    """One flush and one commit cover the whole window, not one per kind."""
    messages = [
        _FakeMessage(_event("e1", event_kind="request"), offset=0),
        _FakeMessage(_event("e2", event_kind="attempt"), offset=1),
        _FakeMessage(_event("e3", event_kind="attack_run"), offset=2),
    ]
    fake = _FakeConsumer(messages)
    landing = LandingConsumer(fake, tmp_path, flush_max_messages=3)
    _drain(landing)

    assert fake.commits == 1
    assert landing.flushes == 1
    for kind in ("request", "attempt", "attack_run"):
        assert list((tmp_path / "raw" / kind).rglob("part-*.parquet"))


# -- undeserializable: the consumer must continue --------------------------


def test_undeserializable_is_quarantined_and_the_loop_continues(tmp_path):
    """docs/03 row 6. The messages AFTER the bad one must still land - that's
    what 'continues rather than halting' means."""
    messages = [
        _FakeMessage(_event("e1"), offset=0),
        _FakeMessage(b"\x00\x01not-json\xff", offset=1),
        _FakeMessage(_event("e2"), offset=2),
    ]
    fake = _FakeConsumer(messages)
    landing = LandingConsumer(fake, tmp_path)
    _drain(landing)

    quarantine_dir = tmp_path / "quarantine" / "undeserializable"
    assert (quarantine_dir / "partition=0-offset=1.json").exists()
    assert landing.rows_landed == 2
    assert landing.rows_quarantined == 1
    assert fake.commits == 1, "the bad message must not hold back the offset"


def test_a_json_scalar_is_treated_as_undeserializable(tmp_path):
    """Valid JSON that isn't an object can't be a row; quarantine rather than
    crash on the first attribute access."""
    fake = _FakeConsumer([_FakeMessage(b'"just a string"', offset=5)])
    landing = LandingConsumer(fake, tmp_path)
    _drain(landing)

    assert (tmp_path / "quarantine" / "undeserializable" / "partition=0-offset=5.json").exists()
    assert landing.rows_quarantined == 1


def test_a_kafka_error_message_is_skipped_without_buffering(tmp_path):
    fake = _FakeConsumer([_FakeMessage(_event(), error="broker transport failure")])
    landing = LandingConsumer(fake, tmp_path)
    _drain(landing)

    assert landing.buffered == 0
    assert fake.commits == 0


# -- partitioning ----------------------------------------------------------


def test_rows_are_partitioned_by_received_at_not_client_ts(tmp_path):
    """The late-arrival pathology backdates client_ts by hours; it must not
    move the partition (hard constraint)."""
    payload = _event(received_at="2026-09-15T13:00:00Z", client_ts="2026-09-15T07:00:00Z")
    fake = _FakeConsumer([_FakeMessage(payload, offset=0)])
    landing = LandingConsumer(fake, tmp_path)
    _drain(landing)

    assert (tmp_path / "raw" / "request" / "dt=2026-09-15" / "hour=13").is_dir()
    assert not (tmp_path / "raw" / "request" / "dt=2026-09-15" / "hour=07").exists()


def test_future_received_at_lands_rather_than_being_quarantined(tmp_path):
    """The clock-skew pathology puts received_at an hour ahead. Quarantining
    that is staging's job in Phase 5 (docs/02), not the consumer's - the
    consumer lands what it saw."""
    payload = _event(received_at="2099-01-01T05:00:00Z")
    fake = _FakeConsumer([_FakeMessage(payload, offset=0)])
    landing = LandingConsumer(fake, tmp_path)
    _drain(landing)

    assert (tmp_path / "raw" / "request" / "dt=2099-01-01" / "hour=05").is_dir()
    assert landing.rows_quarantined == 0


def test_unusable_received_at_falls_back_to_landed_at(tmp_path):
    """Guard against the halt-on-bad-data failure: one null received_at must
    not crash the batch."""
    fake = _FakeConsumer([_FakeMessage(_event(received_at=None), offset=0)])
    landing = LandingConsumer(fake, tmp_path)
    _drain(landing)

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert (tmp_path / "raw" / "request" / f"dt={today}").is_dir()
    assert landing.rows_landed == 1


def test_consumer_columns_are_stamped_on_every_row(tmp_path):
    import pyarrow.parquet as pq

    fake = _FakeConsumer([_FakeMessage(_event(), partition=2, offset=99)])
    landing = LandingConsumer(fake, tmp_path)
    _drain(landing)

    table = pq.read_table(next((tmp_path / "raw").rglob("part-*.parquet")))
    assert table.column("kafka_partition")[0].as_py() == 2
    assert table.column("kafka_offset")[0].as_py() == 99
    assert table.column("landed_at")[0].as_py() is not None


# -- shutdown --------------------------------------------------------------


def test_graceful_stop_drains_the_buffer_before_exit(tmp_path):
    """SIGTERM must land what's buffered. Only SIGKILL should cause a replay."""
    fake = _FakeConsumer([_FakeMessage(_event(), offset=0)])
    landing = LandingConsumer(fake, tmp_path)

    landing.handle(fake.poll(1.0))
    landing._request_stop(15, None)
    landing.run()

    assert fake.commits == 1
    assert fake.closed is True
    assert landing.rows_landed == 1
