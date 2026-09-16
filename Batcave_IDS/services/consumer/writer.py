"""Writes a buffered batch to disk: partitioned Parquet for events that parsed,
quarantine files for the ones that didn't.

Layout (docs/01, and what `.gitignore`'s sample negation assumes):

    data/raw/<event_kind>/dt=YYYY-MM-DD/hour=HH/part-<uuid>.parquet

Partition values come from `received_at` and never from `client_ts` — the
attacker supplies `client_ts` and the pathologies deliberately shuffle it 30s
and backdate it up to 6h (docs/03). Partitioning on it would scatter a session
across partitions an attacker chose.

Everything here is written before any offset is committed; see consumer.py.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from services.consumer.schemas import JSON_ENCODED_FIELDS, known_field_names, landed_schema

# (event_kind, dt, hour) -> the rows landing in that one Parquet file.
PartitionKey = tuple[str, str, str]


def partition_key(event_kind: str, received_at: datetime) -> PartitionKey:
    """Hive partition values from `received_at`, in UTC."""
    ts = received_at.astimezone(UTC)
    return event_kind, ts.strftime("%Y-%m-%d"), ts.strftime("%H")


def partition_dir(data_root: Path, key: PartitionKey) -> Path:
    event_kind, dt, hour = key
    return data_root / "raw" / event_kind / f"dt={dt}" / f"hour={hour}"


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 string from the wire. Pydantic emits a `Z` suffix,
    which fromisoformat handles from Python 3.11."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _coerce(name: str, value: Any, dtype: pa.DataType) -> Any:
    """Shape one event field to its declared column type."""
    if value is None:
        return None
    if pa.types.is_timestamp(dtype):
        return parse_timestamp(value)
    if name in JSON_ENCODED_FIELDS and not isinstance(value, str):
        return json.dumps(value)
    return value


def _drift_value(value: Any) -> str | None:
    """Unknown fields land as strings — the consumer can't know the type of a
    field it has never seen, and a string is lossless for any JSON scalar."""
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value)


def build_table(event_kind: str, rows: list[dict[str, Any]]) -> pa.Table:
    """One batch of same-kind rows as an Arrow table on the declared schema,
    plus a string column for each drifted field present in the batch."""
    schema = landed_schema(event_kind)
    known = known_field_names(event_kind)

    columns: dict[str, list[Any]] = {}
    for field in schema:
        columns[field.name] = [_coerce(field.name, row.get(field.name), field.type) for row in rows]

    # Schema drift (docs/03 row 8): tls_fingerprint and anything else new.
    drift_names = sorted({k for row in rows for k in row} - known - set(schema.names))
    for name in drift_names:
        columns[name] = [_drift_value(row.get(name)) for row in rows]

    full_schema = pa.schema(list(schema) + [pa.field(n, pa.string()) for n in drift_names])
    return pa.table(columns, schema=full_schema)


def write_parquet(data_root: Path, key: PartitionKey, rows: list[dict[str, Any]]) -> Path:
    """Write one partition file, atomically.

    The table is written to a temp name and then renamed into place, so a kill
    mid-write can leave a stray `.tmp` but never a half-written `part-*.parquet`
    that DuckDB would fail to read. os.replace is atomic within a filesystem.
    """
    event_kind, _, _ = key
    directory = partition_dir(data_root, key)
    directory.mkdir(parents=True, exist_ok=True)

    final = directory / f"part-{uuid.uuid4()}.parquet"
    tmp = final.with_suffix(".parquet.tmp")
    pq.write_table(build_table(event_kind, rows), tmp, compression="snappy")
    os.replace(tmp, final)
    return final


def quarantine_path(data_root: Path, partition: int, offset: int) -> Path:
    return (
        data_root
        / "quarantine"
        / "undeserializable"
        / f"partition={partition}-offset={offset}.json"
    )


def write_quarantine(
    data_root: Path,
    partition: int,
    offset: int,
    key: bytes | None,
    raw: bytes,
    error: str,
) -> Path:
    """Park one undeserializable message (docs/01: "with the offset recorded,
    and the consumer continues rather than halting").

    Flat, not partitioned: there is no trustworthy `received_at` to partition on
    in a message that didn't parse, and both docs/01 and docs/05 show the
    directory without a partition scheme. The offset appears in the filename and
    in the content, and the raw bytes are base64-encoded because by definition
    they are not valid JSON (or necessarily valid UTF-8).

    The deterministic filename makes quarantining idempotent: a replayed message
    overwrites its own file instead of duplicating. So the duplicate story after
    a restart lives entirely in Parquet, which is where Phase 5's dedupe works.
    """
    path = quarantine_path(data_root, partition, offset)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "kafka_partition": partition,
        "kafka_offset": offset,
        "landed_at": datetime.now(UTC).isoformat(),
        "kafka_key": key.decode("utf-8", errors="replace") if key is not None else None,
        "error": error,
        "raw_b64": base64.b64encode(raw).decode("ascii"),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
