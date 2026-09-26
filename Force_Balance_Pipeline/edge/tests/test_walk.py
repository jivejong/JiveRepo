"""Walk calibration, clamping, ambient spikes and injected holds (doc 04). Offline, seeded, stdlib."""
import math
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forcesim import constants  # noqa: E402
from forcesim.sectors import Sector, load_sectors  # noqa: E402
from forcesim.walk import (DeviationWalk, Drift, Episode, PlanetProbe, SpikeScheduler,  # noqa: E402
                           clamp, normal, stream)
from forcesim.signatures import InjectionRefused, target_for  # noqa: E402

SECTORS = {s.sector_id: s for s in load_sectors()}


def series(sigma, n, seed, step_factor=constants.STEP_FACTOR):
    walk = DeviationWalk(sigma, stream(seed, "walk"), step_factor)
    return [walk.step() for _ in range(n)]


def sd(values):
    return statistics.pstdev(values)


def lag1(values):
    m = statistics.fmean(values)
    num = sum((a - m) * (b - m) for a, b in zip(values, values[1:]))
    return num / sum((a - m) ** 2 for a in values)


def flat_sector(sid="flat", spike=0.0, population=2e9, baseline=(5000.0, 50.0, 30.0), sigma=(100.0, 4.0, 2.0)):
    return Sector(sid, sid, population, baseline[0], sigma[0], baseline[1], sigma[1], baseline[2], sigma[2],
                  spike, False)


class CalibrationTests(unittest.TestCase):
    # (sector, channel) pairs from the frozen seed: both ends of the sigma bands.
    PAIRS = [("coruscant", "midi"), ("tatooine", "kyber"), ("mustafar", "dark"), ("utapau", "kyber"),
             ("alderaan", "dark")]

    def test_constants(self):
        self.assertAlmostEqual(constants.STEP_FACTOR, 0.5268, places=4)
        self.assertAlmostEqual(constants.STEP_FACTOR, math.sqrt(1 - constants.PHI ** 2), places=12)
        self.assertAlmostEqual(1 / math.sqrt(1 - constants.PHI ** 2), 1.8983, places=4)  # the old form

    def test_stationary_sd_autocorrelation_and_mean(self):
        """1. Unclamped 200,000-step series: SD within 2% of sigma, lag-1 autocorrelation within 0.01 of
        0.85, mean within 0.03 sigma. For a 200,000-step AR(1) series the SE of the SD is about 0.39%
        (sqrt((1 + phi^2) / (2 (1 - phi^2) n))), so 2% is about 5 SE; the SE of the mean is about
        0.008 sigma, so 0.03 sigma is about 3.8 SE. (The plan said 0.02, only 2.5 SE: the measured
        Utapau mean was -0.0195, so that bound would have passed by luck.)"""
        for k, (sid, ch) in enumerate(self.PAIRS):
            sigma = SECTORS[sid].channel(ch)[1]
            xs = series(sigma, 200_000, seed=100 + k)
            with self.subTest(sector=sid, channel=ch):
                self.assertLess(abs(sd(xs) / sigma - 1), 0.02, sd(xs) / sigma)
                self.assertLess(abs(lag1(xs) - constants.PHI), 0.01, lag1(xs))
                self.assertLess(abs(statistics.fmean(xs)) / sigma, 0.03)

    def test_stationary_start(self):
        """2. 20,000 independent series, first sample only: SD within 2% of sigma."""
        sigma = 10.0
        firsts = []
        for k in range(20_000):
            firsts.append(DeviationWalk(sigma, stream(7, "first", k)).step())
        self.assertLess(abs(sd(firsts) / sigma - 1), 0.02, sd(firsts) / sigma)
        self.assertLess(abs(statistics.fmean(firsts)) / sigma, 0.03)

    def test_ninety_day_windows(self):
        """3. 200 independent 90-day (8,640-step) series: the mean of their sample SDs within 1% of
        sigma, and nearly all within 8%. The SE of one window's SD is sqrt((1 + phi^2) / (2 (1 - phi^2) n))
        = 1.9% at n = 8,640, so 8% is about 4.2 SE. (The plan said 2.7%, which used the effective sample
        size for a mean, n (1 - phi) / (1 + phi); for a variance it is n (1 - phi^2) / (1 + phi^2).)"""
        sigma = 4.0
        ratios = [sd(series(sigma, 8640, seed=1000 + k)) / sigma for k in range(200)]
        self.assertLess(abs(statistics.fmean(ratios) - 1), 0.01, statistics.fmean(ratios))
        inside = sum(abs(r - 1) < 0.08 for r in ratios) / len(ratios)
        self.assertGreaterEqual(inside, 0.97, inside)

    def test_regression_guard_uncalibrated_form(self):
        """4. Step noise equal to sigma (the doc 04 original) gives 1.90 sigma; this test would fail
        if the calibration were removed."""
        sigma = 5.0
        ratio = sd(series(sigma, 200_000, seed=9, step_factor=1.0)) / sigma
        self.assertAlmostEqual(ratio, 1.898, delta=0.03)
        calibrated = sd(series(sigma, 200_000, seed=9)) / sigma
        self.assertLess(abs(calibrated - 1), 0.02)

    def test_clamped_worst_case(self):
        """5. Utapau kyber (80, sigma 9.6) with the range clamp on: SD within 3% of sigma. About 1.9%
        of samples clamp at 100, so the SD falls to about 0.98 sigma."""
        baseline, sigma = SECTORS["utapau"].channel("kyber")
        xs = [clamp("kyber", baseline + x) for x in series(sigma, 200_000, seed=55)]
        self.assertLess(abs(sd(xs) / sigma - 1), 0.03, sd(xs) / sigma)
        self.assertLess(sd(xs) / sigma, 1.0)  # clamping can only shrink it
        clamped = sum(v == 100.0 for v in xs) / len(xs)
        self.assertAlmostEqual(clamped, 0.0188, delta=0.004)

    def test_normal_is_stable_and_standard(self):
        rng = stream(3, "n")
        xs = [normal(rng) for _ in range(100_000)]
        self.assertAlmostEqual(statistics.fmean(xs), 0.0, delta=0.02)
        self.assertAlmostEqual(sd(xs), 1.0, delta=0.02)
        # A fixed seed gives fixed draws (reproducibility depends on it).
        self.assertEqual(normal(stream(1, "x")), normal(stream(1, "x")))
        self.assertNotEqual(normal(stream(1, "x")), normal(stream(2, "x")))

    def test_streams_are_independent_of_generation_order(self):
        a = PlanetProbe(SECTORS["tatooine"], 42)
        b = PlanetProbe(SECTORS["naboo"], 42)
        first = [a.step(i) for i in range(50)]
        [b.step(i) for i in range(50)]
        a2 = PlanetProbe(SECTORS["tatooine"], 42)  # built after naboo ran: identical to the first
        self.assertEqual(first, [a2.step(i) for i in range(50)])


