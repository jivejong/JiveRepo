"""The 90-day backfill generator (doc 07 step 5, doc 04 "Backfill window"): the texture file and its validation, the
gap / BURST / ingest-time plan, the generated files, byte-identical regeneration, the manifest and `verify`, the
overlap refusals and the command line. Offline.

Generation tests use the nine planets the texture names, not all 60, so a full 90-day run takes seconds; the row
arithmetic is checked as scans x planets, and the plan arithmetic (batches of 500) is checked with all 60.
"""
import hashlib
import itertools
import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backfill as cli  # noqa: E402
from forcesim import backfill as bf  # noqa: E402
from forcesim.constants import SCANS_PER_DAY  # noqa: E402
from forcesim.envelope import check_envelope, decode_ulid_time, ts_ms  # noqa: E402
from forcesim.sectors import DEFAULT_SEED as SECTOR_PATH  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402
from forcesim.signatures import margin_factor, target_for  # noqa: E402
from forcesim.window import SCANS, BackfillWindow, OverlapRefused, make_window  # noqa: E402

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
TEXTURE_PATH = ROOT / "edge" / "backfill_texture.json"
LIVE = datetime(2026, 9, 26, 14, 27, 0, tzinfo=UTC)
ALL = load_sectors()
NAMED = ["tatooine", "dantooine", "kamino", "coruscant", "mon_cala", "corellia", "kashyyyk", "endor", "bespin"]
SUBSET = [s for s in ALL if s.sector_id in NAMED]
WINDOW = make_window(LIVE)
TEXTURE = bf.load_texture(TEXTURE_PATH, SUBSET)


def texture_with(**changes):
    """The real texture file with some top-level keys replaced, as a temp file path."""
    obj = json.loads(TEXTURE_PATH.read_text(encoding="utf-8"))
    obj.update(changes)
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(obj, handle)
    handle.close()
    return handle.name


def lines_of(data):
    return [json.loads(l) for l in data.decode("utf-8").splitlines()]


class TextureFileTests(unittest.TestCase):
    """The committed texture is what doc 07 asks for; the validator refuses what would break the generator."""

    def setUp(self):
        self.texture = bf.load_texture(TEXTURE_PATH, ALL)
        self.by_id = {s.sector_id: s for s in ALL}

    def test_doc_07_texture_counts(self):
        self.assertIn(len(self.texture.drifts), (3, 4))                                # "drift on 3-4 planets"
        self.assertEqual(len({d.sector for d in self.texture.drifts}), len(self.texture.drifts))
        self.assertIn(len(self.texture.emergencies), (3, 4))                           # "3-4 injected emergencies"
        self.assertEqual(len({e.signature for e in self.texture.emergencies}), len(self.texture.emergencies))
        self.assertGreaterEqual(len(self.texture.gaps), 3)                             # "a few DISCONNECTED gaps"
        self.assertEqual(self.texture.batch_events, 500)                               # doc 04: batches of 500

    def test_emergencies_hold_three_to_five_scans_and_every_gap_is_one_to_three_hours(self):
        for e in self.texture.emergencies:
            self.assertTrue(3 <= e.hold <= 5, e)
        for g in self.texture.gaps:
            self.assertTrue(4 <= g.scans <= 12, g)                                     # 1-3 h at 15 minutes a scan

    def test_the_riser_has_no_ambient_spikes_so_the_check_tests_the_trend(self):
        self.assertEqual(self.by_id[self.texture.riser.sector].dark_spike_probability, 0.0)
        self.assertEqual(self.texture.riser.channel, "dark")

    def test_nothing_overlaps_and_everything_fits_the_window(self):
        for e in self.texture.emergencies:
            self.assertTrue(0 <= e.start_scan and e.end_scan <= SCANS, e)
        spans = sorted([(g.first, g.end) for g in self.texture.gaps])
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            self.assertLess(a1, b0)
        for e in self.texture.emergencies:
            for g in self.texture.gaps:
                self.assertTrue(e.end_scan <= g.first or e.start_scan > g.end)

    def test_the_validator_refuses_bad_textures(self):
        good = json.loads(TEXTURE_PATH.read_text(encoding="utf-8"))
        cases = {
            "unknown sector": {"drifts": [{"sector": "nowhere", "channel": "midi", "total_sigma": 1}]},
            "bad channel": {"drifts": [{"sector": "corellia", "channel": "heat", "total_sigma": 1}]},
            "zero drift": {"drifts": [{"sector": "corellia", "channel": "midi", "total_sigma": 0}]},
            "riser not dark": {"riser": {"sector": "mon_cala", "channel": "midi", "total_sigma": 5}},
            "unproducible signature": {"emergencies": [dict(good["emergencies"][0], signature="veiled_presence")]},
            "hold under 2 scans": {"emergencies": [dict(good["emergencies"][0], hold=1)]},
            "emergency past the end": {"emergencies": [dict(good["emergencies"][0], day=-1, scan_of_day=95)]},
            "day out of range": {"emergencies": [dict(good["emergencies"][0], day=-91)]},
            "gap over an emergency": {"gaps": [dict(day=-71, scan_of_day=38, scans=8)]},
            "gap at the first scan": {"gaps": [dict(day=-90, scan_of_day=0, scans=4)]},
            "gap ends the window": {"gaps": [dict(day=-1, scan_of_day=90, scans=6)]},
            "gaps too close": {"gaps": [dict(day=-30, scan_of_day=10, scans=8), dict(day=-30, scan_of_day=18, scans=4)]},
            "no burst block": {"burst": {}},
            "float batch": {"burst": {"batch_events": 500.5, "batch_pause_seconds": 10, "reconnect_lead_seconds": 5}},
            "wrong version": {"version": 2},
        }
        for name, change in cases.items():
            with self.subTest(name):
                path = texture_with(**change)
                with self.assertRaises(bf.BackfillError):
                    bf.load_texture(path, ALL)

    def test_a_drain_that_outlasts_the_scan_interval_is_refused(self):
        slow = bf.load_texture(texture_with(burst={"batch_events": 10, "batch_pause_seconds": 60,
                                                   "reconnect_lead_seconds": 5}), ALL)
        with self.assertRaises(bf.BackfillError):
            bf.Plan(WINDOW, slow, 1, 60)


