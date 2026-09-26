"""The doc 02 envelope, the seedable ULID, and SimProbe's sweep (doc 02, doc 04). Offline."""
import json
import random
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forcesim import device  # noqa: E402
from forcesim.envelope import (CROCKFORD, ENVELOPE_KEYS, check_envelope, decode_ulid_time, encode_ulid,  # noqa: E402
                               format_ts, make_envelope, new_ulid, probe_payload, to_ndjson_line, ts_ms)
from forcesim.probe import SimProbe  # noqa: E402
from forcesim.sectors import load_sectors, sha256_file  # noqa: E402
from forcesim.signatures import target_for  # noqa: E402
from forcesim.walk import stream  # noqa: E402

SECTORS = load_sectors()
T0 = datetime(2026, 9, 5, 14, 15, 0, tzinfo=timezone.utc)


class UlidTests(unittest.TestCase):
    def test_format_and_timestamp_round_trip(self):
        u = new_ulid(ts_ms(T0), stream(1, "u"))
        self.assertEqual(len(u), 26)
        self.assertTrue(set(u) <= set(CROCKFORD))
        self.assertLessEqual(u[0], "7")  # 128 bits in 130: the first character carries 3 bits
        self.assertEqual(decode_ulid_time(u), ts_ms(T0))

    def test_known_encoding(self):
        self.assertEqual(encode_ulid(0, 0), "0" * 26)
        self.assertEqual(encode_ulid(0, 31), "0" * 25 + "Z")
        self.assertEqual(encode_ulid((1 << 48) - 1, (1 << 80) - 1), "7" + "Z" * 25)
        with self.assertRaises(ValueError):
            encode_ulid(1 << 48, 0)
        with self.assertRaises(ValueError):
            encode_ulid(0, 1 << 80)

    def test_seeded_randomness_is_reproducible_and_seed_dependent(self):
        a = [new_ulid(ts_ms(T0), stream(5, "u")) for _ in range(2)]
        self.assertEqual(a[0], a[1])
        self.assertNotEqual(new_ulid(ts_ms(T0), stream(5, "u")), new_ulid(ts_ms(T0), stream(6, "u")))
        rng = stream(5, "u")
        self.assertNotEqual(new_ulid(ts_ms(T0), rng), new_ulid(ts_ms(T0), rng))  # advances

    def test_sorts_by_time(self):
        rng = stream(9, "u")
        ids = [new_ulid(ts_ms(T0 + timedelta(seconds=s)), rng) for s in (0, 1, 2, 90, 3600)]
        self.assertEqual(ids, sorted(ids))

    def test_system_randomness_works_for_live_probes(self):
        u = new_ulid(ts_ms(T0), random.SystemRandom())
        self.assertEqual(decode_ulid_time(u), ts_ms(T0))

    def test_decode_refuses_non_ulids(self):
        for bad in ("", "short", "8" + "0" * 25, "I" + "0" * 25):
            with self.assertRaises(ValueError):
                decode_ulid_time(bad)


class TimestampTests(unittest.TestCase):
    def test_millisecond_z_format(self):
        t = datetime(2026, 9, 5, 14, 22, 7, 412999, tzinfo=timezone.utc)
        self.assertEqual(format_ts(t), "2026-09-05T14:22:07.412Z")  # truncated, not rounded
        self.assertEqual(format_ts(t.astimezone(timezone(timedelta(hours=5)))), "2026-09-05T14:22:07.412Z")
        with self.assertRaises(ValueError):
            format_ts(datetime(2026, 9, 5))

    def test_ts_ms_is_exact(self):
        self.assertEqual(ts_ms(datetime(1970, 1, 1, 0, 0, 1, 500000, tzinfo=timezone.utc)), 1500)