class SpikeTests(unittest.TestCase):
    def test_idle_start_rate_is_p_over_96(self):
        p_day = 0.05
        sched = SpikeScheduler(p_day, stream(1, "roll"), stream(1, "params"))
        n = 2_000_000
        starts = sum(sched.roll(i, blocked=False) is not None for i in range(n))
        expected = n * p_day / 96
        self.assertAlmostEqual(starts / expected, 1.0, delta=0.10, msg=f"{starts} vs {expected:.0f}")

    def test_zero_probability_never_starts(self):
        sched = SpikeScheduler(0.0, stream(2, "roll"), stream(2, "params"))
        self.assertTrue(all(sched.roll(i, blocked=False) is None for i in range(100_000)))

    def test_episode_parameters_follow_doc_04(self):
        sched = SpikeScheduler(96.0, stream(4, "roll"), stream(4, "params"))  # p_scan = 1: every roll hits
        eps = [sched.roll(i, blocked=False) for i in range(5000)]
        self.assertTrue(all(e is not None for e in eps))
        self.assertEqual({e.ramp for e in eps}, {2, 3, 4})
        self.assertEqual({e.hold for e in eps}, {1, 2})
        self.assertEqual({e.decay for e in eps}, {3, 4, 5})
        depths = [e.depth["dark"] for e in eps]
        self.assertGreaterEqual(min(depths), 4.0)
        self.assertLess(max(depths), 7.0)
        self.assertTrue(all(set(e.depth) == {"dark"} and not e.injected for e in eps))

    def test_blocked_scan_never_starts_but_still_consumes_its_roll(self):
        a = SpikeScheduler(96.0, stream(5, "roll"), stream(5, "params"))
        self.assertIsNone(a.roll(0, blocked=True))
        b = SpikeScheduler(96.0, stream(5, "roll"), stream(5, "params"))
        b.roll(0, blocked=False)
        # Both consumed exactly one roll draw, so their roll streams are aligned afterwards.
        self.assertEqual(a.roll_rng.random(), b.roll_rng.random())

    def test_no_roll_while_in_an_episode_or_an_injected_event_is_active(self):
        # dark_spike_probability 96 makes every eligible scan start an episode, so the only scans
        # without one are the blocked ones: this shows exactly what blocks.
        sector = flat_sector(spike=96.0, population=2e9)
        probe = PlanetProbe(sector, 8)
        injected = probe.inject("civil_unrest", start=40, ramp=2, hold=3, decay=3)
        for i in range(120):
            probe.step(i)
        ambient = sorted((e for e in probe.episodes if not e.injected), key=lambda e: e.start)
        self.assertGreater(len(ambient), 5)
        # never starts inside another episode, ambient or injected
        for e in probe.episodes:
            others = [o for o in probe.episodes if o is not e]
            self.assertFalse(any(o.covers(e.start) and o.start != e.start for o in others),
                             f"episode at {e.start} started inside another")
        # never starts while the injected event is active
        for e in ambient:
            self.assertFalse(injected.covers(e.start), f"ambient episode started at {e.start} inside the injection")
        # and the scan right after each blocking span is free to start one again
        self.assertTrue(any(e.start == injected.end for e in ambient))

    def test_ambient_episode_running_into_an_injected_event_is_cut(self):
        """Live control-topic injection: an ambient episode still running at the injection is cut."""
        sector = flat_sector(spike=96.0)
        probe = PlanetProbe(sector, 12)
        for i in range(3):
            probe.step(i)
        running = [e for e in probe.episodes if not e.injected and e.covers(3)]
        self.assertTrue(running, "expected an ambient episode still running at scan 3")
        probe.inject("civil_unrest", start=3, ramp=2, hold=2, decay=2)
        self.assertTrue(all(e.end <= 3 for e in probe.episodes if not e.injected and e.start < 3))
        for i in range(3, 12):
            probe.step(i)
        self.assertFalse(any(not e.injected and e.start in range(3, 3 + 6) for e in probe.episodes))

    def test_ambient_episode_scheduled_ahead_of_an_injection_is_cut(self):
        """Backfill: an injected event is scheduled first; an ambient episode that would run into it
        is cut where the injected event starts."""
        sector = flat_sector(spike=96.0)
        probe = PlanetProbe(sector, 13)
        probe.inject("civil_unrest", start=50, ramp=2, hold=2, decay=2)
        for i in range(60):
            probe.step(i)
        for e in probe.episodes:
            if not e.injected:
                self.assertFalse(e.covers(50) or e.covers(51), f"ambient episode {e.start}-{e.end} overlaps the injection")


