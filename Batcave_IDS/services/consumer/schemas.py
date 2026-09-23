"""The landed Parquet schema, one per `event_kind`.

Declared explicitly rather than inferred from each batch (`pa.Table.from_pylist`)
because inference is not stable across flushes: a batch in which a nullable
column happens to be all-null (`client_ts`, `run_id`, `path` all can be) infers
pyarrow type `null`, which collides with the `timestamp`/`string` the next batch
infers for the same column. The directory then can't be read as one table, which
would surface in Phase 5 as an unreadable source with no obvious cause.

Schema drift is deliberately NOT normalized away here. The pathology (docs/03
row 8) bumps `schema_version` to v2 and adds `tls_fingerprint` mid-run; those
extra fields land as string columns on the batches that carry them, so sibling
Parquet files in one directory legitimately differ by a column. Tolerating that
is staging's job in Phase 5, which must read with `union_by_name=true`.

Field lists are kept in step with the Pydantic models by
tests/test_consumer_schemas.py, which fails if the two drift apart.
"""

from __future__ import annotations

import pyarrow as pa

# Microsecond UTC timestamps: Pydantic serializes with microsecond precision
# (`...T13:28:51.382366Z`), so `us` is lossless and `ms` would silently truncate.
TIMESTAMP = pa.timestamp("us", tz="UTC")

# services/common/envelope.py — shared by every event_kind.
ENVELOPE_FIELDS: list[tuple[str, pa.DataType]] = [
    ("event_id", pa.string()),
    ("event_kind", pa.string()),
    ("run_id", pa.string()),
    ("session_id", pa.string()),
    ("received_at", TIMESTAMP),
    ("client_ts", TIMESTAMP),
    ("schema_version", pa.string()),
]

# docs/02: "Consumer adds: kafka_partition, kafka_offset, landed_at."
CONSUMER_FIELDS: list[tuple[str, pa.DataType]] = [
    ("kafka_partition", pa.int32()),
    ("kafka_offset", pa.int64()),
    ("landed_at", TIMESTAMP),
]

KIND_FIELDS: dict[str, list[tuple[str, pa.DataType]]] = {
    # services/honeypot/models.py :: RequestEvent
    "request": [
        ("source_ip", pa.string()),
        ("user_agent", pa.string()),
        ("http_method", pa.string()),
        ("path", pa.string()),
        ("query_string", pa.string()),
        ("path_tier", pa.int32()),
        ("request_body", pa.string()),
        ("body_bytes", pa.int64()),
        ("status_returned", pa.int32()),
        ("response_time_ms", pa.float64()),
        ("headers", pa.string()),
        ("attempt_id", pa.string()),
    ],
    # services/simulator/session.py :: AttemptEvent
    "attempt": [
        ("attempt_id", pa.string()),
        ("stage", pa.int32()),
        ("technique_id", pa.string()),
        ("attack_id", pa.string()),
        ("attempt_seq", pa.int32()),
        ("technique_attempt_seq", pa.int32()),
        ("decision", pa.string()),
        ("parameters", pa.string()),
        ("computed_probability", pa.float64()),
        ("roll", pa.float64()),
        ("outcome", pa.string()),
        ("noise_generated", pa.int32()),
        ("stage_entered_at", TIMESTAMP),
        ("attempt_at", TIMESTAMP),
    ],
    # services/simulator/machine.py :: AttackRunEvent  (GROUND TRUTH)
    "attack_run": [
        ("villain_slug", pa.string()),
        ("started_at", TIMESTAMP),
        ("ended_at", TIMESTAMP),
        ("duration_s", pa.float64()),
        ("requests_sent", pa.int32()),
        ("attempts_made", pa.int32()),
        ("max_stage_reached", pa.int32()),
        ("run_outcome", pa.string()),
        ("pathologies_enabled", pa.list_(pa.string())),
        ("timing_compression_factor", pa.float64()),
        # Phase 8 (docs/06 Track B): "headless" (make attack/attack-all) or
        # "console" (services/console/). Record-only, same precedent as
        # timing_compression_factor - lets a reader (or a dbt model) tell a
        # human-paced console run from a corpus-grade headless one without
        # guessing from pathologies_enabled/timing_compression_factor, which
        # a `--no-pathologies --time-scale 1.0` headless run could also show.
        ("session_source", pa.string()),
    ],
}

# Event fields that arrive as JSON objects and land as JSON-encoded strings.
# `headers` is already a JSON string on the wire (RequestEvent encodes it);
# `parameters` is a real dict. Both land as strings so the column has one stable
# type — a struct would have to be re-inferred per batch, which is the problem
# this module exists to avoid.
JSON_ENCODED_FIELDS = frozenset({"headers", "parameters"})


def landed_schema(event_kind: str) -> pa.Schema:
    """The full landed schema for one event_kind: envelope + kind-specific +
    consumer-added columns.

    An unknown kind (`chat_turn` and `counterstrike` exist in the contract but
    nothing produces them yet; Track B adds them) gets envelope + consumer
    columns only, and its other fields ride along as drift strings. That keeps a
    new kind from halting the consumer — the same reason undeserializable
    messages are quarantined rather than raised.
    """
    fields = ENVELOPE_FIELDS + KIND_FIELDS.get(event_kind, []) + CONSUMER_FIELDS
    return pa.schema([pa.field(name, dtype) for name, dtype in fields])


def known_field_names(event_kind: str) -> frozenset[str]:
    """Field names the schema types explicitly; anything else in an event is
    drift and lands as a string column."""
    return frozenset(name for name, _ in ENVELOPE_FIELDS + KIND_FIELDS.get(event_kind, []))