class EnvelopeTests(unittest.TestCase):
    def envelope(self, **over):
        kw = dict(event_id=new_ulid(ts_ms(T0), stream(1, "e")), source_id="probe-01", source_type="probe",
                  event_time=T0, mode="CONNECTED", scan_id=new_ulid(ts_ms(T0), stream(1, "s")),
                  sector_id="tatooine", payload=probe_payload(2800.04, 12.0, 15.55, 41.2, 87))
        kw.update(over)
        return make_envelope(**kw)

    def test_live_envelope_always_carries_both_synthetic_keys(self):
        e = self.envelope()
        self.assertEqual(list(e), list(ENVELOPE_KEYS))
        self.assertIs(e["is_synthetic"], False)
        self.assertIsNone(e["synthetic_ingest_ts"])
        self.assertEqual(check_envelope(e), [])
        self.assertIn('"is_synthetic":false,"synthetic_ingest_ts":null', to_ndjson_line(e))

    def test_synthetic_envelope(self):
        e = self.envelope(is_synthetic=True, synthetic_ingest_ts=T0 + timedelta(hours=2))
        self.assertIs(e["is_synthetic"], True)
        self.assertEqual(e["synthetic_ingest_ts"], "2026-09-05T16:15:00.000Z")
        self.assertEqual(check_envelope(e), [])

    def test_synthetic_ts_is_set_if_and_only_if_synthetic(self):
        with self.assertRaises(ValueError):
            self.envelope(is_synthetic=True)
        with self.assertRaises(ValueError):
            self.envelope(synthetic_ingest_ts=T0)
        bad = self.envelope()
        bad["synthetic_ingest_ts"] = "2026-09-05T16:15:00.000Z"
        self.assertIn("synthetic_ingest_ts must be set if and only if is_synthetic", check_envelope(bad))

    def test_payload_rounding_and_types(self):
        p = probe_payload(14200.54, 62.449, 11.85, 41.24, 87.0)
        self.assertEqual(p, {"midichlorian_ppm": 14200.5, "kyber_resonance": 62.4, "dark_side_activity": 11.8,
                             "sensor_temp_c": 41.2, "battery_pct": 87})
        self.assertIsInstance(p["battery_pct"], int)

    def test_whole_number_readings_are_written_with_a_decimal_place(self):
        """20.0 stays 20.0 on the wire. Even an int passed in (a clamp bound, a control-topic value) is
        written as a float, so no reading is ever the bare token 20."""
        for value in (20, 20.0, 19.96, 0, 0.0, 100, 12000, -3, 40):
            with self.subTest(value=value):
                p = probe_payload(value, value, value, value, 87)
                line = to_ndjson_line(self.envelope(payload=p))
                tokens = re.findall(r'"(midichlorian_ppm|kyber_resonance|dark_side_activity|sensor_temp_c)":([-0-9.]+)', line)
                self.assertEqual(len(tokens), 4)
                for name, token in tokens:
                    self.assertIsInstance(p[name], float)
                    self.assertIn(".", token, f"{name} was written as {token}")
                self.assertRegex(line, r'"battery_pct":87[,}]')
        p = probe_payload(20, 20, 20.0, 20, 87)
        self.assertEqual(to_ndjson_line(self.envelope(payload=p)).count(":20.0,"), 4)

    def test_every_simulated_reading_is_written_with_a_decimal_place(self):
        probe = SimProbe(SECTORS, seed=11)
        whole = 0
        for scan in range(40):
            for e in probe.sweep(scan, T0 + timedelta(minutes=15 * scan)):
                line = to_ndjson_line(e)
                for name, token in re.findall(
                        r'"(midichlorian_ppm|kyber_resonance|dark_side_activity|sensor_temp_c)":([-0-9.]+)', line):
                    self.assertIn(".", token, f"{e['sector_id']} {name} was written as {token}")
                    whole += token.endswith(".0")
        self.assertGreater(whole, 100, "the sample must include readings that round to a whole number")

    def test_ndjson_line(self):
        line = to_ndjson_line(self.envelope())
        self.assertTrue(line.endswith("\n") and line.count("\n") == 1)
        self.assertEqual(list(json.loads(line)), list(ENVELOPE_KEYS))
        self.assertNotIn(", ", line)  # compact separators

    def test_check_envelope_catches_problems(self):
        e = self.envelope()
        cases = {
            "event_id": ("nope", "event_id is not a ULID"),
            "source_type": ("sensor", "source_type must be probe or report"),
            "event_time": ("2026-09-05T14:15:00Z", "event_time is not ISO 8601 UTC with milliseconds"),
            "mode": ("SLEEPING", "is not one of"),
            "scan_id": ("01K4X8QP2K", "scan_id is not a ULID"),
        }
        for key, (value, needle) in cases.items():
            bad = json.loads(json.dumps(e))
            bad[key] = value
            with self.subTest(key=key):
                self.assertTrue(any(needle in p for p in check_envelope(bad)), check_envelope(bad))
        bad = json.loads(json.dumps(e))
        bad["payload"]["kyber_resonance"] = 140
        self.assertTrue(any("kyber_resonance" in p for p in check_envelope(bad)))
        bad = json.loads(json.dumps(e))
        del bad["is_synthetic"]
        self.assertTrue(any("keys are" in p for p in check_envelope(bad)))


class DeviceTests(unittest.TestCase):
    def test_battery_drains_a_point_a_day_and_recharges(self):
        levels = [device.battery_pct(T0 + timedelta(days=d)) for d in range(0, 170)]
        self.assertTrue(all(20 <= v <= 100 for v in levels))
        drops = [a - b for a, b in zip(levels, levels[1:])]
        self.assertTrue(all(d in (1, -79, 0, 2, -80, -78) for d in drops), set(drops))
        self.assertGreaterEqual(sum(d < -50 for d in drops), 1, "a recharge happens within 170 days")

    def test_temperature_is_around_forty_with_a_diurnal_swing(self):
        temps = [device.sensor_temp_c(T0 + timedelta(hours=h), 0.0) for h in range(24)]
        self.assertAlmostEqual(sum(temps) / 24, 40.0, delta=0.05)
        self.assertGreater(max(temps) - min(temps), 5.5)
        self.assertTrue(all(-50 <= t <= 120 for t in temps))


