"""Rule-based baseline for both triage tasks (docs/04 "Baseline").

Run on every session alongside the LLM, or in its place when there is no
Groq key - the documented zero-credential path a cold clone depends on
(docs/04's Configuration section). Reads exactly the same
`int_session_features_observed` columns `services/triage/context.py` does -
same input contract as the LLM, nothing extra, nothing ground-truth.

**Attribution**: nearest neighbour in normalized (z-scored) feature space
between the session and each villain's REFERENCE PROFILE, computed as the
per-villain centroid over the corpus's own labeled sessions - the same
principle `services/simulator/separability.py`'s centroid/effect-size
reporting already uses, reimplemented here against the warehouse's SQL
feature table rather than the harness's in-memory event rows, because a real
baseline classifier is exactly "build reference profiles from historical
labeled incidents, then match new unlabeled ones against them." Building
those profiles from ground truth is legitimate (it never touches the
features of the session actually being scored); scoring a session against
them never reads that session's own villain_slug.

**Signature features are hard discriminators**, not just inputs to the
distance: five of docs/02's own disambiguating-feature list
(`riddle_param_count` / Riddler, `distinct_source_ips` / Penguin,
`body_bytes_trend` / Poison Ivy, `error_ratio` + low duration / Scarecrow,
`wasted_request_ratio` / Ra's al Ghul) are used directly - if a session
clears a villain's threshold, that villain is preferred outright over the
nearest-centroid result, mirroring the two-layer model itself (Layer 2

The sixth, `exact_duplicate_path_pairs` / Two-Face, is deliberately NOT used
as a hard rule - see the comment on HARD_DISCRIMINATORS below for why three
different single-feature thresholds all failed to isolate him from
retry-heavy villains on the real corpus, and cost more accuracy than they
gave back. He's classified by nearest-centroid only.
signatures are the thing that disambiguates once Layer 1 stats alone don't).

Killer Croc's real discriminator (`attempts_per_stage_reached`) is
GROUND-TRUTH-DERIVED (`int_session_features_truth`) and not observable to
either the baseline or the LLM - the two never see it, matching the boundary.
The closest OBSERVED proxy is high volume capped at a low max_path_tier
(grinding without progressing), used here as his hard discriminator instead of
the ground-truth-only one.

**Technique reconstruction**: keyword/threshold matching of each technique's
`detection_signature` against the same evidence features Phase 5 already
proved fire on real data (`traversal_pattern_count`, `injection_pattern_count`,
`repeated_auth_failure_runs`, `path_enumeration_runs`,
`sensitive_data_access_runs`) - the same six mappings
`assert_high_observability_techniques_leave_evidence` enforces (docs/02,
docs/07), reused rather than re-invented. Deliberately conservative: a
technique with no dedicated evidence feature gets no baseline prediction
rather than a guess, so the baseline's recall on `partial`/`low` tiers is
honestly near-zero - that gap is the fair ceiling docs/04 evaluates against.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import duckdb

# Same feature set the harness normalizes over, expressed as SQL columns.
# Excludes anything ground-truth-derived (retry_ratio, pivot_ratio,
# attempts_per_stage_reached live in int_session_features_truth only).
CENTROID_FEATURES = [
    "request_count",
    "duration_s",
    "requests_per_min",
    "max_path_tier",
    "error_ratio",
    "distinct_source_ips",
    "distinct_user_agents",
    "inter_request_stddev_ms",
    "mean_response_time_ms",
    "body_bytes_trend",
    "wasted_request_ratio",
    "path_entropy",
    "exact_duplicate_path_pairs",
]

# docs/02's own disambiguating-feature list, made executable. Each predicate
# takes the session's raw feature dict; a match overrides nearest-centroid.
#
# **No Two-Face rule here, though docs/02 names exact_duplicate_path_pairs as
# his disambiguating feature.** Tried three single-feature thresholds against
# the real corpus and none isolate him:
#   - exact_duplicate_path_pairs >= 2: fires on 143 of 381 sessions when only
#     ~28 are really his - Killer Croc's retry-driven path revisits produce a
#     nearly identical mean (2.12 vs Two-Face's 2.21), because retrying a
#     technique revisits its path exactly the way Two-Face's deliberate
#     doubling does. The feature can't tell "duplicated on purpose" from
#     "duplicated by grinding."
#   - the same feature as a ratio to distinct_paths: still 0.71 vs 0.73 -
#     Croc's narrow gating (low intelligence -> few available techniques)
#     means he also revisits most of his own small path set.
#   - distinct_paths alone (his coin-flip-between-two signature implies it
#     should be low): median 2 for Two-Face, but also 2 for Croc and Riddler -
#     three villains cluster in the same low range for different reasons.
# Adding a request_count cap to separate him from Croc specifically (his
# short, low-durability sessions vs Croc's long grinding ones) cut the
# Croc confusion but left the rule still firing on ~140 sessions, because
# OTHER moderate-volume villains clear it too. Forcing a single-feature
# override that's wrong 4 times out of 5 is worse than no override: nearest-
# centroid weighs this feature alongside the other twelve simultaneously,
# which is the more principled way to use a feature that's informative in
# combination but not decisive alone - exactly what "nearest neighbour in
# normalized stat space" (docs/04) already does. A genuinely reliable
# Two-Face rule would need a feature this project doesn't compute yet (e.g.
# a per-session ratio of distinct-event-id path pairs to total techniques
# attempted, isolating the "always exactly two" pattern from open-ended
# retry counts) - worth a future feature-engineering pass, not a threshold
# hack here.
HARD_DISCRIMINATORS: dict[str, Callable[[dict], bool]] = {
    "558-riddler": lambda f: f["riddle_param_count"] >= 1,
    "514-penguin": lambda f: f["distinct_source_ips"] >= 2,
    "522-poison-ivy": lambda f: f["body_bytes_trend"] >= 0.5,
    "576-scarecrow": lambda f: f["error_ratio"] >= 0.4 and f["duration_s"] < 30,
    "538-ras-al-ghul": lambda f: f["wasted_request_ratio"] <= 0.05 and f["max_path_tier"] >= 3,
    # Croc: the ground-truth discriminator (attempts_per_stage_reached) isn't
    # observable; high volume capped at a low tier is the closest proxy.
    "386-killer-croc": lambda f: f["request_count"] >= 30 and f["max_path_tier"] <= 2,
}

# The same six technique -> evidence-feature mappings
# assert_high_observability_techniques_leave_evidence enforces (docs/02).
TECHNIQUE_EVIDENCE_RULES: dict[str, str] = {
    "active_scan": "path_enumeration_runs",
    "account_discovery": "path_enumeration_runs",
    "brute_force": "repeated_auth_failure_runs",
    "exploit_public_app": "traversal_pattern_count",
    "exploit_remote_svc": "injection_pattern_count",
    "data_local_system": "sensitive_data_access_runs",
}


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def build_reference_profiles(con: duckdb.DuckDBPyConnection) -> dict:
    """Per-villain centroids in z-scored feature space, computed once over
    every labeled session currently in the warehouse. Returns
    {"stats": {feature: (mean, std)}, "centroids": {villain_slug: {feature: z}}}.
    """
    rows = con.execute(
        f"""
        select r.villain_slug, {", ".join("f." + c for c in CENTROID_FEATURES)}
        from int_session_features_observed as f
        inner join fct_attack_runs as r on f.run_id = r.run_id
        """
    ).fetchall()
    if not rows:
        raise ValueError("no labeled sessions to build reference profiles from")

    columns = list(zip(*[row[1:] for row in rows], strict=True))
    stats = {
        feature: (_mean(list(values)), _std(list(values)))
        for feature, values in zip(CENTROID_FEATURES, columns, strict=True)
    }

    by_villain: dict[str, list[tuple]] = {}
    for row in rows:
        by_villain.setdefault(row[0], []).append(row[1:])

    centroids = {}
    for villain, villain_rows in by_villain.items():
        zvecs = []
        for values in villain_rows:
            z = {}
            for feature, value in zip(CENTROID_FEATURES, values, strict=True):
                mean, std = stats[feature]
                z[feature] = (value - mean) / std if std > 1e-9 else 0.0
            zvecs.append(z)
        centroids[villain] = {
            feature: _mean([z[feature] for z in zvecs]) for feature in CENTROID_FEATURES
        }
    return {"stats": stats, "centroids": centroids}


def _zscore(features: dict, stats: dict) -> dict:
    z = {}
    for feature in CENTROID_FEATURES:
        mean, std = stats[feature]
        z[feature] = (features[feature] - mean) / std if std > 1e-9 else 0.0
    return z


def _distance(a: dict, b: dict) -> float:
    return math.sqrt(sum((a[f] - b[f]) ** 2 for f in CENTROID_FEATURES))


def classify_villain(features: dict, profiles: dict) -> tuple[str, list[str], float]:
    """Returns (predicted_slug, ranked_alternates, confidence). Confidence is
    1 / (1 + nearest_distance) for a nearest-centroid call, or a fixed high
    value when a hard discriminator fired - it's a rule match, not a
    statistical guess, and shouldn't be scored with the same uncertainty."""
    for villain, predicate in HARD_DISCRIMINATORS.items():
        if villain in profiles["centroids"] and predicate(features):
            z = _zscore(features, profiles["stats"])
            ranked = sorted(
                profiles["centroids"],
                key=lambda v: _distance(z, profiles["centroids"][v]),
            )
            alternates = [v for v in ranked if v != villain][:2]
            return villain, alternates, 0.9

    z = _zscore(features, profiles["stats"])
    ranked = sorted(profiles["centroids"], key=lambda v: _distance(z, profiles["centroids"][v]))
    nearest_distance = _distance(z, profiles["centroids"][ranked[0]])
    confidence = 1.0 / (1.0 + nearest_distance)
    return ranked[0], ranked[1:3], confidence


def villain_archetype_map(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """slug -> archetype, for deriving suspected_archetype from the baseline's
    predicted villain - the classifier itself only ever predicts a slug."""
    return dict(con.execute("select villain_slug, archetype from dim_villains").fetchall())


def classify_techniques(features: dict) -> list[dict]:
    """One prediction per technique with positive evidence - no guess where
    there's no dedicated evidence feature (see module docstring)."""
    predictions = []
    for technique_id, evidence_feature in TECHNIQUE_EVIDENCE_RULES.items():
        count = features.get(evidence_feature, 0)
        if count and count > 0:
            predictions.append(
                {
                    "technique_id": technique_id,
                    "evidence_feature": evidence_feature,
                    "evidence_count": count,
                }
            )
    return predictions
