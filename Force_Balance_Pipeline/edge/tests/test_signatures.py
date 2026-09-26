"""Signatures: the doc 03 CASE mirror, computed emergency targets (property, minimality and recompute
tests against an independent reference), the margin derivation, hold channels and injection feasibility
(docs 03 and 04). Offline, seeded, stdlib."""
import itertools
import math
import statistics
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _doc03  # noqa: E402
from forcesim import constants  # noqa: E402
from forcesim import signatures as sg  # noqa: E402
from forcesim.constants import CHANNELS, VALID_RANGE  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402
from forcesim.signatures import (InjectionRefused, ORDER, SPECIALTY, check_injection, classify,  # noqa: E402
                                 hold_exact_channels, imbalance_score, target_for)
from forcesim.walk import DeviationWalk, stream  # noqa: E402

SECTORS = {s.sector_id: s for s in load_sectors()}
POP = 2e9
ROWS, FALLBACK = _doc03.signature_table()
DOC_RULES = {name: conditions for name, conditions, _ in ROWS}
_, DOC_EMERGENCY = _doc03.thresholds()


def independent_margin(k=3):
    """The margin recomputed here from constants, not from forcesim.signatures."""
    phi = constants.PHI
    e = math.sqrt((1 + phi ** 2) / (2 * (1 - phi ** 2) * 90 * constants.SCANS_PER_DAY))
    return 1 / (1 - k * e)


def reference_target(name, lower):
    """Brute-force reference, using only the parsed doc 03 table and the doc's composite formula:
    among whole-sigma points on the directional channels (others 0) that classify as `name` and score
    above `lower`, the smallest composite, then the smallest largest deviation, then loading midi."""
    movers = [c for c in CHANNELS
              if any(v == f"z_{c}" and op in (">", "<") for v, op, _ in DOC_RULES[name])]
    best = None
    for values in itertools.product(range(-10, 11), repeat=len(movers)):
        z = {c: 0 for c in CHANNELS}
        z.update(zip(movers, values))
        if _doc03.evaluate(ROWS, "unclassified", z["midi"], z["kyber"], z["dark"], POP, 3) != name:
            continue
        composite = _doc03.composite_score(z["midi"], z["kyber"], z["dark"])
        if composite > lower:
            key = (round(composite, 9), max(abs(v) for v in values), -abs(z["midi"]), z["midi"], z["kyber"], z["dark"])
            if best is None or key < best[0]:
                best = (key, z)
    return None if best is None else best[1]


class ClassifyTests(unittest.TestCase):
    def test_every_target_classifies_as_itself(self):
        for name in sg.PRODUCIBLE:
            t = target_for(name)
            with self.subTest(signature=name):
                self.assertEqual(classify(t["midi"], t["kyber"], t["dark"], POP), name)

    def test_first_match_wins_order(self):
        self.assertEqual(classify(2, -2, 3, POP), "sith_presence")   # matches rules 1 and 2: the earlier wins
        self.assertEqual(classify(2, 0, 2.0, POP), "civil_unrest")   # dark 2.0 is not > 2.0, so not dark_adept
        self.assertEqual(classify(2, 0, 2.01, POP), "dark_adept")

    def test_boundaries_are_strict(self):
        # population is None here so that civil_unrest (dark > 1.5) cannot catch these values
        self.assertEqual(classify(0, -1.01, 2.5, None), "unclassified")   # dark must be > 2.5
        self.assertEqual(classify(0, -1.01, 2.51, None), "sith_presence")
        self.assertEqual(classify(0, -1.0, 2.51, None), "unclassified")   # kyber must be < -1.0
        self.assertEqual(classify(2.0, 2.01, 0, None), "unclassified")    # midi must be > 2.0
        self.assertEqual(classify(0, 0, 1.5, POP), "unclassified")        # dark must be > 1.5
        self.assertEqual(classify(0, 0, 1.51, 1e9), "unclassified")       # population must be > 1e9
        self.assertEqual(classify(0, 0, 1.51, 1e9 + 1), "civil_unrest")

    def test_unclassified_is_reachable_and_null_behaves_like_sql(self):
        self.assertEqual(classify(0, 0, 0, POP), "unclassified")
        self.assertEqual(classify(None, None, None, None), "unclassified")
        self.assertEqual(classify(0, 0, 1.51, None), "unclassified")   # population NULL: not > 1e9
        self.assertEqual(classify(None, -2, 3, POP), "sith_presence")  # midi is not read by rule 1

    def test_veiled_presence_needs_partial_coverage(self):
        self.assertEqual(classify(0.5, 0, 2.5, None, channels_present=3), "unclassified")
        self.assertEqual(classify(0.5, None, 2.5, None, channels_present=1), "veiled_presence")
        self.assertEqual(classify(0.5, 0, 2.5, None, channels_present=2), "veiled_presence")

    def test_every_signature_in_doc_03_is_covered(self):
        self.assertEqual(set(ORDER), set(SPECIALTY))
        self.assertEqual(set(ORDER) - set(sg.PRODUCIBLE), {"veiled_presence"})
        self.assertEqual(ORDER[0], "sith_presence")