class EpisodeShapeTests(unittest.TestCase):
    def test_ramp_hold_decay_fractions(self):
        e = Episode(start=10, ramp=3, hold=2, decay=4, depth={"dark": 5.0})
        self.assertEqual([e.phase(i) for i in range(9, 21)],
                         [None, "ramp", "ramp", "ramp", "hold", "hold", "decay", "decay", "decay", "decay", None, None])
        self.assertEqual([round(e.fraction(i), 4) for i in range(10, 20)],
                         [0.3333, 0.6667, 1.0, 1.0, 1.0, 0.8, 0.6, 0.4, 0.2, 0.0])
        self.assertEqual(e.end, 19)

    def test_cut_ends_the_episode_early(self):
        e = Episode(start=10, ramp=3, hold=2, decay=4, cut=13)
        self.assertEqual(e.end, 13)
        self.assertFalse(e.covers(13))

    def test_phases_must_be_at_least_one_scan(self):
        with self.assertRaises(ValueError):
            Episode(start=0, ramp=0, hold=1, decay=1)


class InjectionTests(unittest.TestCase):
    def test_hold_is_exact_on_region_channels_and_noisy_elsewhere(self):
        sector = SECTORS["tatooine"]  # sith_presence: dark and kyber define the region; midi is not read
        probe = PlanetProbe(sector, 21)
        probe.inject("sith_presence", start=10, ramp=2, hold=3, decay=3)
        vals = [probe.step(i) for i in range(20)]
        base = {ch: sector.channel(ch) for ch in constants.CHANNELS}
        target = target_for("sith_presence")
        self.assertEqual(target, {"midi": 0, "kyber": -3, "dark": 4})       # emergency 5.75 (doc 03), M = 1.0603
        episode = probe.episodes[0]
        self.assertEqual(episode.exact, frozenset({"kyber", "dark"}))
        for i in (12, 13, 14):  # the hold scans
            self.assertEqual(vals[i]["dark"], base["dark"][0] + target["dark"] * base["dark"][1])
            self.assertEqual(vals[i]["kyber"], base["kyber"][0] + target["kyber"] * base["kyber"][1])
        midi_hold = {vals[i]["midi"] for i in (12, 13, 14)}
        self.assertEqual(len(midi_hold), 3, "midi is not in the region, so it keeps its ambient noise")
        for i in (10, 11, 15, 16, 17):  # ramp and decay keep the noise
            frac = episode.fraction(i)
            self.assertNotEqual(vals[i]["dark"], base["dark"][0] + target["dark"] * frac * base["dark"][1])

    def test_guard_channels_are_held_exactly_too(self):
        """civil_unrest at dark +5: midi becomes a guard (dark_adept needs midi > 1.5), so all three hold."""
        sector = SECTORS["coruscant"]
        probe = PlanetProbe(sector, 22)
        probe.inject("civil_unrest", start=6, ramp=2, hold=2, decay=2)
        vals = [probe.step(i) for i in range(14)]
        self.assertEqual(probe.episodes[0].exact, frozenset({"midi", "kyber", "dark"}))
        for i in (8, 9):
            self.assertEqual(vals[i]["midi"], sector.midi_baseline)
            self.assertEqual(vals[i]["kyber"], sector.kyber_baseline)
            self.assertEqual(vals[i]["dark"], sector.dark_baseline + 5 * sector.dark_sigma)

    def test_ambient_walk_state_is_unaffected_by_holds(self):
        """The ambient AR state keeps advancing during a hold, so it stays stationary afterwards."""
        sector = flat_sector(spike=0.0)
        a, b = PlanetProbe(sector, 33), PlanetProbe(sector, 33)
        b.inject("kyber_cache", start=5, ramp=2, hold=2, decay=2)
        for i in range(30):
            va, vb = a.step(i), b.step(i)
            if i >= 5 + 6:  # after the event: the same ambient noise (offset is zero again)
                self.assertEqual(va, vb)

    def test_refusals(self):
        utapau = PlanetProbe(SECTORS["utapau"], 1)
        with self.assertRaisesRegex(InjectionRefused, "outside the valid range"):
            utapau.inject("kyber_cache", 5, 2, 2, 2)  # max reachable z is 2.08 < 3
        with self.assertRaisesRegex(InjectionRefused, "population"):
            PlanetProbe(SECTORS["tatooine"], 1).inject("civil_unrest", 5, 2, 2, 2)
        with self.assertRaisesRegex(InjectionRefused, "STEALTH"):
            PlanetProbe(SECTORS["coruscant"], 1).inject("veiled_presence", 5, 2, 2, 2)
        with self.assertRaisesRegex(InjectionRefused, "unknown signature"):
            PlanetProbe(SECTORS["coruscant"], 1).inject("made_up", 5, 2, 2, 2)
        with self.assertRaisesRegex(InjectionRefused, "at least 2 scans"):
            PlanetProbe(SECTORS["coruscant"], 1).inject("civil_unrest", 5, 2, 1, 2)  # doc 03: sustained >= 2 scans
        p = PlanetProbe(SECTORS["coruscant"], 1)
        p.inject("civil_unrest", 10, 2, 2, 2)
        with self.assertRaisesRegex(InjectionRefused, "overlaps"):
            p.inject("civil_unrest", 12, 2, 2, 2)
        p.step(0)
        with self.assertRaisesRegex(InjectionRefused, "already been generated"):
            p.inject("civil_unrest", 0, 2, 2, 2)

    def test_scans_must_increase(self):
        p = PlanetProbe(SECTORS["coruscant"], 1)
        p.step(5)
        with self.assertRaises(ValueError):
            p.step(5)

    def test_drift_is_linear_and_additive(self):
        sector = flat_sector(spike=0.0)
        d = Drift("midi", 2.0, start=0, end=100)
        self.assertEqual([d.offset(i) for i in (-1, 0, 50, 100, 200)], [0.0, 0.0, 1.0, 2.0, 2.0])
        plain, drifting = PlanetProbe(sector, 4), PlanetProbe(sector, 4, [d])
        diffs = [drifting.step(i)["midi"] - plain.step(i)["midi"] for i in range(101)]
        self.assertAlmostEqual(diffs[50], 1.0 * sector.midi_sigma, places=9)
        self.assertAlmostEqual(diffs[100], 2.0 * sector.midi_sigma, places=9)


if __name__ == "__main__":
    unittest.main()
