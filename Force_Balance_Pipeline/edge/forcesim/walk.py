"""The reading generator: a calibrated mean-reverting walk per planet per channel, ambient dark spike
episodes, drift, and injected signature events (doc 04).

Only random.Random.random() is used, with Box-Muller written here: random.gauss and friends are not
guaranteed stable across Python versions, and a reproducible backfill needs stable draws. Every random
stream is keyed by (seed, planet, purpose), so a planet's series does not depend on the order planets
are generated in.
"""
import math
import random
from dataclasses import dataclass, field, replace
from typing import Dict, FrozenSet, List, Optional

from .constants import CHANNELS, PHI, SCANS_PER_DAY, STEP_FACTOR, VALID_RANGE
from .signatures import SUSTAINED_SCANS, InjectionRefused, check_injection, hold_exact_channels, target_for


def stream(seed, *parts):
    """A named, independent random stream. String seeding is hash-based and stable across versions."""
    return random.Random(":".join([str(seed), *map(str, parts)]))


def normal(rng, mean=0.0, sd=1.0):
    """Box-Muller. Two uniform draws per call, so the position of a stream is easy to reason about."""
    u1 = 1.0 - rng.random()  # (0, 1], never 0
    u2 = rng.random()
    return mean + sd * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


class DeviationWalk:
    """The AR(1) deviation from baseline: x_t = PHI * x_{t-1} + normal(0, step_sd).

    step_sd = sigma * STEP_FACTOR makes the stationary SD equal sigma. The state starts from a
    stationary draw, normal(0, sigma), so there is no burn-in. `step_factor` exists so a test can
    show the uncalibrated form (1.0) gives 1.9 sigma; production code never passes it.
    """

    def __init__(self, sigma, rng, step_factor=STEP_FACTOR):
        self.sigma = sigma
        self.rng = rng
        self.step_sd = sigma * step_factor
        self.x = normal(rng, 0.0, sigma)

    def step(self):
        self.x = PHI * self.x + normal(self.rng, 0.0, self.step_sd)
        return self.x


def clamp(channel, value):
    lo, hi = VALID_RANGE[channel]
    return lo if value < lo else hi if value > hi else value


@dataclass(frozen=True)
class Drift:
    """A linear drift of one channel's mean, `total_sigma` sigmas over scans [start, end]."""
    channel: str
    total_sigma: float
    start: int
    end: int

    def offset(self, i):
        if i <= self.start:
            return 0.0
        if i >= self.end:
            return self.total_sigma
        return self.total_sigma * (i - self.start) / (self.end - self.start)


@dataclass(frozen=True)
class Episode:
    """A ramp-hold-decay excursion (doc 04). `depth` is in sigma units per channel. `cut` ends an
    ambient episode early when an injected event starts on the planet."""
    start: int
    ramp: int
    hold: int
    decay: int
    depth: Dict[str, float] = field(default_factory=dict)
    injected: bool = False
    signature: Optional[str] = None
    exact: FrozenSet[str] = frozenset()  # channels held exactly at target during the hold phase
    cut: Optional[int] = None

    def __post_init__(self):
        if self.ramp < 1 or self.hold < 1 or self.decay < 1:
            raise ValueError("ramp, hold and decay must each be at least 1 scan")

    @property
    def end(self):  # exclusive
        natural = self.start + self.ramp + self.hold + self.decay
        return natural if self.cut is None else min(natural, self.cut)

    def covers(self, i):
        return self.start <= i < self.end

    def phase(self, i):
        if not self.covers(i):
            return None
        k = i - self.start
        if k < self.ramp:
            return "ramp"
        return "hold" if k < self.ramp + self.hold else "decay"

    def fraction(self, i):
        """How far toward the depth the episode is at scan i: ramps to 1, holds at 1, decays to 0."""
        phase = self.phase(i)
        k = i - self.start
        if phase == "ramp":
            return (k + 1) / self.ramp
        if phase == "hold":
            return 1.0
        if phase == "decay":
            return 1.0 - (k - self.ramp - self.hold + 1) / (self.decay + 1)
        return 0.0


