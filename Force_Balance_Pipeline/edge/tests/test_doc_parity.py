"""Parity between forcesim.signatures and docs/03-data-model.md. The document is the source of truth:
if doc 03 is edited (a rule, a threshold, a weight) and the code is not, these fail first."""
import itertools
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _doc03  # noqa: E402
from forcesim import signatures as sg  # noqa: E402
from forcesim.constants import SCANS_PER_DAY  # noqa: E402


class SignatureTableParity(unittest.TestCase):
    def setUp(self):
        self.rows, self.fallback = _doc03.signature_table()

    def test_rules_equal_the_doc_table_in_order(self):
        self.assertEqual([(name, conditions) for name, conditions, _ in self.rows], list(sg.RULES))

    def test_fallback_is_unclassified_and_reachable(self):
        self.assertEqual(self.fallback, ("unclassified", "any"))
        self.assertEqual(sg.classify(0, 0, 0, 2e9), "unclassified")

    def test_specialties_equal_the_doc(self):
        self.assertEqual({name: specialty for name, _, specialty in self.rows}, sg.SPECIALTY)

    def test_classify_agrees_with_an_independent_evaluation_of_the_doc_table(self):
        grid = [x / 2.0 for x in range(-8, 9)]  # -4 to +4 sigma in 0.5 steps
        checked = 0
        for z_midi, z_kyber, z_dark in itertools.product(grid, repeat=3):
            for population in (None, 5e8, 1e9, 2e9):
                for present in (1, 2, 3):
                    expected = _doc03.evaluate(self.rows, "unclassified", z_midi, z_kyber, z_dark, population, present)
                    self.assertEqual(sg.classify(z_midi, z_kyber, z_dark, population, present), expected,
                                     (z_midi, z_kyber, z_dark, population, present))
                    checked += 1
        self.assertGreater(checked, 50_000)

    def test_derived_facts_follow_the_doc(self):
        self.assertEqual(sg.REQUIRES_POPULATION, {"civil_unrest": 1e9})
        self.assertEqual(sg.PRODUCIBLE, tuple(n for n in sg.ORDER if n != "veiled_presence"))
        # movers are exactly the z-variables with a directional condition in the DOC's rule
        for name, conditions, _ in self.rows:
            from_doc = tuple(c for c in ("midi", "kyber", "dark")
                             if any(v == f"z_{c}" and op in (">", "<") for v, op, _ in conditions))
            self.assertEqual(sg.MOVERS[name], from_doc, name)


class ThresholdParity(unittest.TestCase):
    def test_thresholds_equal_the_doc(self):
        self.assertEqual(_doc03.thresholds(), (sg.ANOMALY_THRESHOLD, sg.EMERGENCY_THRESHOLD))

    def test_every_firing_rule_uses_the_emergency_threshold(self):
        found = _doc03.firing_thresholds()
        self.assertGreaterEqual(len(found), 2, "expected the probe-sourced and report-sourced rules")
        self.assertEqual(set(found), {sg.EMERGENCY_THRESHOLD})

    def test_sustained_scans_equal_the_doc(self):
        self.assertEqual(_doc03.sustained_scans(), sg.SUSTAINED_SCANS)


class CompositeParity(unittest.TestCase):
    def test_weights_and_scaling_equal_the_doc(self):
        weights, full = _doc03.composite()
        self.assertEqual(weights, sg.COMPOSITE_WEIGHTS)
        self.assertEqual(full, sg.FULL_CHANNELS)

    def test_imbalance_score_agrees_with_the_doc_formula(self):
        grid = [-5.0, -2.5, -1.0, 0.0, 0.7, 2.0, 4.5]
        for z in itertools.product(grid, repeat=3):
            for present in (1, 2, 3):
                self.assertAlmostEqual(sg.imbalance_score(*z, channels_present=present),
                                       _doc03.composite_score(*z, channels_present=present), places=12)
        self.assertAlmostEqual(sg.imbalance_score(None, None, 2, channels_present=1),
                               _doc03.composite_score(None, None, 2, channels_present=1), places=12)

    def test_the_baseline_window_is_ninety_days(self):
        self.assertIn("Rolling 90-day", _doc03.text())
        self.assertEqual(sg.WINDOW_SCANS, 90 * SCANS_PER_DAY)


if __name__ == "__main__":
    unittest.main()
