"""Fault injection (doc 04): the four faults at their doc rates (sum inside 2-3%), one fault replaces one reading, the injected sector
never matches a real one, every fault is logged (with the reject reason silver should give it) before the caller sees it. Offline."""
import json
import random
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _probe_support import SECTORS  # noqa: E402
from forcesim.envelope import check_envelope  # noqa: E402
from forcesim.probe import SimProbe  # noqa: E402
from probe.clock import parse_iso  # noqa: E402
from probe.faults import FUTURE_OFFSET, RATES, FaultInjector  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from unittest import mock  # noqa: E402

import probe.faults as faults_module  # noqa: E402

SECTOR_IDS = [s.sector_id for s in SECTORS]
NOW = "2026-09-27T12:00:00.000Z"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="faults-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = self.tmp / "sub" / "fault_injection.jsonl"
        self.fsyncs = []
        patcher = mock.patch.object(faults_module.os, "fsync", side_effect=self.fsyncs.append)   # real fsyncs make thousands of faults slow
        patcher.start()
        self.addCleanup(patcher.stop)

    def injector(self, seed=1, rate=1.0):
        inj = FaultInjector(random.Random(seed), SECTOR_IDS, self.log, rate=rate)
        self.addCleanup(inj.close)
        return inj

    def scans(self, n):
        probe = SimProbe(SECTORS, seed=3)
        base = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
        for i in range(n):
            yield from probe.sweep(i, datetime.fromtimestamp(base + 900 * i, timezone.utc))

    def logged(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []


class RateTests(Fixture):
    def test_the_doc_rates_and_their_sum_inside_two_to_three_percent(self):
        self.assertEqual({name: rate for name, rate, _ in RATES},
                         {"null_channel": 0.012, "out_of_range": 0.010, "unknown_sector": 0.003, "future_event_time": 0.002})
        self.assertTrue(0.02 <= sum(r for _, r, _ in RATES) <= 0.03)

    def test_over_many_readings_each_fault_is_near_its_rate(self):
        inj, total, seen = self.injector(seed=11), 0, Counter()
        for envelope in self.scans(500):                     # 30,000 readings
            total += 1
            _, record = inj.apply(envelope, NOW)
            if record:
                seen[record["fault"]] += 1
        self.assertEqual(total, 30000)
        for name, rate, _ in RATES:
            expected = rate * total
            self.assertAlmostEqual(seen[name], expected, delta=5 * (expected ** 0.5), msg=name)   # about 5 standard deviations
        self.assertAlmostEqual(sum(seen.values()) / total, 0.027, delta=0.003)

    def test_the_rate_scales_all_four_together_and_zero_turns_it_off(self):
        for envelope in self.scans(50):
            self.assertIsNone(self.injector(rate=0.0).apply(envelope, NOW)[1])
        self.assertFalse(self.log.exists())
        double = self.injector(seed=2, rate=2.0)
        n = sum(1 for e in self.scans(200) if double.apply(e, NOW)[1])
        self.assertAlmostEqual(n / 12000, 0.054, delta=0.008)

    def test_an_absurd_rate_is_refused(self):
        for rate in (-0.1, 50.0):
            with self.assertRaises(ValueError):
                self.injector(rate=rate)

    def test_it_is_deterministic_for_a_seed(self):
        first = self.injector(seed=5)
        a = [first.apply(e, NOW)[1] for e in self.scans(20)]
        first.close()
        self.log.unlink(missing_ok=True)
        second = self.injector(seed=5)
        b = [second.apply(e, NOW)[1] for e in self.scans(20)]
        self.assertEqual(a, b)


class FaultShapeTests(Fixture):
    def faults(self, kind, limit=40):
        """Up to `limit` (envelope-before, envelope-after, record) for one fault kind."""
        inj, out = self.injector(seed=21, rate=8.0), []
        for envelope in self.scans(300):
            before = json.loads(json.dumps(envelope))
            after, record = inj.apply(envelope, NOW)
            if record and record["fault"] == kind:
                out.append((before, after, record))
                if len(out) >= limit:
                    break
        self.assertTrue(out, kind)
        return out

    def test_a_null_fault_nulls_exactly_one_science_channel(self):
        for before, after, record in self.faults("null_channel"):
            changed = [k for k in after["payload"] if after["payload"][k] != before["payload"][k]]
            self.assertEqual(changed, [record["channel"]])
            self.assertIsNone(after["payload"][record["channel"]])
            self.assertIn(record["channel"], ("midichlorian_ppm", "kyber_resonance", "dark_side_activity"))
            self.assertEqual(record["expected_reject_reason"], "null_required_field")
            self.assertEqual(record["original"], before["payload"][record["channel"]])

    def test_an_out_of_range_fault_leaves_the_valid_range_on_exactly_one_channel(self):
        for before, after, record in self.faults("out_of_range"):
            self.assertEqual([k for k in after["payload"] if after["payload"][k] != before["payload"][k]], [record["channel"]])
            limit = {"midichlorian_ppm": 30000, "kyber_resonance": 100, "dark_side_activity": 100}[record["channel"]]
            self.assertGreater(after["payload"][record["channel"]], limit)
            self.assertEqual(record["expected_reject_reason"], "out_of_range")
            self.assertTrue(any("outside" in p for p in check_envelope(after)))

    def test_kyber_goes_to_140_as_in_the_doc(self):
        kyber = [a for b, a, r in self.faults("out_of_range", 300) if r["channel"] == "kyber_resonance"]
        self.assertTrue(kyber)
        self.assertTrue(all(a["payload"]["kyber_resonance"] == 140.0 for a in kyber))

    def test_an_unknown_sector_is_never_a_real_one_including_uncharted_and_never_repeats(self):
        found = self.faults("unknown_sector", 200)
        ids = [after["sector_id"] for _, after, _ in found]
        self.assertEqual(len(ids), len(set(ids)))
        for before, after, record in found:
            self.assertNotIn(after["sector_id"], SECTOR_IDS)
            self.assertNotEqual(after["sector_id"], "uncharted")
            self.assertEqual(record["original"], before["sector_id"])
            self.assertEqual(record["expected_reject_reason"], "unknown_sector")

    def test_a_future_event_time_is_24_hours_ahead(self):
        self.assertEqual(FUTURE_OFFSET.total_seconds(), 86400)
        for before, after, record in self.faults("future_event_time"):
            self.assertEqual(parse_iso(after["event_time"]) - parse_iso(before["event_time"]), 86400)
            self.assertEqual(record["original"], before["event_time"])
            self.assertEqual(record["expected_reject_reason"], "impossible_timestamp")

    def test_a_fault_replaces_the_reading_the_event_id_and_scan_are_unchanged(self):
        for kind in ("null_channel", "unknown_sector", "future_event_time"):
            for before, after, record in self.faults(kind, 10):
                self.assertEqual((after["event_id"], after["scan_id"], after["mode"]), (before["event_id"], before["scan_id"], before["mode"]))
                self.assertEqual(record["event_id"], before["event_id"])


class UniqueSectorTests(Fixture):
    def test_a_generated_sector_id_that_matches_a_real_one_is_drawn_again(self):
        colliding = "unknown-00000001"
        rng = random.Random(3)
        draws = iter([1, 2])
        rng.getrandbits = lambda bits: next(draws)
        inj = FaultInjector(rng, SECTOR_IDS + [colliding], self.log, rate=1.0)
        self.addCleanup(inj.close)
        inj._choose = lambda: ("unknown_sector", "unknown_sector")
        envelope = next(iter(self.scans(1)))
        after, record = inj.apply(envelope, NOW)
        self.assertEqual(after["sector_id"], "unknown-00000002")
        self.assertEqual(record["injected"], "unknown-00000002")


class LogTests(Fixture):
    def test_every_fault_is_logged_with_the_documented_fields_and_nothing_else_is(self):
        inj, faulty = self.injector(seed=4, rate=4.0), []
        for envelope in self.scans(100):
            after, record = inj.apply(envelope, NOW)
            if record:
                faulty.append(after)
        entries = self.logged()
        self.assertEqual(len(entries), len(faulty))
        self.assertGreater(len(entries), 100)
        for entry, envelope in zip(entries, faulty):
            self.assertEqual(list(entry), ["event_id", "scan_id", "fault", "expected_reject_reason", "channel", "original",
                                           "injected", "event_time", "sector_id", "logged_utc"])
            self.assertEqual((entry["event_id"], entry["scan_id"]), (envelope["event_id"], envelope["scan_id"]))
            self.assertEqual((entry["event_time"], entry["sector_id"]), (envelope["event_time"], envelope["sector_id"]))
            self.assertEqual(entry["logged_utc"], NOW)

    def test_the_log_is_written_before_apply_returns_so_before_the_event_can_be_buffered(self):
        inj = self.injector(seed=6, rate=8.0)
        for envelope in self.scans(100):
            _, record = inj.apply(envelope, NOW)
            if record:
                self.assertIn(record["event_id"], [e["event_id"] for e in self.logged()])
                return
        self.fail("no fault injected")

    def test_the_log_is_appended_across_runs_and_one_object_per_line(self):
        for _ in range(2):
            inj = self.injector(seed=8, rate=8.0)
            for envelope in list(self.scans(20)):
                inj.apply(envelope, NOW)
            inj.close()
        text = self.log.read_text(encoding="utf-8")
        self.assertNotIn(chr(13), text)
        self.assertTrue(text.endswith(chr(10)))
        self.assertEqual(len(self.logged()), len(text.splitlines()))

    def test_each_fault_is_flushed_to_disk_before_apply_returns(self):
        inj = self.injector(seed=6, rate=8.0)
        faults = 0
        for envelope in self.scans(50):
            _, record = inj.apply(envelope, NOW)
            faults += bool(record)
        self.assertGreater(faults, 10)
        self.assertEqual(len(self.fsyncs), faults)

    def test_the_counts_match_the_log(self):
        inj = self.injector(seed=9, rate=4.0)
        for envelope in self.scans(60):
            inj.apply(envelope, NOW)
        self.assertEqual(sum(inj.counts.values()), len(self.logged()))


if __name__ == "__main__":
    unittest.main()
