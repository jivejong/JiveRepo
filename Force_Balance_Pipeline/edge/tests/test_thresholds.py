"""The threshold analysis (edge/forcesim/thresholds.py, edge/analyze_thresholds.py): its tables are monotone, agree
with the backfill report at the current thresholds, and the recomputed injection targets are derived, not
copied. Offline, on the nine planets the texture names."""
import contextlib
import io
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze_thresholds as cli  # noqa: E402
from forcesim import backfill as bf  # noqa: E402
from forcesim import thresholds as th  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402
from forcesim.signatures import EMERGENCY_THRESHOLD, margin_factor, target_for  # noqa: E402
from forcesim.window import make_window  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TEXTURE_PATH = ROOT / "edge" / "backfill_texture.json"
LIVE = datetime(2026, 9, 26, 14, 27, 0, tzinfo=timezone.utc)
NAMED = ["tatooine", "dantooine", "kamino", "coruscant", "mon_cala", "corellia", "kashyyyk", "endor", "bespin"]
SUBSET = [s for s in load_sectors() if s.sector_id in NAMED]
TEXTURE = bf.load_texture(TEXTURE_PATH, SUBSET)


class RangeTests(unittest.TestCase):
    def test_frange_is_inclusive_and_exact(self):
        self.assertEqual(th.frange(4.5, 8.0, 0.25), [4.5 + 0.25 * i for i in range(15)])
        self.assertEqual(len(th.frange(*th.EMERGENCY_RANGE)), 15)
        self.assertEqual(th.frange(*th.ANOMALY_RANGE)[0], 3.0)
        self.assertEqual(len(th.frange(*th.ANOMALY_RANGE)), 21)

    def test_peak_sustained(self):
        self.assertEqual(th._peak_sustained([1, 5, 6, 2, 9]), 5)
        self.assertEqual(th._peak_sustained([1, 9, 2]), 2)
        self.assertEqual(th._peak_sustained([7]), 0.0)


