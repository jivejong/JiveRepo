"""Fault injection (doc 04, Fault injection): during CONNECTED, 2-3% of readings are deliberately faulty, and every fault is logged
locally BEFORE its event is buffered, so the log can be reconciled against silver.rejects.

Rates (doc 04): ~1.2% one channel null, ~1.0% one channel out of range, ~0.3% sector_id not in dim_sector, ~0.2% event_time in the
future; their sum, 2.7%, is inside the 2-3% band. `rate` scales all four together (1.0 default, 0 off). Each reading is drawn
independently; a faulty reading REPLACES the clean one, so a scan stays 60 events. A null or out-of-range fault hits any one of the
three science channels; an unknown sector_id is unique per fault; a future event_time is 24 hours ahead.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from forcesim.envelope import format_ts
from .clock import parse_iso

RATES = (("null_channel", 0.012, "null_required_field"), ("out_of_range", 0.010, "out_of_range"),
         ("unknown_sector", 0.003, "unknown_sector"), ("future_event_time", 0.002, "impossible_timestamp"))
FUTURE_OFFSET = timedelta(hours=24)
SCIENCE_CHANNELS = ("midichlorian_ppm", "kyber_resonance", "dark_side_activity")
UPPER = {"midichlorian_ppm": 30000.0, "kyber_resonance": 100.0, "dark_side_activity": 100.0}
OUT_OF_RANGE_FACTOR = 1.4          # kyber 100 -> 140, the example in doc 04


class FaultInjector:
    def __init__(self, rng, valid_sector_ids, log_path, rate=1.0):
        if rate < 0 or rate * sum(r[1] for r in RATES) > 1:
            raise ValueError("fault rate out of range")
        self.rng, self.valid = rng, frozenset(valid_sector_ids)
        self.rate, self.log_path = rate, Path(log_path)
        self.counts = {name: 0 for name, _, _ in RATES}
        self._file = None

    def _choose(self):
        draw, edge = self.rng.random(), 0.0
        for name, share, reason in RATES:
            edge += share * self.rate
            if draw < edge:
                return name, reason
        return None

    def apply(self, envelope, now_iso):
        """Return (envelope, fault_record). The envelope is changed in place when a fault is injected; the record has been appended
        to the log (and flushed) before this returns."""
        choice = self._choose()
        if choice is None:
            return envelope, None
        name, reason = choice
        record = {"event_id": envelope["event_id"], "scan_id": envelope["scan_id"], "fault": name,
                  "expected_reject_reason": reason, "channel": None, "original": None, "injected": None}
        payload = envelope["payload"]
        if name in ("null_channel", "out_of_range"):
            channel = self.rng.choice(SCIENCE_CHANNELS)
            record["channel"], record["original"] = channel, payload[channel]
            payload[channel] = None if name == "null_channel" else round(UPPER[channel] * OUT_OF_RANGE_FACTOR, 1)
            record["injected"] = payload[channel]
        elif name == "unknown_sector":
            record["original"] = envelope["sector_id"]
            while True:
                fake = f"unknown-{self.rng.getrandbits(32):08x}"
                if fake not in self.valid:
                    break
            envelope["sector_id"] = record["injected"] = fake
        else:
            record["original"] = envelope["event_time"]
            ahead = datetime.fromtimestamp(parse_iso(envelope["event_time"]), timezone.utc) + FUTURE_OFFSET
            envelope["event_time"] = record["injected"] = format_ts(ahead)
        record["event_time"], record["sector_id"], record["logged_utc"] = envelope["event_time"], envelope["sector_id"], now_iso
        self._log(record)
        self.counts[name] += 1
        return envelope, record

    def _log(self, record):
        """Append one line and force it to disk before returning; the file stays open, so a fault costs one write and one fsync."""
        if self._file is None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.log_path.open("a", encoding="utf-8", newline=chr(10))
        self._file.write(json.dumps(record, separators=(",", ":")) + chr(10))
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = None
