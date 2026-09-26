"""The doc 02 envelope and the seedable ULID.

`python-ulid` draws from OS randomness, so it cannot give a reproducible backfill. This module
implements the ULID format itself (48-bit millisecond timestamp + 80 bits of randomness, 26
Crockford base32 characters) and takes the randomness from a caller-supplied source: a seeded
random.Random for the backfill, random.SystemRandom for a live probe.
"""
import json
import re
from datetime import datetime, timedelta, timezone

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

SCHEMA_VERSION = 1
MODES = ("CONNECTED", "DISCONNECTED", "BURST", "STEALTH")
ENVELOPE_KEYS = ("event_id", "source_id", "source_type", "schema_version", "event_time", "mode",
                 "scan_id", "sector_id", "is_synthetic", "synthetic_ingest_ts", "payload")


def ts_ms(dt):
    """Integer milliseconds since the epoch (exact; no float rounding)."""
    return (dt - _EPOCH) // timedelta(milliseconds=1)


def encode_ulid(timestamp_ms, randomness):
    if not 0 <= timestamp_ms < 1 << 48:
        raise ValueError("ULID timestamp must fit in 48 bits")
    if not 0 <= randomness < 1 << 80:
        raise ValueError("ULID randomness must fit in 80 bits")
    value = (timestamp_ms << 80) | randomness
    chars = []
    for _ in range(26):
        chars.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(timestamp_ms, rng):
    """A ULID whose 80 random bits come from two 40-bit draws of rng.random() (exact for a double)."""
    randomness = (int(rng.random() * (1 << 40)) << 40) | int(rng.random() * (1 << 40))
    return encode_ulid(timestamp_ms, randomness)


def decode_ulid_time(ulid):
    """The millisecond timestamp encoded in a ULID."""
    if not _ULID_RE.match(ulid):
        raise ValueError(f"not a ULID: {ulid!r}")
    value = 0
    for c in ulid:
        value = value * 32 + CROCKFORD.index(c)
    return value >> 80


def format_ts(dt):
    """ISO 8601 UTC with milliseconds and a Z suffix, as in the doc 02 example."""
    if dt.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    u = dt.astimezone(timezone.utc)
    return f"{u:%Y-%m-%dT%H:%M:%S}.{u.microsecond // 1000:03d}Z"


def make_envelope(*, event_id, source_id, source_type, event_time, mode, scan_id, sector_id, payload,
                  is_synthetic=False, synthetic_ingest_ts=None):
    """A doc 02 envelope. `is_synthetic` and `synthetic_ingest_ts` are ALWAYS present (live: false,
    null): Auto Loader's rescue mode would send a field missing from the inferred schema to
    _rescued_data instead of a column."""
    if is_synthetic != (synthetic_ingest_ts is not None):
        raise ValueError("synthetic_ingest_ts is set if and only if is_synthetic")
    return {
        "event_id": event_id,
        "source_id": source_id,
        "source_type": source_type,
        "schema_version": SCHEMA_VERSION,
        "event_time": format_ts(event_time),
        "mode": mode,
        "scan_id": scan_id,
        "sector_id": sector_id,
        "is_synthetic": bool(is_synthetic),
        "synthetic_ingest_ts": format_ts(synthetic_ingest_ts) if synthetic_ingest_ts is not None else None,
        "payload": payload,
    }


def probe_payload(midi, kyber, dark, sensor_temp_c, battery_pct):
    """The doc 02 probe payload, values rounded as in the doc's example (one decimal). The four readings are
    always floats, so each is written with at least one decimal place (20.0, never 20), even if a caller
    passes an int; battery_pct is the one integer."""
    return {"midichlorian_ppm": round(float(midi), 1), "kyber_resonance": round(float(kyber), 1),
            "dark_side_activity": round(float(dark), 1), "sensor_temp_c": round(float(sensor_temp_c), 1),
            "battery_pct": int(battery_pct)}


def to_ndjson_line(envelope):
    """One JSON object per line, no wrapping array (doc 02). Compact and deterministic."""
    return json.dumps(envelope, separators=(",", ":")) + "\n"


def check_envelope(obj):
    """Problems with an envelope against doc 02; an empty list means it is well-formed. Checks the
    outer structure, and for a probe the payload's fields and valid ranges."""
    problems = []
    if list(obj.keys()) != list(ENVELOPE_KEYS):
        problems.append(f"keys are {list(obj.keys())}, expected {list(ENVELOPE_KEYS)}")
        return problems
    if not isinstance(obj["event_id"], str) or not _ULID_RE.match(obj["event_id"]):
        problems.append("event_id is not a ULID")
    if obj["source_type"] not in ("probe", "report"):
        problems.append("source_type must be probe or report")
    if obj["schema_version"] != SCHEMA_VERSION or isinstance(obj["schema_version"], bool):
        problems.append("schema_version must be 1")
    if not isinstance(obj["event_time"], str) or not _TS_RE.match(obj["event_time"]):
        problems.append("event_time is not ISO 8601 UTC with milliseconds")
    if not isinstance(obj["is_synthetic"], bool):
        problems.append("is_synthetic must be a boolean")
    synthetic_ts = obj["synthetic_ingest_ts"]
    if synthetic_ts is not None and not (isinstance(synthetic_ts, str) and _TS_RE.match(synthetic_ts)):
        problems.append("synthetic_ingest_ts is not ISO 8601 UTC with milliseconds")
    if isinstance(obj["is_synthetic"], bool) and obj["is_synthetic"] != (synthetic_ts is not None):
        problems.append("synthetic_ingest_ts must be set if and only if is_synthetic")
    if not isinstance(obj["sector_id"], str) or not obj["sector_id"]:
        problems.append("sector_id is missing")
    if not isinstance(obj["payload"], dict):
        problems.append("payload must be an object")
    if obj["source_type"] == "probe":
        if obj["mode"] not in MODES:
            problems.append(f"mode {obj['mode']!r} is not one of {MODES}")
        if not isinstance(obj["scan_id"], str) or not _ULID_RE.match(obj["scan_id"]):
            problems.append("scan_id is not a ULID")
        p = obj["payload"] if isinstance(obj["payload"], dict) else {}
        for name, lo, hi in (("midichlorian_ppm", 0, 30000), ("kyber_resonance", 0, 100),
                             ("dark_side_activity", 0, 100), ("sensor_temp_c", -50, 120)):
            v = p.get(name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                problems.append(f"payload.{name} = {v!r} is missing or outside {lo}-{hi}")
        b = p.get("battery_pct")
        if isinstance(b, bool) or not isinstance(b, int) or not 0 <= b <= 100:
            problems.append(f"payload.battery_pct = {b!r} is not an integer in 0-100")
    return problems