class ComputedTargetTests(unittest.TestCase):
    """Targets are computed from the doc 03 threshold and the margin, not listed."""

    LOWER = DOC_EMERGENCY * independent_margin()

    def test_targets_equal_an_independent_reference_at_the_doc_threshold(self):
        for name in sg.PRODUCIBLE:
            with self.subTest(signature=name):
                self.assertEqual(target_for(name), reference_target(name, self.LOWER))

    def test_property_in_region_clears_the_margin_and_only_movers_move(self):
        for name in sg.PRODUCIBLE:
            t = target_for(name)
            with self.subTest(signature=name):
                self.assertEqual(classify(t["midi"], t["kyber"], t["dark"], POP), name)
                self.assertGreater(imbalance_score(t["midi"], t["kyber"], t["dark"]), self.LOWER)
                for ch in CHANNELS:
                    if ch not in sg.MOVERS[name]:
                        self.assertEqual(t[ch], 0, f"{ch} is not a directional channel of {name}: it stays 0")
                for var, op, _ in DOC_RULES[name]:
                    if op in (">", "<") and var.startswith("z_"):  # a mover moves the way its condition points
                        self.assertGreater(t[var[2:]] if op == ">" else -t[var[2:]], 0)

    def test_minimality_no_smaller_point_clears_the_threshold(self):
        for name in sg.PRODUCIBLE:
            t = target_for(name)
            best = imbalance_score(t["midi"], t["kyber"], t["dark"])
            smaller = []
            for values in itertools.product(range(-10, 11), repeat=len(sg.MOVERS[name])):
                z = {c: 0 for c in CHANNELS}
                z.update(zip(sg.MOVERS[name], values))
                if classify(z["midi"], z["kyber"], z["dark"], POP) == name:
                    c = imbalance_score(z["midi"], z["kyber"], z["dark"])
                    if self.LOWER < c < best - 1e-9:
                        smaller.append((z, c))
            with self.subTest(signature=name):
                self.assertEqual(smaller, [])
            # local view: bringing any moving channel one whole sigma closer to zero loses the region
            # or the threshold
            for ch in sg.MOVERS[name]:
                z = dict(t)
                z[ch] -= 1 if t[ch] > 0 else -1
                ok = (classify(z["midi"], z["kyber"], z["dark"], POP) == name
                      and imbalance_score(z["midi"], z["kyber"], z["dark"]) > self.LOWER)
                with self.subTest(signature=name, channel=ch):
                    self.assertFalse(ok)

    def test_recomputes_when_the_threshold_changes(self):
        previous = {}
        for threshold in (3.0, 4.0, 4.5, 5.5, 7.0):
            for name in sg.PRODUCIBLE:
                lower = threshold * independent_margin()
                t = target_for(name, threshold=threshold)
                with self.subTest(signature=name, threshold=threshold):
                    self.assertEqual(t, reference_target(name, lower))
                    c = imbalance_score(t["midi"], t["kyber"], t["dark"])
                    self.assertGreater(c, lower)
                    self.assertGreaterEqual(c, previous.get(name, 0.0) - 1e-9)  # never lower with a higher threshold
                    previous[name] = c
        targets_low = {n: target_for(n, threshold=3.0) for n in sg.PRODUCIBLE}
        targets_high = {n: target_for(n, threshold=7.0) for n in sg.PRODUCIBLE}
        self.assertNotEqual(targets_low, targets_high)

    def test_the_module_threshold_is_what_the_default_uses(self):
        """Tuning the doc 03 threshold (and the constant that mirrors it) recomputes every target."""
        with mock.patch.object(sg, "EMERGENCY_THRESHOLD", 6.0):
            for name in sg.PRODUCIBLE:
                self.assertEqual(target_for(name), reference_target(name, 6.0 * independent_margin()))
        self.assertEqual(target_for("sith_presence"), reference_target("sith_presence", self.LOWER))

    def test_no_hard_coded_target_table(self):
        self.assertFalse(hasattr(sg, "TARGETS"))

    def test_golden_targets_at_the_current_doc_thresholds(self):
        """Documentation of today's values (emergency 4.5, k = 3, M = 1.0603). If doc 03's thresholds
        are tuned this table changes by design; the parity tests fail first if code and doc disagree."""
        golden = {"sith_presence": (0, -3, 3), "dark_adept": (3, 0, 3), "nexus_awakening": (4, 3, 0),
                  "force_drain": (-4, -3, 0), "kyber_cache": (0, 5, 0), "civil_unrest": (0, 0, 4)}
        composites = {"sith_presence": 5.196, "dark_adept": 5.196, "nexus_awakening": 5.0,
                      "force_drain": 5.0, "kyber_cache": 5.0, "civil_unrest": 5.657}
        for name, (midi, kyber, dark) in golden.items():
            t = target_for(name)
            with self.subTest(signature=name):
                self.assertEqual((t["midi"], t["kyber"], t["dark"]), (midi, kyber, dark))
                self.assertAlmostEqual(imbalance_score(midi, kyber, dark), composites[name], places=3)

    def test_every_target_keeps_room_for_the_sampling_error(self):
        """Even against a window SD estimated 3 standard errors high, the score still clears 4.5."""
        e = sg.window_sd_se()
        for name in sg.PRODUCIBLE:
            t = target_for(name)
            self.assertGreater(imbalance_score(t["midi"], t["kyber"], t["dark"]) * (1 - 3 * e), DOC_EMERGENCY)

    def test_no_point_raises(self):
        with self.assertRaisesRegex(InjectionRefused, "no whole-sigma point"):
            target_for("civil_unrest", threshold=1000.0)
        with self.assertRaisesRegex(InjectionRefused, "STEALTH"):
            target_for("veiled_presence")
        with self.assertRaisesRegex(InjectionRefused, "unknown signature"):
            target_for("made_up")


