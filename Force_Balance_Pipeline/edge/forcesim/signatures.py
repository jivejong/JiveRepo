"""The signature definitions shared by the backfill's historical emergencies and Phase 3's control
topic (doc 04), and a Python mirror of doc 03's classification CASE and composite score.

RULES is the doc 03 signature table as data, in doc 03's order (first match wins). classify(), the
channels a signature's target moves (MOVERS) and the population requirement are all derived from it,
and tests/test_doc_parity.py parses doc 03 and fails if RULES, the specialties, the thresholds, the
sustained-scan requirement or the composite weights drift from the document.

Injection targets are computed, not listed. The target of a signature is the smallest whole-sigma
point whose reading classifies as that signature and whose nominal composite score exceeds the
emergency threshold from doc 03 times a margin M (see margin_factor). Tuning the doc 03 threshold
recomputes every target. Injections target emergency level only; a severity can be added later as a
different threshold.

The margin is a margin on injection INTENT (the composite the injection is meant to reach). It is not
a margin on the signature's region: the region's boundary is never widened.
"""
import functools
import itertools
import math

from .constants import CHANNELS, PHI, SCANS_PER_DAY, VALID_RANGE

# Doc 03 signature table, in order; first match wins. A condition is (variable, operator, value):
#   ">"    variable > value            "<"     variable < value
#   "abs<" ABS(variable) < value       (a "quiet" bound toward zero)
RULES = (
    ("sith_presence",   (("z_dark", ">", 2.5), ("z_kyber", "<", -1.0))),
    ("dark_adept",      (("z_dark", ">", 2.0), ("z_midi", ">", 1.5))),
    ("nexus_awakening", (("z_midi", ">", 2.0), ("z_kyber", ">", 2.0), ("z_dark", "abs<", 1.5))),
    ("force_drain",     (("z_midi", "<", -2.0), ("z_kyber", "<", -2.0))),
    ("kyber_cache",     (("z_kyber", ">", 2.5), ("z_midi", "abs<", 1.5), ("z_dark", "abs<", 1.5))),
    ("civil_unrest",    (("z_dark", ">", 1.5), ("population", ">", 1e9), ("z_kyber", "abs<", 1.5))),
    ("veiled_presence", (("z_midi", "abs<", 1.0), ("z_dark", ">", 2.0), ("channels_present", "<", 3))),
)
ORDER = tuple(name for name, _ in RULES)

# Doc 03 matched specialty, for reference by later phases (checked against doc 03 by the parity test).
SPECIALTY = {"sith_presence": "combat", "dark_adept": "combat", "nexus_awakening": "investigation",
             "force_drain": "investigation", "kyber_cache": "diplomacy", "civil_unrest": "diplomacy",
             "veiled_presence": "stealth"}

# Doc 03 thresholds ("tune after the backfill exists") and the firing rule's sustained requirement.
ANOMALY_THRESHOLD = 3.0
EMERGENCY_THRESHOLD = 4.5
SUSTAINED_SCANS = 2  # doc 03: "Sustained across at least 2 consecutive scans"

# Doc 03 composite: weighted Euclidean over signed z-scores, scaled by SQRT(3 / channels_present).
COMPOSITE_WEIGHTS = {"midi": 1.0, "kyber": 1.0, "dark": 2.0}
FULL_CHANNELS = 3.0


def _channel_of(variable):
    return variable[2:] if variable.startswith("z_") else None


def _movers():
    """Channels a signature's target moves: those with a DIRECTIONAL condition (> or <) on their
    z-score. Channels bounded toward zero (abs<), channels the rule does not mention, and guard
    channels stay at 0."""
    out = {}
    for name, conditions in RULES:
        found = {_channel_of(var) for var, op, _ in conditions if op in (">", "<") and _channel_of(var)}
        out[name] = tuple(c for c in CHANNELS if c in found)
    return out


MOVERS = _movers()

# Signatures that can be injected in CONNECTED mode: not gated on partial coverage (STEALTH, Phase 3).
PRODUCIBLE = tuple(name for name, conditions in RULES
                   if not any(var == "channels_present" for var, _, _ in conditions))

# Minimum population, from the rule ("population", ">", value).
REQUIRES_POPULATION = {name: value for name, conditions in RULES
                       for var, _, value in conditions if var == "population"}


class InjectionRefused(ValueError):
    """The signature cannot be injected on this planet (or at all in CONNECTED mode)."""


def _holds(op, value, x):
    if x is None:  # SQL NULL semantics: a comparison with NULL is not true
        return False
    if op == ">":
        return x > value
    if op == "<":
        return x < value
    if op == "abs<":
        return abs(x) < value
    raise ValueError(f"unknown operator {op!r}")


def classify(z_midi, z_kyber, z_dark, population=None, channels_present=3):
    """The doc 03 CASE: the first RULES entry whose conditions all hold, else 'unclassified'."""
    env = {"z_midi": z_midi, "z_kyber": z_kyber, "z_dark": z_dark, "population": population,
           "channels_present": channels_present}
    for name, conditions in RULES:
        if all(_holds(op, value, env[var]) for var, op, value in conditions):
            return name
    return "unclassified"


def imbalance_score(z_midi, z_kyber, z_dark, channels_present=3):
    """The doc 03 composite. A missing (None) z counts as 0, like COALESCE."""
    z = {"midi": z_midi, "kyber": z_kyber, "dark": z_dark}
    total = sum(COMPOSITE_WEIGHTS[c] * (0.0 if z[c] is None else z[c]) ** 2 for c in CHANNELS)
    return math.sqrt(total) * math.sqrt(FULL_CHANNELS / channels_present)