class PlanTests(unittest.TestCase):
    """Modes and the simulated ingest time, with all 60 planets (a 12-scan gap is 720 events: two batches)."""

    def setUp(self):
        self.texture = bf.load_texture(TEXTURE_PATH, ALL)
        self.plan = bf.Plan(WINDOW, self.texture, 7, 60)
        self.gap = {g.day: g for g in self.texture.gaps}

    def test_gap_scans_are_disconnected_and_the_recovery_scan_is_burst(self):
        for spec in self.texture.gaps:
            for i in range(spec.first, spec.end):
                self.assertEqual(self.plan.mode(i), "DISCONNECTED")
            self.assertEqual(self.plan.mode(spec.first - 1), "CONNECTED")
            self.assertEqual(self.plan.mode(spec.end), "BURST")
            self.assertEqual(self.plan.mode(spec.end + 1), "CONNECTED")
        self.assertEqual(sum(self.plan.mode(i) == "BURST" for i in range(SCANS)), len(self.texture.gaps))

    def test_drain_batches_are_500_events_in_event_time_order_ten_seconds_apart(self):
        spec = self.gap[-64]                                     # 12 scans x 60 = 720 events
        plan = next(g for g in self.plan.gaps if g.first == spec.first)
        self.assertEqual((plan.events, plan.batches), (720, 2))
        stamps = [self.plan.event_ingest(i, j) for i in range(spec.first, spec.end) for j in range(60)]
        self.assertEqual(stamps, sorted(stamps))
        first, second = plan.reconnect + bf.DRAIN_LATENCY, plan.reconnect + timedelta(seconds=10) + bf.DRAIN_LATENCY
        self.assertEqual(stamps[:500], [first] * 500)
        self.assertEqual(stamps[500:], [second] * 220)
        self.assertEqual(plan.reconnect, self.plan.times[spec.end] - timedelta(seconds=5))
        self.assertEqual(plan.drain_end, plan.reconnect + timedelta(seconds=20))

    def test_a_one_batch_gap(self):
        spec = self.gap[-49]                                     # 4 scans = 240 events
        plan = next(g for g in self.plan.gaps if g.first == spec.first)
        self.assertEqual((plan.events, plan.batches), (240, 1))

    def test_drained_events_are_hours_behind_and_normal_scans_are_seconds_behind(self):
        spec = self.gap[-80]
        lag = (self.plan.event_ingest(spec.first, 0) - self.plan.times[spec.first]).total_seconds()
        self.assertGreater(lag, bf.REPLAYED_LAG_SECONDS)
        for i in (0, 1000, SCANS - 1):
            ingest = self.plan.scan_ingest(i)
            self.assertTrue(timedelta(seconds=4) <= ingest - self.plan.times[i] < timedelta(seconds=5))
            self.assertGreater(ingest - self.plan.times[i], timedelta(milliseconds=2950))   # after the last reading

    def test_the_ingest_time_does_not_depend_on_anything_but_the_seed_and_the_scan(self):
        other = bf.Plan(WINDOW, self.texture, 7, 60)
        self.assertEqual([self.plan.scan_ingest(i) for i in range(50)], [other.scan_ingest(i) for i in range(50)])
        different = bf.Plan(WINDOW, self.texture, 8, 60)
        self.assertNotEqual([self.plan.scan_ingest(i) for i in range(50)], [different.scan_ingest(i) for i in range(50)])