class MarginTests(unittest.TestCase):
    def test_margin_derivation(self):
        e = sg.window_sd_se()
        self.assertAlmostEqual(e, 0.0190, places=3)
        self.assertAlmostEqual(sg.margin_factor(), 1.0603, places=3)
        self.assertAlmostEqual(sg.margin_factor(), 1 / (1 - 3 * e), places=12)
        self.assertAlmostEqual(sg.margin_factor(k=2), 1.0394, places=3)
        self.assertAlmostEqual(sg.margin_factor(k=0), 1.0, places=12)
        self.assertEqual(sg.MARGIN_K, 3)
        with self.assertRaises(ValueError):
            sg.margin_factor(k=60)

    def test_analytic_error_matches_a_simulation_of_window_sds(self):
        """The 1.9% is checked against 200 simulated 90-day windows (n = 8,640), not just derived."""
        sigma = 4.0
        ratios = []
        for k in range(200):
            walk = DeviationWalk(sigma, stream(2000 + k, "margin"))
            ratios.append(statistics.pstdev([walk.step() for _ in range(sg.WINDOW_SCANS)]) / sigma)
        measured = statistics.pstdev(ratios)
        # the SE of an SD estimated from 200 values is about 5% of itself; 20% is about 4 SE
        self.assertLess(abs(measured / sg.window_sd_se() - 1), 0.20, (measured, sg.window_sd_se()))
        self.assertLess(abs(statistics.fmean(ratios) - 1), 0.01)

    def test_the_margin_is_on_injection_intent_not_on_the_region(self):
        """The margin scales the SCORE the injection aims for; it never widens the region. The region
        test itself (classify) has no margin parameter, and every target lies inside its region."""
        import inspect
        self.assertEqual(list(inspect.signature(classify).parameters),
                         ["z_midi", "z_kyber", "z_dark", "population", "channels_present"])


class HoldChannelTests(unittest.TestCase):
    # The region of a signature is its own rule AND NOT any earlier rule, so a guard channel counts,
    # and it is computed at the target: civil_unrest at dark +4 needs midi pinned (dark_adept guard).
    EXPECTED_EXACT = {
        "sith_presence":   {"dark", "kyber"},           # midi is not read by rule 1, which is first
        "dark_adept":      {"dark", "midi", "kyber"},   # kyber >= -1 guards against sith_presence
        "nexus_awakening": {"midi", "kyber", "dark"},
        "force_drain":     {"midi", "kyber", "dark"},   # dark <= 2.5 guards against sith_presence
        "kyber_cache":     {"midi", "kyber", "dark"},
        "civil_unrest":    {"midi", "kyber", "dark"},   # midi <= 1.5 guards against dark_adept once dark > 2
    }

    def test_exact_channels_match_the_computed_regions(self):
        for name, expected in self.EXPECTED_EXACT.items():
            with self.subTest(signature=name):
                self.assertEqual(set(hold_exact_channels(name)), expected)

    def test_free_channels_really_are_free(self):
        for name, exact in self.EXPECTED_EXACT.items():
            base = target_for(name)
            for ch in set(CHANNELS) - exact:
                for tenth in range(-100, 101):
                    z = dict(base)
                    z[ch] = tenth / 10.0
                    with self.subTest(signature=name, channel=ch, z=z[ch]):
                        self.assertEqual(classify(z["midi"], z["kyber"], z["dark"], POP), name)

    def test_exact_channels_really_are_needed(self):
        for name, exact in self.EXPECTED_EXACT.items():
            base = target_for(name)
            for ch in exact:
                broken = False
                for tenth in range(-100, 101):
                    z = dict(base)
                    z[ch] = tenth / 10.0
                    if classify(z["midi"], z["kyber"], z["dark"], POP) != name:
                        broken = True
                        break
                self.assertTrue(broken, f"{name}: {ch} never changes the classification")