# ---- the margin -------------------------------------------------------------------------------------
# z-scores are measured against a rolling 90-day baseline (doc 03), so one window's standard deviation
# carries sampling error. For an AR(1) series the relative standard error of a window's SD is
#     e = sqrt((1 + phi^2) / (2 (1 - phi^2) n)),   n = 90 x 96 = 8,640 scans  ->  1.9%
# (measured over 200 simulated windows: 1.89%). A reading's realised score is about nominal x (1 - d)
# with d ~ N(0, e^2), so to clear a threshold T with k standard errors the nominal score must be at
# least T / (1 - k e). That defines M = 1 / (1 - k e). k = 3 (0.13% chance of falling short per event;
# 0.5% that one of four backfill emergencies does) gives M = 1.0603. For a multi-channel target the
# composite's relative error is smaller than e, so e is a conservative bound.
WINDOW_SCANS = 90 * SCANS_PER_DAY
MARGIN_K = 3


def window_sd_se(phi=PHI, n=WINDOW_SCANS):
    return math.sqrt((1.0 + phi ** 2) / (2.0 * (1.0 - phi ** 2) * n))


def margin_factor(k=MARGIN_K, phi=PHI, n=WINDOW_SCANS):
    denominator = 1.0 - k * window_sd_se(phi, n)
    if denominator <= 0:
        raise ValueError("k standard errors leave no room: k * e >= 1")
    return 1.0 / denominator


# ---- computed targets ---------------------------------------------------------------------------------
ZMAX = 10  # the search covers whole-sigma points in [-ZMAX, ZMAX] on each moving channel


@functools.lru_cache(maxsize=None)
def _search(name, lower):
    movers = MOVERS[name]
    best = None
    for values in itertools.product(range(-ZMAX, ZMAX + 1), repeat=len(movers)):
        z = {c: 0 for c in CHANNELS}
        z.update(zip(movers, values))
        if classify(z["midi"], z["kyber"], z["dark"], 2e9) != name:  # 2e9: above any population rule
            continue
        composite = imbalance_score(z["midi"], z["kyber"], z["dark"])
        if not composite > lower:
            continue
        # smallest composite, then the smallest largest deviation, then load midichlorian (the
        # channel with the widest valid range), then a fixed order so the result is deterministic
        key = (round(composite, 9), max(abs(v) for v in values), -abs(z["midi"]), z["midi"], z["kyber"], z["dark"])
        if best is None or key < best[0]:
            best = (key, tuple(sorted(z.items())))
    return None if best is None else best[1]


def target_for(name, threshold=None, margin=None):
    """The injection target of a signature, as whole sigmas per channel: the smallest whole-sigma
    point (moving channels only) that classifies as `name` and whose nominal composite exceeds
    threshold x margin. Defaults: the doc 03 emergency threshold and margin_factor(). Raises
    InjectionRefused for a signature that cannot be injected or has no such point."""
    if name not in ORDER:
        raise InjectionRefused(f"unknown signature {name!r}")
    if name not in PRODUCIBLE:
        raise InjectionRefused(f"{name} needs channels_present < 3 (STEALTH, Phase 3); it cannot be "
                               "produced in CONNECTED mode")
    threshold = EMERGENCY_THRESHOLD if threshold is None else threshold
    margin = margin_factor() if margin is None else margin
    lower = threshold * margin
    found = _search(name, lower)
    if found is None:
        raise InjectionRefused(f"no whole-sigma point (|z| <= {ZMAX}) in {name}'s region has a composite "
                               f"above {lower:.3f}")
    return dict(found)


def hold_exact_channels(name, target=None):
    """The channels held exactly at their target during the hold phase: those where moving the channel
    away from its target (the others at target) changes the classification. Computed at the target,
    so it includes guard channels (and can grow as the target grows)."""
    target = target_for(name) if target is None else target
    population = 2e9  # any value above the population rule; only civil_unrest reads it
    grid = [x / 4.0 for x in range(-40, 41)]  # -10 to +10 sigma in 0.25 steps
    exact = set()
    for channel in CHANNELS:
        for value in grid:
            z = dict(target)
            z[channel] = value
            if classify(z["midi"], z["kyber"], z["dark"], population) != name:
                exact.add(channel)
                break
    return frozenset(exact)


def check_injection(sector, name):
    """Raise InjectionRefused unless `name` can be injected on `sector`: the signature must be
    producible in CONNECTED mode, every target value must stay inside the doc 02 valid range, and a
    population rule (civil_unrest) must hold."""
    target = target_for(name)
    for channel in CHANNELS:
        baseline, sigma = sector.channel(channel)
        value = baseline + target[channel] * sigma
        lo, hi = VALID_RANGE[channel]
        if not lo <= value <= hi:
            raise InjectionRefused(
                f"{name} on {sector.sector_id}: {channel} target {value:.2f} is outside the valid range "
                f"{lo:g}-{hi:g} (baseline {baseline:g}, sigma {sigma:g}, target {target[channel]:+d} sigma)")
    need = REQUIRES_POPULATION.get(name)
    if need is not None and not (sector.population is not None and sector.population > need):
        raise InjectionRefused(f"{name} needs population > {need:.0e}; {sector.sector_id} has "
                               f"{sector.population if sector.population is not None else 'no population value'}")
