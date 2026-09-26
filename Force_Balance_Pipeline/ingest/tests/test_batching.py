"""Bridge buffering and flush rules (doc 02 "Flush policy", doc 04 "Collector bridge"). Offline."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402


def make(**kw):
    clock = kw.pop("clock", None) or S.Clock()
    d = tempfile.mkdtemp()
    b = bridge.Bridge(S.ScriptedUploader(), dead_letter_path=Path(d) / "dead.ndjson", clock=clock.now,
                      sleep=lambda s: None, log=lambda *a: None, **kw)
    return b, clock, Path(d) / "dead.ndjson"


def feed(b, messages):
    out = []
    for m in messages:
        out += b.handle_message(m)
    return out


class CompleteScanTests(unittest.TestCase):
    def test_a_complete_scan_flushes_exactly_one_file_and_only_at_the_sixtieth_event(self):
        b, clock, _ = make()
        msgs = S.scan_messages()
        self.assertEqual(feed(b, msgs[:59]), [])
        self.assertEqual(b.depth(), 59)
        batches = b.handle_message(msgs[59])
        self.assertEqual(len(batches), 1)
        self.assertEqual(b.depth(), 0)
        batch = batches[0]
        self.assertEqual(batch.lines, [m + b"\n" for m in msgs])  # the published bytes, in arrival order
        self.assertEqual(len(batch.lines), 60)

    def test_path_uses_ingest_wall_clock_time_and_source_id_and_a_ulid(self):
        clock = S.Clock(1_788_000_000.0)  # 2026-08-29T10:40:00Z; the events are from 2026-06-01
        b, _, _ = make(clock=clock)
        batch = feed(b, S.scan_messages())[0]
        m = re.fullmatch(r"dt=(\d{4}-\d{2}-\d{2})/hh=(\d{2})/probe-01-([0-9A-HJKMNP-TV-Z]{26})\.ndjson", batch.relpath)
        self.assertIsNotNone(m, batch.relpath)
        self.assertEqual((m.group(1), m.group(2)), ("2026-08-29", "10"))  # flush time, not the event's date
        ts_ms = 0
        for c in m.group(3):
            ts_ms = ts_ms * 32 + bridge._CROCKFORD.index(c)
        self.assertEqual(ts_ms >> 80, 1_788_000_000_000)

    def test_two_scans_arriving_in_turn_make_two_files(self):
        b, _, _ = make()
        first = feed(b, S.scan_messages(0))
        second = feed(b, S.scan_messages(1))
        self.assertEqual((len(first), len(second)), (1, 1))
        self.assertNotEqual(first[0].relpath, second[0].relpath)

    def test_interleaved_scans_the_complete_one_flushes_alone(self):
        b, clock, _ = make()
        a, c = S.scan_messages(0), S.scan_messages(1)
        self.assertEqual(feed(b, a[:30]), [])
        batches = feed(b, c)  # scan B completes while A is half in
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].lines, [m + b"\n" for m in c])
        self.assertEqual(b.depth(), 30)  # A's 30 events wait (they are not lost or mixed in)
        clock.advance(90)
        rest = b.tick()
        self.assertEqual(len(rest), 1)
        self.assertEqual(rest[0].lines, [m + b"\n" for m in a[:30]])

    def test_a_repeated_sector_does_not_complete_a_scan_early_and_is_never_deduplicated(self):
        b, _, _ = make()
        msgs = S.scan_messages()
        self.assertEqual(feed(b, msgs[:59] + [msgs[10]]), [])  # 60 messages, 59 distinct planets
        batches = b.handle_message(msgs[59])
        self.assertEqual(len(batches[0].lines), 61)
        self.assertEqual(batches[0].lines.count(msgs[10] + b"\n"), 2)


class TimerTests(unittest.TestCase):
    def test_ninety_seconds_from_the_first_buffered_event_not_the_last(self):
        b, clock, _ = make()
        e1, m1 = S.report_envelope(1)
        e2, m2 = S.report_envelope(2)
        b.handle_message(m1)
        clock.advance(80)
        b.handle_message(m2)
        clock.advance(9.9)  # 89.9 s after the first
        self.assertEqual(b.tick(), [])
        clock.advance(0.1)  # 90 s
        batches = b.tick()
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].lines, [m1 + b"\n", m2 + b"\n"])
        self.assertEqual(b.depth(), 0)

    def test_the_timer_restarts_with_the_next_first_event(self):
        b, clock, _ = make()
        _, m = S.report_envelope(1)
        b.handle_message(m)
        clock.advance(90)
        self.assertEqual(len(b.tick()), 1)
        clock.advance(10)
        b.handle_message(m)
        clock.advance(89)
        self.assertEqual(b.tick(), [])
        clock.advance(1)
        self.assertEqual(len(b.tick()), 1)

    def test_an_empty_buffer_never_flushes(self):
        b, clock, _ = make()
        clock.advance(10_000)
        self.assertEqual(b.tick(), [])
        self.assertEqual(b.drain(), [])

    def test_reports_flush_on_the_timer_one_file_per_source_in_arrival_order(self):
        b, clock, _ = make()
        sent = []
        for n, source in enumerate(["web-a3f2", "web-b7c1", "web-a3f2", "web-b7c1", "web-a3f2"]):
            e, m = S.report_envelope(n, source_id=source)
            sent.append((source, m))
            self.assertEqual(b.handle_message(m), [])  # no scan_id: never flushes on completion
        clock.advance(90)
        batches = b.tick()
        self.assertEqual(sorted(x.source_id for x in batches), ["web-a3f2", "web-b7c1"])
        for batch in batches:
            self.assertEqual(batch.lines, [m + b"\n" for s, m in sent if s == batch.source_id])
            self.assertTrue(batch.relpath.split("/")[-1].startswith(batch.source_id + "-"))
        self.assertEqual(len({x.relpath for x in batches}), 2)


class SizeTests(unittest.TestCase):
    def test_flush_when_the_buffer_reaches_max_bytes(self):
        b, _, _ = make(max_bytes=1000)
        sent = []
        batches = []
        for n in range(5):
            e, m = S.report_envelope(n, payload={"description": "x" * 150})
            sent.append(m)
            batches += b.handle_message(m)
            if batches:
                break
        self.assertEqual(len(batches), 1)
        total = sum(len(m) + 1 for m in sent)
        self.assertGreaterEqual(total, 1000)
        self.assertLess(total - (len(sent[-1]) + 1), 1000)  # it fired on the message that crossed the limit
        self.assertEqual(batches[0].lines, [m + b"\n" for m in sent])

    def test_default_limits_are_the_doc_02_policy(self):
        self.assertEqual((bridge.DEFAULT_MAX_BYTES, bridge.DEFAULT_MAX_SECONDS, bridge.DEFAULT_SCAN_SIZE),
                         (4 * 1024 * 1024, 90, 60))


class NeverAlterTests(unittest.TestCase):
    def test_lines_are_the_published_bytes_event_time_included(self):
        b, _, _ = make()
        old = "2026-01-01T00:00:00.123Z"  # a replayed reading, hours or days behind: correct and load-bearing
        e, m = S.report_envelope(1, event_time=old)
        b.handle_message(m)
        line = b.drain()[0].lines[0]
        self.assertEqual(line, m + b"\n")
        self.assertEqual(json.loads(line)["event_time"], old)

    def test_arrival_order_is_kept_and_duplicates_are_kept(self):
        b, _, _ = make()
        msgs = [S.report_envelope(n)[1] for n in (5, 3, 9, 3, 1)]
        for m in msgs:
            b.handle_message(m)
        lines = b.drain()[0].lines
        self.assertEqual(lines, [m + b"\n" for m in msgs])  # not sorted, and the repeated event stays twice

    def test_a_multi_line_payload_becomes_one_line_with_the_same_values(self):
        b, _, _ = make()
        e, _ = S.report_envelope(1)
        pretty = json.dumps(e, indent=2).encode()
        b.handle_message(pretty)
        line = b.drain()[0].lines[0]
        self.assertEqual(line.count(b"\n"), 1)
        self.assertEqual(json.loads(line), e)


class StructureTests(unittest.TestCase):
    def dead(self, path):
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_structural_failures_go_to_the_dead_letter_file_and_nothing_is_buffered(self):
        b, _, dead = make()
        good, _ = S.report_envelope(1)
        cases = {
            "not JSON": b"{oops",
            "envelope is not a JSON object": b"[1,2,3]",
            "missing keys": json.dumps({k: v for k, v in good.items() if k != "is_synthetic"}).encode(),
            "source_id is not a safe identifier": json.dumps({**good, "source_id": "../evil"}).encode(),
            "source_id is not a safe identifier ": json.dumps({**good, "source_id": "a/b"}).encode(),
            "source_id is not a safe identifier  ": json.dumps({**good, "source_id": ""}).encode(),
            "source_type must be probe or report": json.dumps({**good, "source_type": "sensor"}).encode(),
            "schema_version is not an integer": json.dumps({**good, "schema_version": "1"}).encode(),
            "event_time is not an ISO 8601": json.dumps({**good, "event_time": "yesterday"}).encode(),
            "is_synthetic is not a boolean": json.dumps({**good, "is_synthetic": "false"}).encode(),
            "payload is not a JSON object": json.dumps({**good, "payload": "text"}).encode(),
            "scan_id is neither": json.dumps({**good, "scan_id": 7}).encode(),
        }
        for needle, message in cases.items():
            self.assertEqual(b.handle_message(message), [])
        records = self.dead(dead)
        self.assertEqual(len(records), len(cases))
        for (needle, message), record in zip(cases.items(), records):
            self.assertIn(needle.strip(), record["reason"])
            self.assertEqual(record["raw"], message.decode())
        self.assertEqual(b.depth(), 0)
        self.assertEqual(b.stats["dead_lettered"], len(cases))
        self.assertEqual(b.stats["received"], len(cases))

    def test_payload_contents_are_never_validated(self):
        """Faults and STEALTH readings must reach bronze; silver decides what they mean (doc 04)."""
        b, _, dead = make()
        probe_like = S.scan_messages()[0]
        obj = json.loads(probe_like)
        variants = [
            {**obj, "payload": {**obj["payload"], "kyber_resonance": 140}},             # out of range
            {**obj, "payload": {**obj["payload"], "midichlorian_ppm": None}},           # null channel
            {**obj, "payload": {}},                                                    # empty payload
            {**obj, "sector_id": "not_a_planet"},                                      # unknown sector
            {**obj, "mode": "SLEEPING"},                                               # unknown mode
            {**obj, "event_time": "2099-01-01T00:00:00.000Z"},                         # in the future
        ]
        for v in variants:
            b.handle_message(json.dumps(v).encode())
        self.assertEqual(b.stats["dead_lettered"], 0)
        self.assertFalse(dead.exists())
        self.assertEqual(b.depth(), len(variants))


class ShutdownTests(unittest.TestCase):
    def test_drain_returns_everything_and_clears_the_scan_tracking(self):
        b, _, _ = make()
        feed(b, S.scan_messages(0)[:20])
        feed(b, [S.report_envelope(1)[1]])
        batches = b.drain()
        self.assertEqual(sorted(len(x.lines) for x in batches), [1, 20])
        self.assertEqual(b.depth(), 0)
        self.assertEqual(b._scans, {})

    def test_counters(self):
        b, _, _ = make()
        feed(b, S.scan_messages())
        self.assertEqual(b.stats["received"], 60)
        self.assertIn("buffer_depth=0", b.summary())


if __name__ == "__main__":
    unittest.main()
