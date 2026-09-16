"""services.consumer.writer — Hive partition paths, row coercion, drift
columns, and the atomic write. Runs against tmp_path; no broker involved.
"""

import base64
import json
from datetime import UTC, datetime

import pyarrow.parquet as pq
import pytest

from services.consumer.writer import (
    build_table,
    partition_key,
    quarantine_path,
    write_parquet,
    write_quarantine,
)


def _request_row(**overrides):
    row = {
        "event_id": "e1",
        "event_kind": "request",
        "run_id": "r1",
        "session_id": "s1",
        "received_at": "2026-09-15T13:28:51.382366Z",
        "client_ts": None,
        "schema_version": "v1",
        "source_ip": "192.0.2.1",
        "user_agent": "curl/8",
        "http_method": "GET",
        "path": "/batcomputer",
        "query_string": None,
        "path_tier": 3,
        "request_body": None,
        "body_bytes": 0,
        "status_returned": 200,
        "response_time_ms": 12.5,
        "headers": '{"host": "h"}',
        "attempt_id": "a1",
        "kafka_partition": 0,
        "kafka_offset": 7,
        "landed_at": "2026-09-15T13:28:52.000000Z",
    }
    row.update(overrides)
    return row


def test_partition_key_comes_from_received_at_in_utc():
    ts = datetime(2026, 9, 15, 13, 5, tzinfo=UTC)
    assert partition_key("request", ts) == ("request", "2026-09-15", "13")


def test_partition_key_normalizes_to_utc_before_slicing():
    """A non-UTC timestamp must not land in the local-time hour."""
    from datetime import timedelta, timezone

    ts = datetime(2026, 9, 15, 1, 30, tzinfo=timezone(timedelta(hours=5)))
    assert partition_key("request", ts) == ("request", "2026-09-14", "20")


def test_build_table_types_columns_from_the_declared_schema():
    table = build_table("request", [_request_row()])
    assert table.schema.field("received_at").type.tz == "UTC"
    assert table.column("received_at")[0].as_py() == datetime(
        2026, 9, 15, 13, 28, 51, 382366, tzinfo=UTC
    )
    assert table.column("path")[0].as_py() == "/batcomputer"


def test_all_null_nullable_column_keeps_its_declared_type():
    """The reason the schema is declared rather than inferred: a batch where
    client_ts is entirely null must still be a timestamp column, or it can't
    merge with a sibling file that has values."""
    table = build_table("request", [_request_row(), _request_row(event_id="e2")])
    assert table.column("client_ts").null_count == 2
    assert table.schema.field("client_ts").type.tz == "UTC"


def test_schema_drift_lands_as_an_extra_string_column():
    """docs/03 row 8: mid-run v2 + tls_fingerprint. The consumer must carry it,
    not normalize it away."""
    drifted = _request_row(event_id="e2", schema_version="v2", tls_fingerprint="ja3:abc")
    table = build_table("request", [_request_row(), drifted])
    assert "tls_fingerprint" in table.schema.names
    assert table.column("tls_fingerprint").to_pylist() == [None, "ja3:abc"]


def test_dict_valued_fields_land_as_json_strings():
    """attempt.parameters is a real dict; a struct column would have to be
    re-inferred per batch, which is what the declared schema avoids."""
    row = {
        "event_id": "e1",
        "event_kind": "attempt",
        "session_id": "s1",
        "received_at": "2026-09-15T13:00:00Z",
        "parameters": {"target": "/vault"},
        "kafka_partition": 0,
        "kafka_offset": 1,
        "landed_at": "2026-09-15T13:00:01Z",
    }
    table = build_table("attempt", [row])
    assert json.loads(table.column("parameters")[0].as_py()) == {"target": "/vault"}


def test_list_valued_field_survives_the_round_trip(tmp_path):
    """attack_run.pathologies_enabled is list<string> - Parquet handles it
    natively, so it should not be flattened to a JSON string."""
    row = {
        "event_id": "e1",
        "event_kind": "attack_run",
        "session_id": "s1",
        "received_at": "2026-09-15T13:00:00Z",
        "villain_slug": "60-bane",
        "pathologies_enabled": ["burst", "unkeyed"],
        "kafka_partition": 0,
        "kafka_offset": 1,
        "landed_at": "2026-09-15T13:00:01Z",
    }
    key = ("attack_run", "2026-09-15", "13")
    path = write_parquet(tmp_path, key, [row])
    assert pq.read_table(path).column("pathologies_enabled")[0].as_py() == ["burst", "unkeyed"]


def test_write_parquet_lands_at_the_hive_path_and_leaves_no_temp_file(tmp_path):
    key = ("request", "2026-09-15", "13")
    path = write_parquet(tmp_path, key, [_request_row()])

    assert path.parent == tmp_path / "raw" / "request" / "dt=2026-09-15" / "hour=13"
    assert path.name.startswith("part-") and path.suffix == ".parquet"
    assert list(path.parent.glob("*.tmp")) == []
    assert pq.read_table(path).num_rows == 1


def test_each_flush_writes_its_own_file(tmp_path):
    """Two flushes into the same hour must not clobber each other."""
    key = ("request", "2026-09-15", "13")
    first = write_parquet(tmp_path, key, [_request_row()])
    second = write_parquet(tmp_path, key, [_request_row(event_id="e2")])
    assert first != second
    assert len(list(first.parent.glob("part-*.parquet"))) == 2


def test_unknown_event_kind_still_lands(tmp_path):
    """A kind with no declared schema must not halt the writer."""
    row = {
        "event_id": "e1",
        "event_kind": "chat_turn",
        "session_id": "s1",
        "received_at": "2026-09-15T13:00:00Z",
        "user_text": "who are you",
        "kafka_partition": 0,
        "kafka_offset": 1,
        "landed_at": "2026-09-15T13:00:01Z",
    }
    path = write_parquet(tmp_path, ("chat_turn", "2026-09-15", "13"), [row])
    assert pq.read_table(path).column("user_text")[0].as_py() == "who are you"


def test_quarantine_records_the_offset_in_the_name_and_the_content(tmp_path):
    raw = b"\x00\x01not-json\xff"
    path = write_quarantine(tmp_path, 2, 41, b"session-1", raw, "Expecting value")

    assert path.name == "partition=2-offset=41.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["kafka_partition"] == 2
    assert document["kafka_offset"] == 41
    assert base64.b64decode(document["raw_b64"]) == raw
    assert list(path.parent.glob("*.tmp")) == []


def test_quarantine_is_idempotent_on_replay(tmp_path):
    """A replayed undeserializable message overwrites its own file rather than
    duplicating, so the duplicate story after a restart lives in Parquet only."""
    for _ in range(3):
        write_quarantine(tmp_path, 1, 9, None, b"\xff\xfe", "boom")
    directory = quarantine_path(tmp_path, 1, 9).parent
    assert len(list(directory.glob("*.json"))) == 1


@pytest.mark.parametrize("bad", ["not-a-timestamp", "", None])
def test_unparseable_timestamp_becomes_null_rather_than_raising(bad):
    """Guard: a bad received_at must not crash the batch. The row lands with a
    null so Phase 5 staging can flag it (docs/02)."""
    table = build_table("request", [_request_row(client_ts=bad)])
    assert table.column("client_ts")[0].as_py() is None
