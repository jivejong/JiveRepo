"""scripts/faultlog_to_seed.py (doc 04, "Fault injection"): the pulled fault_injection.jsonl becomes a frozen
seed carrying only event_id and expected_reject_reason -- the two columns ingest/phase4_checkpoint.sql's p4-12
join actually uses. Offline, no real log or seed involved."""
import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import faultlog_to_seed as fts  # noqa: E402

# One record per doc 04 fault type (edge/probe/faults.py's own RATES: name -> expected_reject_reason), in the
# real record shape faults.py._log() actually writes -- extra fields a seed doesn't need, included anyway, since
# convert() must pick its two columns out of a real record, not a pre-trimmed one.
FIXTURE_RECORDS = [
    {"event_id": "01EVT0000000000000000001", "scan_id": "01SCAN000000000000000001", "fault": "null_channel",
     "expected_reject_reason": "null_required_field", "channel": "kyber_resonance", "original": 42.0,
     "injected": None, "event_time": "2026-10-05T12:00:00.500Z", "sector_id": "tatooine",
     "logged_utc": "2026-10-05T12:00:00.510Z"},
    {"event_id": "01EVT0000000000000000002", "scan_id": "01SCAN000000000000000001", "fault": "out_of_range",
     "expected_reject_reason": "out_of_range", "channel": "kyber_resonance", "original": 42.0, "injected": 140.0,
     "event_time": "2026-10-05T12:00:00.520Z", "sector_id": "naboo",
     "logged_utc": "2026-10-05T12:00:00.530Z"},
    {"event_id": "01EVT0000000000000000003", "scan_id": "01SCAN000000000000000001", "fault": "unknown_sector",
     "expected_reject_reason": "unknown_sector", "channel": None, "original": "dagobah",
     "injected": "unknown-deadbeef", "event_time": "2026-10-05T12:00:00.540Z", "sector_id": "unknown-deadbeef",
     "logged_utc": "2026-10-05T12:00:00.550Z"},
    {"event_id": "01EVT0000000000000000004", "scan_id": "01SCAN000000000000000001", "fault": "future_event_time",
     "expected_reject_reason": "impossible_timestamp", "channel": None, "original": "2026-10-05T12:00:00.560Z",
     "injected": "2026-10-06T12:00:00.560Z", "event_time": "2026-10-06T12:00:00.560Z", "sector_id": "coruscant",
     "logged_utc": "2026-10-05T12:00:00.570Z"},
]


def fixture_lines():
    return [json.dumps(r, separators=(",", ":")) for r in FIXTURE_RECORDS]


class ConvertTests(unittest.TestCase):
    def test_all_four_fault_types_convert_to_their_doc_03_reject_reason(self):
        rows = list(fts.convert(fixture_lines()))
        self.assertEqual(rows, [
            {"event_id": "01EVT0000000000000000001", "expected_reject_reason": "null_required_field"},
            {"event_id": "01EVT0000000000000000002", "expected_reject_reason": "out_of_range"},
            {"event_id": "01EVT0000000000000000003", "expected_reject_reason": "unknown_sector"},
            {"event_id": "01EVT0000000000000000004", "expected_reject_reason": "impossible_timestamp"},
        ])

    def test_blank_lines_are_skipped(self):
        lines = ["", fixture_lines()[0], "   ", fixture_lines()[1], ""]
        rows = list(fts.convert(lines))
        self.assertEqual(len(rows), 2)

    def test_order_is_preserved_not_resorted(self):
        lines = list(reversed(fixture_lines()))
        rows = list(fts.convert(lines))
        self.assertEqual([r["event_id"] for r in rows],
                          ["01EVT0000000000000000004", "01EVT0000000000000000003",
                           "01EVT0000000000000000002", "01EVT0000000000000000001"])


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log_path = self.tmp / "fault_injection.jsonl"
        self.log_path.write_text("\n".join(fixture_lines()) + "\n", encoding="utf-8")
        self.out_path = self.tmp / "fault_injection_2026-10-05_2026-10-07.csv"

    def test_writes_exactly_the_two_columns_for_every_record(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(fts.main([str(self.log_path), "--out", str(self.out_path)]), 0)
        with self.out_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)
        self.assertEqual(fieldnames, ["event_id", "expected_reject_reason"])
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2], {"event_id": "01EVT0000000000000000003", "expected_reject_reason": "unknown_sector"})

    def test_the_seed_has_no_crlf_on_any_os(self):
        # Windows Python text-mode writes corrupt an already-LF file to CRLF; faultlog_to_seed.py opens with
        # newline="" for exactly this reason (same discipline as scripts/enrich_common.py's write_csv).
        with redirect_stdout(io.StringIO()):
            fts.main([str(self.log_path), "--out", str(self.out_path)])
        self.assertNotIn(b"\r", self.out_path.read_bytes())

    def test_refuses_to_overwrite_an_existing_seed_without_force(self):
        self.out_path.write_text("event_id,expected_reject_reason\n", encoding="utf-8", newline="\n")
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                fts.main([str(self.log_path), "--out", str(self.out_path)])

    def test_force_does_overwrite(self):
        self.out_path.write_text("stale\n", encoding="utf-8", newline="\n")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(fts.main([str(self.log_path), "--out", str(self.out_path), "--force"]), 0)
        self.assertIn("event_id", self.out_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