class SweepTests(unittest.TestCase):
    def test_sixty_valid_envelopes_sharing_a_scan_id(self):
        probe = SimProbe(SECTORS, seed=1)
        sweep = probe.sweep(0, T0)
        self.assertEqual(len(sweep), 60)
        self.assertEqual([e["sector_id"] for e in sweep], [s.sector_id for s in SECTORS])
        self.assertEqual(len({e["scan_id"] for e in sweep}), 1)
        self.assertEqual(len({e["event_id"] for e in sweep}), 60)
        for e in sweep:
            self.assertEqual(check_envelope(e), [], e)
            self.assertEqual((e["source_id"], e["source_type"], e["mode"]), ("probe-01", "probe", "CONNECTED"))
            self.assertIs(e["is_synthetic"], False)
        # event_time is when the reading was taken, in planet order, 0-3 s after the scan boundary
        times = [datetime.strptime(e["event_time"], "%Y-%m-%dT%H:%M:%S.%fZ") for e in sweep]
        self.assertEqual(times, sorted(times))
        self.assertLess((times[-1] - times[0]).total_seconds(), 3.01)
        self.assertEqual([decode_ulid_time(e["event_id"]) for e in sweep],
                         [ts_ms(T0) + 50 * j for j in range(60)])
        self.assertEqual(decode_ulid_time(sweep[0]["scan_id"]), ts_ms(T0))

    def test_deterministic_with_a_seed_and_different_across_seeds(self):
        def lines(seed, scans=3):
            probe = SimProbe(SECTORS, seed=seed)
            out = []
            for i in range(scans):
                out += [to_ndjson_line(e) for e in probe.sweep(i, T0 + timedelta(minutes=15 * i))]
            return out
        self.assertEqual(lines(7), lines(7))
        self.assertNotEqual(lines(7), lines(8))

    def test_a_scan_can_be_regenerated_on_its_own_ids_and_temperature(self):
        """ULIDs and sensor noise are keyed by scan index, so they do not depend on earlier scans (the
        walk values do, by design: state persists across scans)."""
        a = SimProbe(SECTORS, seed=3)
        a.sweep(0, T0)
        second = a.sweep(1, T0 + timedelta(minutes=15))
        b = SimProbe(SECTORS, seed=3)
        b.sweep(0, T0)
        again = b.sweep(1, T0 + timedelta(minutes=15))
        self.assertEqual(second, again)

    def test_live_probe_without_a_seed_is_not_reproducible(self):
        a = SimProbe(SECTORS)
        b = SimProbe(SECTORS)
        self.assertFalse(a.deterministic)
        self.assertNotEqual([e["event_id"] for e in a.sweep(0, T0)], [e["event_id"] for e in b.sweep(0, T0)])

    def test_synthetic_sweep_carries_the_simulated_ingest_time(self):
        probe = SimProbe(SECTORS, seed=2)
        ingest = T0 + timedelta(hours=3)
        sweep = probe.sweep(0, T0, mode="DISCONNECTED", is_synthetic=True, synthetic_ingest_ts=ingest)
        for e in sweep:
            self.assertEqual(check_envelope(e), [])
            self.assertEqual((e["mode"], e["is_synthetic"], e["synthetic_ingest_ts"]),
                             ("DISCONNECTED", True, "2026-09-05T17:15:00.000Z"))

    def test_injected_signature_shows_in_the_sweep(self):
        probe = SimProbe(SECTORS, seed=4)
        probe.inject("coruscant", "civil_unrest", start_scan=2, ramp=1, hold=2, decay=1)
        rows = {}
        for i in range(5):
            for e in probe.sweep(i, T0 + timedelta(minutes=15 * i)):
                if e["sector_id"] == "coruscant":
                    rows[i] = e["payload"]
        coruscant = next(s for s in SECTORS if s.sector_id == "coruscant")
        # civil_unrest's emergency target is derived from the doc 03 threshold (dark +5 sigma at 5.75); every
        # channel is pinned during the hold
        target = target_for("civil_unrest")
        self.assertEqual(target, {"midi": 0, "kyber": 0, "dark": 5})
        self.assertEqual(rows[3]["dark_side_activity"],
                         round(coruscant.dark_baseline + target["dark"] * coruscant.dark_sigma, 1))
        self.assertEqual(rows[3]["kyber_resonance"], round(coruscant.kyber_baseline, 1))
        self.assertEqual(rows[3]["midichlorian_ppm"], round(coruscant.midi_baseline, 1))

    def test_the_frozen_seed_is_what_the_tests_ran_against(self):
        self.assertEqual(len(SECTORS), 60)
        self.assertEqual(len(sha256_file()), 64)


if __name__ == "__main__":
    unittest.main()