class NoiseCountTests(unittest.TestCase):
    """noise_counts is shared by the backfill report and the analysis, so it is checked on its own."""

    def test_scans_pairs_and_runs(self):
        series = [1, 9, 9, 9, 1, 9, 1, 9, 9, 1]
        self.assertEqual(bf.noise_counts(series, 5), (6, 3, 2))       # runs of 3 and 2 count once each; a single scan none
        self.assertEqual(bf.noise_counts(series, 0), (10, 9, 1))
        self.assertEqual(bf.noise_counts(series, 9), (0, 0, 0))       # over means strictly over
        self.assertEqual(bf.noise_counts([], 1), (0, 0, 0))

    def test_a_blocked_scan_ends_a_run_and_is_not_counted(self):
        series = [9, 9, 9, 9, 9]
        self.assertEqual(bf.noise_counts(series, 5, blocked={2}), (4, 2, 2))
        self.assertEqual(bf.noise_counts(series, 5, blocked={0, 1, 2, 3, 4}), (0, 0, 0))

    def test_a_run_is_counted_when_it_reaches_the_sustained_requirement(self):
        self.assertEqual(bf.SUSTAINED_SCANS, 2)
        self.assertEqual(bf.noise_counts([9, 1, 9, 1], 5)[2], 0)
        self.assertEqual(bf.noise_counts([9, 9], 5)[2], 1)
        self.assertEqual(bf.noise_counts([9, 9, 9, 9], 5)[2], 1)


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backfill = bf.Backfill(SUBSET, make_window(LIVE), TEXTURE, 42)
        for _ in cls.backfill.files():
            pass
        cls.result = th.analyze(cls.backfill)
        cls.report = cls.backfill.report()

    def test_noise_and_detection_never_increase_with_the_threshold(self):
        for table, keys in ((self.result["emergency"], ("noise_scans", "noise_pairs", "noise_runs", "ambient_firing",
                                                        "injected_firing")),
                            (self.result["anomaly"], ("noise_scans", "ambient_with_scan", "injected_with_scan"))):
            for key in keys:
                values = [row[key] for row in table]
                with self.subTest(key=key):
                    self.assertEqual(values, sorted(values, reverse=True))

    def test_the_tables_cover_the_ranges_at_quarter_steps(self):
        self.assertEqual([r["threshold"] for r in self.result["emergency"]], th.frange(4.5, 8.0, 0.25))
        self.assertEqual([r["threshold"] for r in self.result["anomaly"]], th.frange(3.0, 8.0, 0.25))
        self.assertEqual([r["threshold"] for r in self.result["targets"]], th.frange(4.5, 8.0, 0.25))

    def test_the_row_at_the_current_emergency_threshold_matches_the_backfill_report(self):
        row = next(r for r in self.result["emergency"] if r["threshold"] == EMERGENCY_THRESHOLD)
        ambient = self.report["ambient"]
        self.assertEqual(row["noise_scans"], ambient["scans_over_emergency"]["outside_all_episodes"])
        self.assertEqual(row["noise_pairs"], ambient["consecutive_pairs_over_emergency"]["outside_all_episodes"])
        self.assertEqual(row["noise_runs"], ambient["sustained_runs_over_emergency_outside_all_episodes"])
        firing = ambient["ambient_episodes_firing_sustained_over_emergency"]
        self.assertEqual((row["ambient_firing"], row["ambient"]), (firing["fired"], firing["of"]))
        # the data was generated with targets for this threshold, so every injected emergency fires
        self.assertEqual((row["injected_firing"], row["injected"]), (4, 4))

    def test_the_row_at_the_current_anomaly_threshold_matches_the_report(self):
        row = next(r for r in self.result["anomaly"] if r["threshold"] == bf.ANOMALY_THRESHOLD)
        self.assertEqual(row["noise_scans"], self.report["ambient"]["scans_over_anomaly"]["outside_all_episodes"])

    def test_runs_per_week_is_runs_over_ninety_days(self):
        for row in self.result["emergency"]:
            self.assertAlmostEqual(row["runs_per_week"], round(row["noise_runs"] / 90 * 7, 2))

    def test_targets_are_recomputed_at_each_threshold_and_match_the_generator(self):
        for row in self.result["targets"]:
            for s in row["signatures"]:
                with self.subTest(threshold=row["threshold"], signature=s["signature"]):
                    t = target_for(s["signature"], threshold=row["threshold"])
                    self.assertEqual(s["target"], (t["midi"], t["kyber"], t["dark"]))
                    self.assertGreater(s["composite"], row["threshold"] * margin_factor())
        current = next(r for r in self.result["targets"] if r["threshold"] == EMERGENCY_THRESHOLD)
        self.assertTrue(current["all_feasible"])
        self.assertEqual({s["signature"]: dict(zip(("midi", "kyber", "dark"), s["target"])) for s in current["signatures"]},
                         {e.signature: target_for(e.signature) for e in TEXTURE.emergencies})

    def test_an_infeasible_target_is_reported(self):
        """utapau cannot host kyber_cache once its target needs kyber +7 sigma (80 + 7 x 9.6 = 147.2 > 100)."""
        from forcesim.sectors import load_sectors as load
        utapau = next(s for s in load() if s.sector_id == "utapau")
        target = dict(zip(("midi", "kyber", "dark"), (0, 7, 0)))
        self.assertFalse(th._feasible(utapau, "kyber_cache", target))
        self.assertTrue(th._feasible(next(s for s in SUBSET if s.sector_id == "coruscant"), "civil_unrest",
                                     {"midi": 0, "kyber": 0, "dark": 5}))

    def test_markdown_has_the_three_tables(self):
        text = th.format_markdown(self.result)
        self.assertIn("EMERGENCY threshold", text)
        self.assertIn("ANOMALY threshold", text)
        self.assertIn("Injection targets recomputed", text)
        self.assertEqual(text.count("| 5.75 |"), 3)         # one row in each of the three tables
        self.assertNotIn("recommend", text.lower().replace("it recommends nothing", ""))
        self.assertTrue(text.endswith(chr(10)) and chr(13) not in text)


class CommandLineTests(unittest.TestCase):
    def test_it_prints_the_tables_and_writes_them_on_request(self):
        import tempfile
        out = Path(tempfile.mkdtemp()) / "tables.md"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--earliest-live", "2026-09-26T14:27:00.000Z", "--out", str(out)], sectors=SUBSET)
        self.assertEqual(code, 0)
        self.assertEqual(out.read_text(encoding="utf-8"), buffer.getvalue().rstrip(chr(10)) + chr(10))
        self.assertIn("EMERGENCY threshold", buffer.getvalue())

    def test_earliest_live_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main([], sectors=SUBSET)


if __name__ == "__main__":
    unittest.main()
