"""Shared test support: import paths, a controllable clock, scripted uploaders and envelope builders."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ingest" / "bridge"))
sys.path.insert(0, str(ROOT / "edge"))  # forcesim builds realistic envelopes; test-only, the bridge never imports it

from forcesim.envelope import to_ndjson_line  # noqa: E402
from forcesim.probe import SimProbe  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402

SECTORS = load_sectors()
SCAN_TIME = datetime(2026, 6, 1, 8, 15, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start=1_788_000_000.0):
        self.t = start

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class ScriptedUploader:
    """put() returns the next scripted status (an Exception instance is raised); 204 once the script is spent."""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []

    def put(self, relpath, data):
        self.calls.append((relpath, data))
        step = self.script.pop(0) if self.script else 204
        if isinstance(step, Exception):
            raise step
        return step


def scan_messages(scan_index=0, seed=1, source_id="probe-01", start=SCAN_TIME):
    """The 60 messages of one sweep, as the bytes a probe publishes (compact JSON, no newline)."""
    from datetime import timedelta
    probe = SimProbe(SECTORS, seed=seed, source_id=source_id)
    envelopes = probe.sweep(scan_index, start + timedelta(minutes=15 * scan_index))
    return [to_ndjson_line(e).rstrip("\n").encode() for e in envelopes]


def report_envelope(n=0, source_id="web-a3f2", **over):
    import json
    e = {"event_id": f"01K4X8QP2M7YRZ9NBVCDXWQ{n:03d}"[:26].ljust(26, "0"), "source_id": source_id,
         "source_type": "report", "schema_version": 1, "event_time": "2026-06-01T08:15:00.000Z", "mode": None,
         "scan_id": None, "sector_id": "tatooine", "is_synthetic": False, "synthetic_ingest_ts": None,
         "payload": {"description": "Saw a figure in black robes.", "relevance_score": 0.85}}
    e.update(over)
    return e, json.dumps(e, separators=(",", ":")).encode()