class SpikeScheduler:
    """Ambient dark spike episodes. `dark_spike_probability` is read as a per-day rate (doc 04), so a
    scan starts an episode with probability p / 96. One roll is drawn every scan whether or not it is
    used, so the roll stream does not depend on what blocked earlier scans."""

    def __init__(self, probability_per_day, roll_rng, param_rng):
        self.p_scan = probability_per_day / SCANS_PER_DAY
        self.roll_rng = roll_rng
        self.param_rng = param_rng

    def roll(self, i, blocked):
        draw = self.roll_rng.random()
        if blocked or draw >= self.p_scan:
            return None
        p = self.param_rng.random
        ramp = 2 + int(p() * 3)     # 2-4 scans (doc 04)
        hold = 1 + int(p() * 2)     # 1-2
        decay = 3 + int(p() * 3)    # 3-5
        depth = 4.0 + 3.0 * p()     # baseline + (4 to 7) sigma
        return Episode(i, ramp, hold, decay, {"dark": depth})


class PlanetProbe:
    """One planet's three channels. step(i) must be called with strictly increasing scan indices."""

    def __init__(self, sector, seed, drifts=()):
        self.sector = sector
        self.walks = {ch: DeviationWalk(sector.channel(ch)[1], stream(seed, sector.sector_id, ch))
                      for ch in CHANNELS}
        self.spikes = SpikeScheduler(sector.dark_spike_probability,
                                     stream(seed, sector.sector_id, "spike-roll"),
                                     stream(seed, sector.sector_id, "spike-params"))
        self.drifts = list(drifts)
        self.episodes: List[Episode] = []
        self._last = -1

    def inject(self, signature, start, ramp, hold, decay):
        """Schedule an injected emergency-level signature event (the target is computed from the doc 03
        threshold, see signatures.target_for). Refuses one the planet cannot support, one that holds
        fewer than SUSTAINED_SCANS scans, or one that overlaps another injected event. Any ambient
        episode still running at `start` is cut there."""
        check_injection(self.sector, signature)
        if hold < SUSTAINED_SCANS:
            raise InjectionRefused(f"an injected emergency must hold at least {SUSTAINED_SCANS} scans (doc 03: a "
                                   f"disturbance needs {SUSTAINED_SCANS} consecutive scans above the threshold)")
        if start <= self._last:
            raise InjectionRefused(f"cannot inject at scan {start}: scan {self._last} has already been generated")
        target = target_for(signature)
        episode = Episode(start, ramp, hold, decay, target, injected=True, signature=signature,
                          exact=hold_exact_channels(signature, target))
        for other in self.episodes:
            if other.injected and other.start < episode.end and episode.start < other.end:
                raise InjectionRefused(f"{signature} at scan {start} overlaps the injected "
                                       f"{other.signature} at scan {other.start}")
        self.episodes = [replace(e, cut=start) if (not e.injected and e.end > start) else e
                         for e in self.episodes]
        self.episodes.append(episode)
        return episode

    def _covered(self, i):
        return [e for e in self.episodes if e.covers(i)]

    def step(self, i):
        """The three channel values for scan i, clamped to the valid ranges."""
        if i <= self._last:
            raise ValueError(f"scan {i} is not after scan {self._last}")
        self._last = i
        active = self._covered(i)
        ambient = self.spikes.roll(i, blocked=bool(active))
        if ambient is not None:
            upcoming = [e.start for e in self.episodes if e.injected and e.start > i]
            if upcoming and ambient.end > min(upcoming):
                ambient = replace(ambient, cut=min(upcoming))  # an injected event scheduled ahead wins
            self.episodes.append(ambient)
            active.append(ambient)
        values = {}
        for ch in CHANNELS:
            baseline, sigma = self.sector.channel(ch)
            x = self.walks[ch].step()  # always advances, so the ambient state stays stationary
            offset = sum(d.offset(i) for d in self.drifts if d.channel == ch)
            offset += sum(e.depth.get(ch, 0.0) * e.fraction(i) for e in active)
            if any(e.injected and e.phase(i) == "hold" and ch in e.exact for e in active):
                x = 0.0
            values[ch] = clamp(ch, baseline + offset * sigma + x)
        return values

    def active_events(self, i):
        return [e for e in self.episodes if e.covers(i)]