class GeneratedBackfillTests(unittest.TestCase):
    """One full 90-day run over the nine planets, written to disk, and the checks on it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="backfill-test-"))
        cls.out = cls.tmp / "out"
        cls.manifest = bf.generate(SUBSET, WINDOW, TEXTURE, TEXTURE_PATH, 42, cls.out)
        cls.files = sorted((cls.out / "scans").glob("*.ndjson"), key=lambda p: p.name)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_one_file_per_scan_with_one_line_per_planet(self):
        self.assertEqual(len(self.files), SCANS)
        self.assertEqual(self.manifest["files"], SCANS)
        self.assertEqual(self.manifest["rows"], SCANS * len(SUBSET))
        for path in self.files[::400] + self.files[-1:]:
            self.assertEqual(len(lines_of(path.read_bytes())), len(SUBSET))

    def test_file_names_are_source_and_ulid_and_ascend_with_scan_time(self):
        names = [p.name for p in self.files]
        self.assertEqual(names, sorted(names))
        for i, name in enumerate(names[::500]):
            match = re.fullmatch(r"probe-01-([0-9A-HJKMNP-TV-Z]{26})[.]ndjson", name)
            self.assertIsNotNone(match, name)
        first = decode_ulid_time(re.fullmatch(r"probe-01-(.{26})[.]ndjson", names[0]).group(1))
        self.assertEqual(first, ts_ms(WINDOW.start))

    def test_every_event_is_a_valid_synthetic_envelope_inside_the_window_and_before_the_live_data(self):
        ids, scan_ids, times = set(), set(), []
        for path in self.files:
            envelopes = lines_of(path.read_bytes())
            self.assertEqual(len({e["scan_id"] for e in envelopes}), 1)
            scan_ids.add(envelopes[0]["scan_id"])
            for e in envelopes:
                self.assertEqual(check_envelope(e), [])
                self.assertIs(e["is_synthetic"], True)
                self.assertIsNotNone(e["synthetic_ingest_ts"])
                self.assertGreaterEqual(e["synthetic_ingest_ts"], e["event_time"])
                self.assertEqual(e["source_id"], "probe-01")
                ids.add(e["event_id"])
                times.append(e["event_time"])
        self.assertEqual(len(ids), SCANS * len(SUBSET))
        self.assertEqual(len(scan_ids), SCANS)
        self.assertEqual(min(times), "2026-06-28T14:15:00.000Z")
        self.assertTrue(max(times) <= "2026-09-26T14:00:00.400Z", max(times))       # 14:00 + 8 x 50 ms: nine planets
        self.assertLess(max(times), "2026-09-26T14:27:00.000Z")                     # before the earliest live event

    def test_the_content_hash_is_the_hash_of_the_files_on_disk(self):
        disk = bf.hash_directory(self.out / "scans")
        self.assertEqual(disk.hexdigest(), self.manifest["content_hash"]["value"])
        self.assertEqual((disk.files, disk.bytes), (self.manifest["files"], self.manifest["bytes"]))
        manual = hashlib.sha256()
        for path in self.files:
            data = path.read_bytes()
            manual.update(path.name.encode() + bytes([0]) + str(len(data)).encode() + bytes([0]) + data)
        self.assertEqual(manual.hexdigest(), disk.hexdigest())

    def test_a_regeneration_is_byte_identical(self):
        self.assertEqual(bf.verify(self.manifest, SUBSET, TEXTURE_PATH, out_dir=self.out), [])

    def test_a_slice_is_identical_to_the_same_files_of_the_full_run(self):
        window_first = SCANS - 71 * SCANS_PER_DAY
        run = bf.Backfill(SUBSET, WINDOW, TEXTURE, 42, scan_range=(window_first, window_first + SCANS_PER_DAY))
        got = dict(itertools.islice(run.files(), SCANS_PER_DAY))      # stops stepping once the day is done
        self.assertEqual(len(got), SCANS_PER_DAY)
        for name, data in got.items():
            self.assertEqual(data, (self.out / "scans" / name).read_bytes())

    def test_a_different_seed_gives_different_data(self):
        run = bf.Backfill(SUBSET, WINDOW, TEXTURE, 43, scan_range=(0, 2))
        other = dict(itertools.islice(run.files(), 2))
        mine = {p.name: p.read_bytes() for p in self.files[:2]}
        self.assertNotEqual(list(other.values()), list(mine.values()))
        self.assertNotEqual(list(other), list(mine))

    def test_rows_by_mode_and_the_replayed_rows(self):
        report = self.manifest["report"]
        gaps = sum(g.scans for g in TEXTURE.gaps)
        self.assertEqual(report["modes"], {"BURST": len(TEXTURE.gaps) * len(SUBSET),
                                           "CONNECTED": (SCANS - gaps - len(TEXTURE.gaps)) * len(SUBSET),
                                           "DISCONNECTED": gaps * len(SUBSET)})
        plan = bf.Plan(WINDOW, TEXTURE, 42, len(SUBSET))
        self.assertEqual(report["rows_with_lag_over_1800_s"], sum(
            1 for i in range(SCANS) for j in range(len(SUBSET))
            if (plan.event_ingest(i, j) - plan.times[i]).total_seconds() > bf.REPLAYED_LAG_SECONDS))
        self.assertGreater(report["rows_with_lag_over_1800_s"], 0)

    def test_the_files_carry_the_modes_and_drain_times_the_plan_says(self):
        spec = TEXTURE.gaps[0]
        plan = bf.Plan(WINDOW, TEXTURE, 42, len(SUBSET))
        by_scan = {}
        for path in self.files:
            envelopes = lines_of(path.read_bytes())
            by_scan[envelopes[0]["event_time"]] = envelopes
        for i in (spec.first - 1, spec.first, spec.end - 1, spec.end, spec.end + 1):
            time = WINDOW.scan_times()[i].strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"
            envelopes = by_scan[time]
            self.assertEqual({e["mode"] for e in envelopes}, {plan.mode(i)})
            self.assertEqual([e["synthetic_ingest_ts"] for e in envelopes],
                             [bf.format_ts(plan.event_ingest(i, j)) for j in range(len(SUBSET))])

    def test_each_injected_emergency_is_sustained_and_classified_as_its_signature(self):
        emergencies = self.manifest["report"]["emergencies"]
        self.assertEqual([e["signature"] for e in emergencies], [e.signature for e in TEXTURE.emergencies])
        for e in emergencies:
            with self.subTest(e["signature"]):
                self.assertTrue(e["all_hold_scans_classify_as_signature"])
                self.assertGreater(e["min_hold_composite"], bf.EMERGENCY_THRESHOLD)
                self.assertGreaterEqual(e["consecutive_scans_at_emergency_level_as_signature"], 2)
                self.assertEqual(len(e["hold_scans"]), e["hold"])

    def test_the_riser_passes_as_a_trend_and_ambient_noise_alone_crosses_the_anomaly_threshold_on_every_planet(self):
        riser = self.manifest["report"]["riser"]
        self.assertGreater(riser["last_day_mean_z_dark"], 1.0)      # +5 sigma linear: the window mean is +2.5 sigma
        self.assertLess(riser["last_day_mean_z_dark"], 1.9)
        self.assertLess(riser["last_day_mean_composite"], bf.ANOMALY_THRESHOLD)   # the trend has not crossed it
        self.assertIs(riser["trend_below_anomaly"], True)
        self.assertNotIn("max_composite_below_anomaly", riser)      # the single-scan form was retired
        # The finding behind the single-scan form: the maximum composite of ANY planet is over the anomaly
        # threshold (4.0) from noise alone.
        self.assertGreater(riser["other_planets_max_composite"]["min"], bf.ANOMALY_THRESHOLD)
        self.assertGreater(riser["max_composite"], bf.ANOMALY_THRESHOLD)

    def test_the_trend_check_compares_the_last_days_mean_composite_with_the_anomaly_threshold(self):
        run = bf.Backfill(SUBSET, WINDOW, TEXTURE, 42)
        for _ in run.files():
            pass
        self.assertIs(run.report()["riser"]["trend_below_anomaly"], True)
        mean_composite = self.manifest["report"]["riser"]["last_day_mean_composite"]
        for threshold, expected in ((mean_composite + 0.01, True), (mean_composite - 0.01, False)):
            bf.ANOMALY_THRESHOLD, saved = threshold, bf.ANOMALY_THRESHOLD
            try:
                self.assertIs(run.report()["riser"]["trend_below_anomaly"], expected, threshold)
            finally:
                bf.ANOMALY_THRESHOLD = saved

    def test_ambient_episodes_are_counted_and_noise_dominates_the_threshold_crossings(self):
        ambient = self.manifest["report"]["ambient"]
        self.assertGreater(ambient["episodes"], 0)
        self.assertLessEqual(ambient["episodes"], 3 * ambient["expected_episodes"] + 10)
        self.assertGreater(ambient["scans_over_anomaly"]["outside_all_episodes"], 0)
        self.assertLessEqual(ambient["scans_over_anomaly"]["outside_all_episodes"], ambient["scans_over_anomaly"]["all"])

    def test_the_manifest_records_the_thresholds_and_the_targets_they_derive(self):
        m = self.manifest
        self.assertEqual(m["thresholds"], {"anomaly": 4.0, "emergency": 5.75, "sustained_scans": 2, "margin_k": 3,
                                           "margin": round(margin_factor(), 6)})
        self.assertEqual(m["thresholds"], bf.current_thresholds())
        self.assertEqual(m["targets"], {e.signature: target_for(e.signature) for e in TEXTURE.emergencies})
        self.assertEqual(m["targets"]["sith_presence"], {"midi": 0, "kyber": -3, "dark": 4})
        self.assertEqual(m["targets"]["civil_unrest"], {"midi": 0, "kyber": 0, "dark": 5})

    def test_the_report_counts_noise_runs_and_ambient_firing_at_the_emergency_threshold(self):
        ambient = self.manifest["report"]["ambient"]
        firing = ambient["ambient_episodes_firing_sustained_over_emergency"]
        self.assertEqual(firing["of"], ambient["episodes"])
        self.assertTrue(0 <= firing["fired"] <= firing["of"])
        runs = ambient["sustained_runs_over_emergency_outside_all_episodes"]
        pairs = ambient["consecutive_pairs_over_emergency"]["outside_all_episodes"]
        self.assertTrue(0 <= runs <= pairs)          # every run of 2+ scans holds at least one pair

    def test_the_manifest_records_inputs_not_machines(self):
        m = self.manifest
        self.assertEqual(m["seed"], 42)
        self.assertEqual(m["window"], WINDOW.to_manifest())
        self.assertEqual(m["texture"]["sha256"], hashlib.sha256(TEXTURE_PATH.read_bytes()).hexdigest())
        self.assertEqual(m["dim_sector"]["sha256"], hashlib.sha256(SECTOR_PATH.read_bytes()).hexdigest())
        self.assertEqual(m["texture"]["path"], "edge/backfill_texture.json")
        self.assertEqual(m["dim_sector"]["path"], "warehouse/dbt/seeds/dim_sector.csv")
        self.assertEqual(m["scan_range"], {"first": 0, "last_exclusive": SCANS, "partial": False})
        text = bf.manifest_text(m)
        self.assertNotIn(chr(92), text)                              # no Windows paths
        self.assertNotIn("Users", text)
        self.assertIsNone(re.search(r"[A-Za-z]:/", text))
        self.assertEqual(json.loads(text), m)
        self.assertTrue(text.endswith(chr(10)) and chr(13) not in text)

    def test_generate_refuses_a_non_empty_directory(self):
        with self.assertRaises(bf.BackfillError):
            bf.generate(SUBSET, WINDOW, TEXTURE, TEXTURE_PATH, 42, self.out, scan_range=(0, 1))


COMMITTED_MANIFEST = ROOT / "edge" / "backfill_manifest.json"


@unittest.skipUnless(COMMITTED_MANIFEST.exists(), "the frozen manifest is committed separately from the code")
class CommittedManifestTests(unittest.TestCase):
    """The frozen manifest still describes the code and inputs in the repo. It cannot regenerate 90 days here
    (`python edge/backfill.py verify` does that); it checks that nothing it depends on has moved."""

    def setUp(self):
        self.m = json.loads(COMMITTED_MANIFEST.read_text(encoding="utf-8"))

    def test_it_was_written_for_these_inputs(self):
        self.assertEqual(self.m["texture"]["sha256"], hashlib.sha256(TEXTURE_PATH.read_bytes()).hexdigest())
        self.assertEqual(self.m["dim_sector"]["sha256"], hashlib.sha256(SECTOR_PATH.read_bytes()).hexdigest())
        self.assertEqual(self.m["thresholds"], bf.current_thresholds())
        self.assertEqual(self.m["generator_version"], bf.GENERATOR_VERSION)
        self.assertEqual(self.m["manifest_version"], bf.MANIFEST_VERSION)
        self.assertEqual(self.m["targets"], {e.signature: target_for(e.signature)
                                             for e in bf.load_texture(TEXTURE_PATH, ALL).emergencies})

    def test_it_describes_the_whole_window_with_the_earliest_live_event_from_bronze(self):
        w = self.m["window"]
        self.assertEqual((w["window_start_utc"], w["window_end_utc"]), ("2026-06-28T14:15:00.000Z",
                                                                        "2026-09-26T14:15:00.000Z"))
        self.assertEqual(w["earliest_live_event_time_utc"], "2026-09-26T14:27:00.000Z")   # query (e0), PHASE2-RESULTS
        self.assertEqual(w, make_window(LIVE).to_manifest())
        self.assertEqual((self.m["files"], self.m["rows"], self.m["lines_per_file"]), (SCANS, SCANS * 60, 60))
        self.assertEqual(self.m["scan_range"], {"first": 0, "last_exclusive": SCANS, "partial": False})

    def test_the_recorded_report_meets_the_phase_2_checks(self):
        report = self.m["report"]
        for e in report["emergencies"]:
            self.assertTrue(e["all_hold_scans_classify_as_signature"], e["signature"])
            self.assertGreaterEqual(e["consecutive_scans_at_emergency_level_as_signature"], 2, e["signature"])
            self.assertGreater(e["min_hold_composite"], self.m["thresholds"]["emergency"], e["signature"])
        self.assertIs(report["riser"]["trend_below_anomaly"], True)
        self.assertLess(report["riser"]["last_day_mean_composite"], self.m["thresholds"]["anomaly"])

    def test_it_holds_no_machine_paths_or_secrets(self):
        text = COMMITTED_MANIFEST.read_text(encoding="utf-8")
        self.assertNotIn(chr(92), text)
        self.assertNotIn("Users", text)
        self.assertNotIn(chr(13), text)
        for word in ("token", "secret", "password", "DATABRICKS"):
            self.assertNotIn(word, text)


class VerifyTests(unittest.TestCase):
    """verify catches every way the backfill can stop matching its manifest. A one-day slice keeps each run short."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="backfill-verify-"))
        first = SCANS - 71 * SCANS_PER_DAY
        cls.range = (first, first + SCANS_PER_DAY)
        cls.out = cls.tmp / "out"
        cls.manifest = bf.generate(SUBSET, WINDOW, TEXTURE, TEXTURE_PATH, 42, cls.out, scan_range=cls.range)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_partial_manifest_says_so_and_verifies(self):
        self.assertTrue(self.manifest["scan_range"]["partial"])
        self.assertEqual(self.manifest["files"], SCANS_PER_DAY)
        self.assertEqual(bf.verify(self.manifest, SUBSET, TEXTURE_PATH, out_dir=self.out), [])

    def test_a_changed_byte_on_disk_is_caught(self):
        copy = self.tmp / "tampered"
        shutil.copytree(self.out, copy)
        victim = sorted((copy / "scans").glob("*.ndjson"))[10]
        victim.write_bytes(victim.read_bytes().replace(b'"tatooine"', b'"tatooinf"', 1))
        problems = bf.verify(self.manifest, SUBSET, TEXTURE_PATH, out_dir=copy, regenerate=False)
        self.assertEqual(len(problems), 1)
        self.assertIn("the files on disk differ", problems[0])

    def test_a_missing_and_an_extra_file_are_caught(self):
        for change in ("missing", "extra"):
            copy = self.tmp / change
            shutil.copytree(self.out, copy)
            files = sorted((copy / "scans").glob("*.ndjson"))
            if change == "missing":
                files[3].unlink()
            else:
                (copy / "scans" / "probe-01-7ZZZZZZZZZZZZZZZZZZZZZZZZZ.ndjson").write_bytes(b"{}" + chr(10).encode())
            with self.subTest(change):
                self.assertIn("the files on disk differ", " ".join(
                    bf.verify(self.manifest, SUBSET, TEXTURE_PATH, out_dir=copy, regenerate=False)))

    def test_a_wrong_seed_in_the_manifest_is_caught_by_the_regeneration(self):
        bad = json.loads(json.dumps(self.manifest))
        bad["seed"] = 43
        self.assertIn("the regeneration differs", " ".join(bf.verify(bad, SUBSET, TEXTURE_PATH)))

    def test_a_wrong_content_hash_in_the_manifest_is_caught_on_disk_without_regenerating(self):
        bad = json.loads(json.dumps(self.manifest))
        bad["content_hash"] = {"algorithm": "sha256", "method": "x", "value": "0" * 64}
        self.assertIn("the files on disk differ", " ".join(
            bf.verify(bad, SUBSET, TEXTURE_PATH, out_dir=self.out, regenerate=False)))
        with self.assertRaises(bf.BackfillError):
            bf.verify(self.manifest, SUBSET, TEXTURE_PATH, regenerate=False)   # nothing to check against

    def test_changed_thresholds_make_the_regeneration_not_comparable(self):
        """The injection targets derive from the doc 03 thresholds, so a manifest written under other
        thresholds cannot be regenerated by this code."""
        for name, value in (("ANOMALY_THRESHOLD", 3.0), ("EMERGENCY_THRESHOLD", 4.5)):
            saved = getattr(bf, name)
            setattr(bf, name, value)
            try:
                problems = bf.verify(self.manifest, SUBSET, TEXTURE_PATH, out_dir=self.out, regenerate=False)
            finally:
                setattr(bf, name, saved)
            with self.subTest(name):
                self.assertEqual(len(problems), 1)
                self.assertIn("the doc 03 thresholds changed", problems[0])
        old = json.loads(json.dumps(self.manifest))
        del old["thresholds"]
        self.assertIn("thresholds changed", " ".join(bf.verify(old, SUBSET, TEXTURE_PATH, out_dir=self.out,
                                                               regenerate=False)))

    def test_a_changed_texture_or_dim_sector_makes_the_regeneration_not_comparable(self):
        other = texture_with(riser={"sector": "mon_cala", "channel": "dark", "total_sigma": 4.0})
        self.assertIn("texture file changed", " ".join(bf.verify(self.manifest, SUBSET, other)))
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as f:
            f.write(SECTOR_PATH.read_bytes() + b" ")
        self.assertIn("dim_sector.csv changed", " ".join(bf.verify(self.manifest, SUBSET, TEXTURE_PATH, sector_path=f.name)))