class CompositeTests(unittest.TestCase):
    def test_composite_matches_doc_03(self):
        self.assertAlmostEqual(imbalance_score(0, 0, 0), 0.0)
        self.assertAlmostEqual(imbalance_score(3, 0, 0), 3.0)
        self.assertAlmostEqual(imbalance_score(0, 0, 3), 3.0 * 2 ** 0.5)  # dark side is double-weighted
        # STEALTH: one channel present scales by sqrt(3 / 1); missing z counts as 0 (COALESCE)
        self.assertAlmostEqual(imbalance_score(None, None, 2, channels_present=1), (2 * 4) ** 0.5 * 3 ** 0.5)
        self.assertEqual((sg.ANOMALY_THRESHOLD, sg.EMERGENCY_THRESHOLD), (3.0, 4.5))

    def test_every_injection_target_is_emergency_level(self):
        """The reason the targets are computed: at the whole-sigma point just inside each region only
        sith_presence and dark_adept cleared 4.5. Now every producible signature does, with margin."""
        for name in sg.PRODUCIBLE:
            t = target_for(name)
            with self.subTest(signature=name):
                self.assertGreater(imbalance_score(t["midi"], t["kyber"], t["dark"]),
                                   sg.EMERGENCY_THRESHOLD * sg.margin_factor())

    def test_ambient_dark_spikes_are_emergency_level(self):
        for depth in (4.0, 7.0):
            self.assertGreater(imbalance_score(0, 0, depth), sg.EMERGENCY_THRESHOLD)


class InjectionCheckTests(unittest.TestCase):
    def test_backfill_texture_defaults_are_feasible(self):
        for sector, name in (("tatooine", "sith_presence"), ("dantooine", "nexus_awakening"),
                             ("kamino", "force_drain"), ("coruscant", "civil_unrest")):
            with self.subTest(sector=sector, signature=name):
                check_injection(SECTORS[sector], name)

    def test_utapau_cannot_host_kyber_cache(self):
        s = SECTORS["utapau"]
        baseline, sigma = s.channel("kyber")
        self.assertAlmostEqual((VALID_RANGE["kyber"][1] - baseline) / sigma, 2.083, places=2)
        with self.assertRaisesRegex(InjectionRefused, r"kyber target 128\.00"):  # 80 + 5 x 9.6
            check_injection(s, "kyber_cache")

    def test_civil_unrest_population_rule(self):
        self.assertEqual(sg.REQUIRES_POPULATION["civil_unrest"], 1e9)
        with self.assertRaisesRegex(InjectionRefused, "no population value"):
            check_injection(SECTORS["hoth"], "civil_unrest")  # SWAPI population unknown: blank
        with self.assertRaisesRegex(InjectionRefused, "population"):
            check_injection(SECTORS["tatooine"], "civil_unrest")
        check_injection(SECTORS["coruscant"], "civil_unrest")

    def test_feasible_host_counts_are_pinned(self):
        """Hosts per signature out of the 60 sectors at today's targets. If the thresholds are tuned this
        changes by design; a signature must always keep at least one host."""
        expected = {"sith_presence": 58, "dark_adept": 58, "nexus_awakening": 59, "force_drain": 60,
                    "kyber_cache": 59, "civil_unrest": 19}
        for name, want in expected.items():
            hosts = []
            for s in SECTORS.values():
                try:
                    check_injection(s, name)
                    hosts.append(s.sector_id)
                except InjectionRefused:
                    pass
            with self.subTest(signature=name):
                self.assertEqual(len(hosts), want)
                self.assertGreaterEqual(len(hosts), 1)


if __name__ == "__main__":
    unittest.main()