class RefusalTests(unittest.TestCase):
    def test_a_window_that_would_overlap_the_live_data_is_refused_before_anything_is_generated(self):
        too_late = BackfillWindow(end=WINDOW.end + timedelta(minutes=15), earliest_live=LIVE)
        with self.assertRaises(OverlapRefused):
            bf.Backfill(SUBSET, too_late, TEXTURE, 1)

    def test_the_per_event_guard_refuses_a_synthetic_event_at_or_after_the_earliest_live_event(self):
        class Lenient(BackfillWindow):
            def validate(self):          # a window whose own check is bypassed: the guard must still refuse
                return self

        live = WINDOW.scan_times()[100] + timedelta(milliseconds=120)   # scan 100's fourth reading
        window = Lenient(end=WINDOW.end, earliest_live=live)
        run = bf.Backfill(SUBSET, window, TEXTURE, 1)
        produced = []
        with self.assertRaises(OverlapRefused) as caught:
            for name, data in run.files():
                produced.append(name)
        self.assertEqual(len(produced), 100)                             # scans 0-99 were fine, scan 100 was refused
        self.assertIn("at or after the earliest live event_time", str(caught.exception))

    def test_scan_range_must_lie_inside_the_window(self):
        for bad in ((-1, 5), (5, 5), (0, SCANS + 1)):
            with self.assertRaises(bf.BackfillError):
                bf.Backfill(SUBSET, WINDOW, TEXTURE, 1, scan_range=bad)

    def test_an_infeasible_injection_is_refused(self):
        texture = bf.load_texture(TEXTURE_PATH, ALL)
        bad = bf.Texture(texture.drifts, (bf.EmergencySpec("utapau", "kyber_cache", -30, 10, 2, 3, 3),), texture.riser,
                         texture.gaps, texture.batch_events, texture.batch_pause_seconds, texture.reconnect_lead_seconds)
        from forcesim.signatures import InjectionRefused
        with self.assertRaises(InjectionRefused):
            next(bf.Backfill(ALL, WINDOW, bad, 1).files())


class CommandLineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="backfill-cli-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv), sectors=SUBSET)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_earliest_live_is_required_and_must_be_a_utc_time(self):
        code, _, err = self.run_cli("generate", "--out", str(self.tmp / "a"))
        self.assertEqual(code, 2)
        code, _, err = self.run_cli("generate", "--out", str(self.tmp / "a"), "--earliest-live", "2026-09-26 14:27")
        self.assertIn("not an ISO 8601 UTC time", str(code) + err)

    def test_it_refuses_to_write_inside_the_repository(self):
        code, _, err = self.run_cli("generate", "--out", str(ROOT / "edge" / "should-not-exist"),
                                    "--earliest-live", "2026-09-26T14:27:00.000Z")
        self.assertEqual(code, 2)
        self.assertIn("inside the repository", err)
        self.assertFalse((ROOT / "edge" / "should-not-exist").exists())

    def test_a_window_end_after_the_allowed_boundary_is_refused(self):
        code, _, err = self.run_cli("generate", "--out", str(self.tmp / "late"), "--earliest-live",
                                    "2026-09-26T14:27:00.000Z", "--end", "2026-09-26T14:30:00.000Z")
        self.assertEqual(code, 2)
        self.assertIn("refused", err)
        self.assertFalse((self.tmp / "late").exists())

    def test_generate_a_day_then_verify_it(self):
        out, manifest = self.tmp / "day", self.tmp / "m" / "manifest.json"
        code, text, err = self.run_cli("generate", "--out", str(out), "--earliest-live", "2026-09-26T14:27:00.000Z",
                                       "--only-day", "-71", "--manifest", str(manifest))
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(code, 0 if recorded["report"]["riser"]["trend_below_anomaly"] else 3, err)
        self.assertTrue(recorded["report"]["riser"]["trend_below_anomaly"])
        self.assertEqual(code, 0)
        self.assertEqual(len(list((out / "scans").glob("*.ndjson"))), SCANS_PER_DAY)
        self.assertIn("injected emergencies", text)
        self.assertIn("tatooine sith_presence", text)
        self.assertEqual(recorded["window"]["window_end_utc"], "2026-09-26T14:15:00.000Z")
        self.assertNotIn(chr(13), manifest.read_text(encoding="utf-8"))
        code, text, err = self.run_cli("verify", "--out", str(out), "--manifest", str(manifest))
        self.assertEqual(code, 0, err)
        self.assertIn("verify OK", text)
        code, _, err = self.run_cli("generate", "--out", str(out), "--earliest-live", "2026-09-26T14:27:00.000Z",
                                    "--only-day", "-71")
        self.assertEqual(code, 2)
        self.assertIn("is not empty", err)
        victim = sorted((out / "scans").glob("*.ndjson"))[0]
        victim.write_bytes(victim.read_bytes() + b" ")
        code, text, err = self.run_cli("verify", "--out", str(out), "--manifest", str(manifest), "--disk-only")
        self.assertEqual(code, 1)
        self.assertIn("the files on disk differ", err)
        victim.write_bytes(victim.read_bytes()[:-1])
        code, text, err = self.run_cli("verify", "--out", str(out), "--manifest", str(manifest), "--disk-only")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("the files on disk match", text)
        code, _, err = self.run_cli("verify", "--manifest", str(manifest), "--disk-only")
        self.assertEqual(code, 2)

    def test_it_exits_3_when_the_riser_trend_has_crossed_the_anomaly_threshold(self):
        bf.ANOMALY_THRESHOLD, saved = 1.0, bf.ANOMALY_THRESHOLD      # the riser's last-day mean composite is about 2.0
        try:
            code, text, err = self.run_cli("generate", "--out", str(self.tmp / "crossed"), "--earliest-live",
                                           "2026-09-26T14:27:00.000Z", "--only-day", "-71")
        finally:
            bf.ANOMALY_THRESHOLD = saved
        self.assertEqual(code, 3, err)
        recorded = json.loads((self.tmp / "crossed" / "manifest.json").read_text(encoding="utf-8"))
        self.assertIs(recorded["report"]["riser"]["trend_below_anomaly"], False)
        self.assertIn("is under 1.0: False", text)

    def test_verify_defaults_to_the_committed_manifest(self):
        self.assertEqual(cli.MANIFEST_PATH, ROOT / "edge" / "backfill_manifest.json")
        self.assertEqual(cli.build_parser().parse_args(["verify"]).manifest, str(ROOT / "edge" / "backfill_manifest.json"))
        self.assertEqual(cli.build_parser().parse_args(["verify", "--manifest", "x.json"]).manifest, "x.json")

    def test_only_day_must_be_in_range(self):
        code, _, err = self.run_cli("generate", "--out", str(self.tmp / "bad"), "--earliest-live",
                                    "2026-09-26T14:27:00.000Z", "--only-day", "0")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
