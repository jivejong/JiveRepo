"""Separability harness (Phase 3 checkpoint, Part 1).

Runs every villain, consumes the attempt + request events back off the topic
into DuckDB, computes the discriminating session features **in SQL**, and
reports how separable the villains are in normalized feature space.

Why SQL, not pandas: Phase 5 lifts these exact expressions into the dbt
feature models (docs/06). A divergence between this harness and dbt on the
same event corpus is then a bug in one of them, not two implementations
drifting — so the harness stays a permanent artifact (`make separability`),
the other half of that cross-check.

Timing note: requests are paced in real time, so a full run is minutes. The
`--time-scale` factor shrinks the sleeps uniformly; because the report uses
**normalized** (z-scored) distances, scaling every villain's timing by the
same factor leaves separability unchanged — it only makes iteration fast.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time

import duckdb
from confluent_kafka import Consumer, TopicPartition

from services.simulator.catalog import load_villains
from services.simulator.session import KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC, run_scripted_session

# The discriminating subset (docs/06 Phase 3 plan) — not all of docs/02's
# features, just the ones that do the separability work plus the five
# signature features.
_FEATURE_SQL = """
with req as (
    select * from events where event_kind = 'request'
),
att as (
    select * from events where event_kind = 'attempt'
),
ordered as (
    select
        session_id,
        received_at,
        path,
        status_returned,
        path_tier,
        source_ip,
        user_agent,
        query_string,
        body_bytes,
        response_time_ms,
        row_number() over (partition by session_id order by received_at) as rn,
        epoch(received_at) - lag(epoch(received_at))
            over (partition by session_id order by received_at) as gap_s,
        -- tier_reached_so_far BEFORE this request (running max of prior rows).
        coalesce(
            max(path_tier) over (
                partition by session_id order by received_at
                rows between unbounded preceding and 1 preceding
            ), -1
        ) as tier_before
    from req
),
peak as (
    select
        session_id,
        max(path_tier) as peak_tier,
        min(case when path_tier = mt then rn end) as first_peak_rn
    from (
        select o.*, max(path_tier) over (partition by session_id) as mt from ordered o
    )
    group by session_id
),
req_features as (
    select
        o.session_id,
        count(*) as request_count,
        greatest(epoch(max(o.received_at)) - epoch(min(o.received_at)), 0.001) as duration_s,
        count(*) / (greatest(epoch(max(o.received_at)) - epoch(min(o.received_at)), 0.001) / 60.0)
            as requests_per_min,
        max(o.path_tier) as max_path_tier,
        avg(case when o.status_returned >= 400 then 1.0 else 0.0 end) as error_ratio,
        count(distinct o.source_ip) as distinct_source_ips,
        count(distinct o.user_agent) as distinct_user_agents,
        stddev_samp(o.gap_s) * 1000.0 as inter_request_stddev_ms,
        avg(o.response_time_ms) as mean_response_time_ms,
        sum(case when o.query_string like '%riddle=%' then 1 else 0 end) as riddle_param_count,
        case
            when count(*) > 1 and stddev_pop(o.body_bytes) > 0 then corr(o.body_bytes, o.rn)
            else 0.0
        end as body_bytes_trend,
        -- wasted_request_ratio (docs/02): fraction of requests that did NOT
        -- increase tier_reached_so_far, counted only up to first reaching the
        -- session's peak tier. Requests after peak aren't waste — there's
        -- nothing left to advance toward (Bane's post-escalation hammering is
        -- the objective, not waste). A request advances iff its tier exceeds
        -- the running max before it.
        sum(case when o.rn <= p.first_peak_rn and o.path_tier <= o.tier_before then 1 else 0 end)
            * 1.0 / greatest(min(p.first_peak_rn), 1) as wasted_request_ratio
    from ordered o
    join peak p on o.session_id = p.session_id
    group by o.session_id
),
path_counts as (
    select session_id, path, count(*) as c
    from req group by session_id, path
),
entropy as (
    select
        pc.session_id,
        -sum((pc.c * 1.0 / rc.total) * log2(pc.c * 1.0 / rc.total)) as path_entropy,
        sum(case when pc.c > 1 then 1 else 0 end) as exact_duplicate_path_pairs
    from path_counts pc
    join (select session_id, sum(c) as total from path_counts group by session_id) rc
        on pc.session_id = rc.session_id
    group by pc.session_id
),
att_features as (
    select
        session_id,
        count(*) as attempt_count,
        -- Croc's real discriminator (docs/03): he concentrates a similar
        -- attempt count into far fewer stages than a villain who reaches the
        -- objective. durability 90 + a hard gating ceiling at stage 2.
        count(*) * 1.0 / greatest(max(stage), 1) as attempts_per_stage_reached,
        avg(case when decision = 'retry' then 1.0 else 0.0 end) as retry_ratio,
        avg(case when decision = 'pivot' then 1.0 else 0.0 end) as pivot_ratio
    from att
    group by session_id
)
select
    r.session_id,
    r.request_count,
    r.duration_s,
    r.requests_per_min,
    r.max_path_tier,
    r.error_ratio,
    r.distinct_source_ips,
    r.distinct_user_agents,
    coalesce(r.inter_request_stddev_ms, 0.0) as inter_request_stddev_ms,
    coalesce(r.mean_response_time_ms, 0.0) as mean_response_time_ms,
    r.riddle_param_count,
    r.body_bytes_trend,
    r.wasted_request_ratio,
    coalesce(e.path_entropy, 0.0) as path_entropy,
    coalesce(e.exact_duplicate_path_pairs, 0) as exact_duplicate_path_pairs,
    coalesce(a.retry_ratio, 0.0) as retry_ratio,
    coalesce(a.pivot_ratio, 0.0) as pivot_ratio,
    coalesce(a.attempts_per_stage_reached, 0.0) as attempts_per_stage_reached
from req_features r
left join entropy e on r.session_id = e.session_id
left join att_features a on r.session_id = a.session_id
"""

_NORMALIZED_FEATURES = [
    "requests_per_min",
    "duration_s",
    "max_path_tier",
    "error_ratio",
    "inter_request_stddev_ms",
    "path_entropy",
    "retry_ratio",
    "pivot_ratio",
    "riddle_param_count",
    "exact_duplicate_path_pairs",
    "distinct_source_ips",
    "body_bytes_trend",
    "wasted_request_ratio",
    "attempts_per_stage_reached",
    "mean_response_time_ms",
]

# The mid-stat cluster (Bane, Harley, Poison Ivy, Freeze) overlaps on
# stat-derived behavior — expected, since a derived mapping produces similar
# behavior from similar stats. What must still separate them is their Layer 2
# fingerprint. Split the features so the two can be measured apart.
_CLUSTER = ["60-bane", "309-harley-quinn", "522-poison-ivy", "457-mister-freeze"]
_SIGNATURE_FEATURES = [
    "body_bytes_trend",  # Poison Ivy: monotonic growth
    "inter_request_stddev_ms",  # Harley: bursts then pauses
    "mean_response_time_ms",  # Freeze: holds connections open
    "path_entropy",  # Bane: hammers one endpoint -> low entropy
]
_STAT_FEATURES = [
    "requests_per_min",
    "duration_s",
    "max_path_tier",
    "error_ratio",
    "retry_ratio",
    "pivot_ratio",
    "wasted_request_ratio",
    "attempts_per_stage_reached",
]


def run_corpus(time_scale: float, runs_per_villain: int, seed: int = 0) -> dict[str, str]:
    """Run every villain `runs_per_villain` times; return {session_id: slug}.

    Multiple runs per villain because a single run is noisy for stop-on-first-
    error villains (Riddler, Two-Face) — durability 14 means a couple of early
    rolls decide the whole session. That variance is correct behavior, not
    noise to damp; averaging over runs just lets the steady-state separability
    be read through it. Each run's seed is derived from `seed` so a batch is
    reproducible (the seed is printed) without being locked to one value."""
    scaled_sleep = lambda s: time.sleep(s * time_scale)  # noqa: E731
    mapping: dict[str, str] = {}
    for vi, slug in enumerate(load_villains()):
        for r in range(runs_per_villain):
            run_seed = seed + vi * 1000 + r
            result = run_scripted_session(slug, rng=random.Random(run_seed), sleep_fn=scaled_sleep)
            mapping[result.session_id] = slug
    return mapping


def consume_events() -> list[dict]:
    """Read every message currently on the topic (earliest -> high-watermark)."""
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": f"separability-{time.time()}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    # Resolve the end offsets so we know when we've drained the topic.
    metadata = consumer.list_topics(KAFKA_TOPIC, timeout=10)
    partitions = list(metadata.topics[KAFKA_TOPIC].partitions)
    tps = [TopicPartition(KAFKA_TOPIC, p, 0) for p in partitions]
    consumer.assign(tps)
    ends = {
        p: consumer.get_watermark_offsets(TopicPartition(KAFKA_TOPIC, p))[1] for p in partitions
    }

    events: list[dict] = []
    done = {p: ends[p] == 0 for p in partitions}
    try:
        while not all(done.values()):
            msg = consumer.poll(2.0)
            if msg is None:
                break
            if msg.error():
                continue
            events.append(json.loads(msg.value()))
            if msg.offset() >= ends[msg.partition()] - 1:
                done[msg.partition()] = True
    finally:
        consumer.close()
    return events


def compute_features(events: list[dict], session_to_villain: dict[str, str]) -> list[dict]:
    con = duckdb.connect(":memory:")
    con.execute(
        "create table events (event_kind varchar, session_id varchar, received_at timestamp, "
        "path varchar, status_returned int, path_tier int, source_ip varchar, user_agent varchar, "
        "query_string varchar, body_bytes int, decision varchar, stage int, "
        "response_time_ms double)"
    )
    con.executemany(
        "insert into events values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            [
                e.get("event_kind"),
                e.get("session_id"),
                e.get("received_at"),
                e.get("path"),
                e.get("status_returned"),
                e.get("path_tier"),
                e.get("source_ip"),
                e.get("user_agent"),
                e.get("query_string"),
                e.get("body_bytes"),
                e.get("decision"),
                e.get("stage"),
                e.get("response_time_ms"),
            ]
            for e in events
        ],
    )
    cur = con.execute(_FEATURE_SQL)
    columns = [d[0] for d in cur.description]
    rows: list[dict] = []
    for values in cur.fetchall():
        row = dict(zip(columns, values, strict=True))
        villain = session_to_villain.get(row["session_id"])
        if villain is None:
            continue
        row["villain"] = villain
        rows.append(row)
    return rows


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _std(xs: list[float]) -> float:
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


# requests_per_min explodes for near-instant sessions (a 3-request session
# spanning milliseconds implies tens of thousands/min), which would dominate
# the normalization. Cap it — nothing realistic exceeds a few hundred/min.
_REQ_PER_MIN_CAP = 600.0


def _normalize_rows(rows: list[dict]) -> list[dict]:
    """Z-score every feature by its GLOBAL spread across all runs of all
    villains, so features are comparable and within-villain spread and
    between-villain distance are measured in the same units."""
    for r in rows:
        r["requests_per_min"] = min(float(r["requests_per_min"]), _REQ_PER_MIN_CAP)
    stats = {}
    for f in _NORMALIZED_FEATURES:
        vals = [float(r[f]) for r in rows]
        stats[f] = (_mean(vals), _std(vals))
    out = []
    for r in rows:
        z = {"villain": r["villain"]}
        for f in _NORMALIZED_FEATURES:
            m, s = stats[f]
            z[f] = (float(r[f]) - m) / s if s > 1e-9 else 0.0
        out.append(z)
    return out


def _centroids(zrows: list[dict]) -> dict[str, dict[str, float]]:
    by_villain: dict[str, list[dict]] = {}
    for z in zrows:
        by_villain.setdefault(z["villain"], []).append(z)
    return {
        v: {f: _mean([z[f] for z in runs]) for f in _NORMALIZED_FEATURES}
        for v, runs in by_villain.items()
    }


def _within_spread(zrows: list[dict], centroids: dict[str, dict[str, float]]) -> dict[str, float]:
    """RMS distance of a villain's runs from its own centroid — the radius of
    its cloud in normalized space."""
    by_villain: dict[str, list[dict]] = {}
    for z in zrows:
        by_villain.setdefault(z["villain"], []).append(z)
    spread = {}
    for v, runs in by_villain.items():
        c = centroids[v]
        sq = [sum((z[f] - c[f]) ** 2 for f in _NORMALIZED_FEATURES) for z in runs]
        spread[v] = math.sqrt(_mean(sq)) if sq else 0.0
    return spread


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.sqrt(sum((a[f] - b[f]) ** 2 for f in _NORMALIZED_FEATURES))


_REPORT_COLS = [
    "request_count",
    "duration_s",
    "requests_per_min",
    "max_path_tier",
    "error_ratio",
    "inter_request_stddev_ms",
    "retry_ratio",
    "pivot_ratio",
    "distinct_source_ips",
    "riddle_param_count",
    "exact_duplicate_path_pairs",
    "wasted_request_ratio",
]


def _leaders(rows: list[dict]) -> None:
    """Who actually leads the checkpoint's single-feature claims, from real
    per-villain means — reported because several of those claims came from
    prose, not measurement (Croc request_count, Freeze duration, and which
    villain leads retry_ratio / pivot_ratio / wasted_request_ratio)."""
    by_villain: dict[str, list[dict]] = {}
    for r in rows:
        by_villain.setdefault(r["villain"], []).append(r)
    means = {
        v: {c: _mean([float(x[c]) for x in rs]) for c in _REPORT_COLS + ["wasted_request_ratio"]}
        for v, rs in by_villain.items()
    }
    print("\n=== who leads each claimed feature (per-villain means) ===")
    for feat in [
        "request_count",
        "duration_s",
        "retry_ratio",
        "pivot_ratio",
        "wasted_request_ratio",
    ]:
        ranked = sorted(means.items(), key=lambda kv: kv[1][feat], reverse=True)
        top = ", ".join(f"{v.split('-', 1)[1]}={m[feat]:.2f}" for v, m in ranked[:3])
        print(f"  {feat:24} {top}")


def report(rows: list[dict]) -> None:
    by_villain: dict[str, list[dict]] = {}
    for r in rows:
        by_villain.setdefault(r["villain"], []).append(r)

    print("\n=== per-villain feature means ===")
    print("villain".ljust(16) + "".join(c[:9].rjust(11) for c in _REPORT_COLS))
    for v in sorted(by_villain):
        rs = by_villain[v]
        line = v.ljust(16) + "".join(
            f"{_mean([float(x[c]) for x in rs]):11.2f}" for c in _REPORT_COLS
        )
        print(line)

    _leaders(rows)

    zrows = _normalize_rows(rows)
    centroids = _centroids(zrows)
    spread = _within_spread(zrows, centroids)
    names = list(centroids)

    # Effect size = centroid distance / pooled within-villain spread. This is
    # the metric that predicts single-session classifiability (Phase 6): two
    # villains with distant centroids but overlapping clouds are NOT separable
    # for one session. Raw centroid distance kept alongside for comparison to
    # the stat-space analysis.
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            cdist = _distance(centroids[a], centroids[b])
            pooled = math.sqrt((spread[a] ** 2 + spread[b] ** 2) / 2) or 1e-9
            pairs.append((cdist / pooled, cdist, a, b))
    pairs.sort()

    print("\n=== closest pairs by EFFECT SIZE (centroid dist / within-villain spread) ===")
    print("  effect  centroid  pair")
    for eff, cdist, a, b in pairs[:10]:
        print(f"  {eff:6.2f}  {cdist:8.2f}  {a} / {b}")


def diagnostics(rows: list[dict]) -> None:
    """The specific numbers the tuning-loop review asked for: the full
    request_count distribution (mean +/- spread) across all twelve, so we can
    see whether it discriminates or everything lands around ten; and whether
    the redefined wasted_request_ratio correlates with error_ratio (if it
    does, one of the two isn't earning its place)."""
    by_villain: dict[str, list[dict]] = {}
    for r in rows:
        by_villain.setdefault(r["villain"], []).append(r)

    print("\n=== request_count distribution (mean +/- within-villain spread) ===")
    rc = []

    def _rc_mean(v):
        return _mean([float(x["request_count"]) for x in by_villain[v]])

    for v in sorted(by_villain, key=lambda v: -_rc_mean(v)):
        vals = [float(x["request_count"]) for x in by_villain[v]]
        rc.append((v, _mean(vals), _std(vals)))
        rng_s = f"(min {min(vals):.0f}, max {max(vals):.0f})"
        print(f"  {v:18} {_mean(vals):6.1f} +/- {_std(vals):5.1f}   {rng_s}")
    lo = min(m for _, m, _ in rc)
    hi = max(m for _, m, _ in rc)
    print(f"  range of per-villain means: {lo:.1f} .. {hi:.1f}  (ratio {hi / max(lo, 0.01):.1f}x)")

    print("\n=== attempts_per_stage_reached (Croc's discriminator) ===")
    for v in sorted(
        by_villain,
        key=lambda v: -_mean([float(x["attempts_per_stage_reached"]) for x in by_villain[v]]),
    ):
        vals = [float(x["attempts_per_stage_reached"]) for x in by_villain[v]]
        print(f"  {v:18} {_mean(vals):5.2f} +/- {_std(vals):4.2f}")

    print("\n=== wasted_request_ratio (redefined, progress-based) ===")
    for v in sorted(
        by_villain, key=lambda v: _mean([float(x["wasted_request_ratio"]) for x in by_villain[v]])
    ):
        vals = [float(x["wasted_request_ratio"]) for x in by_villain[v]]
        print(f"  {v:18} {_mean(vals):.3f}")

    # Correlation of per-villain-mean wasted vs error across the twelve.
    wv = [_mean([float(x["wasted_request_ratio"]) for x in by_villain[v]]) for v in by_villain]
    ev = [_mean([float(x["error_ratio"]) for x in by_villain[v]]) for v in by_villain]
    mw, me = _mean(wv), _mean(ev)
    cov = _mean([(w - mw) * (e - me) for w, e in zip(wv, ev, strict=True)])
    denom = _std(wv) * _std(ev)
    r = cov / denom if denom > 1e-9 else 0.0
    print(f"\nwasted_request_ratio vs error_ratio correlation across the twelve: r = {r:.2f}")
    print("(strong correlation would mean the two features measure the same thing)")


def _subset_effect_size(rows: list[dict], a: str, b: str, features: list[str]) -> float:
    """Effect size (centroid distance / pooled within-villain spread) between
    two villains over a feature subset, z-scored across all villains so the
    features are comparable within the subset."""
    stats = {}
    for f in features:
        vals = [float(r[f]) for r in rows]
        stats[f] = (_mean(vals), _std(vals))

    def zvec(r: dict) -> dict[str, float]:
        return {
            f: (float(r[f]) - stats[f][0]) / stats[f][1] if stats[f][1] > 1e-9 else 0.0
            for f in features
        }

    za = [zvec(r) for r in rows if r["villain"] == a]
    zb = [zvec(r) for r in rows if r["villain"] == b]
    ca = {f: _mean([z[f] for z in za]) for f in features}
    cb = {f: _mean([z[f] for z in zb]) for f in features}
    centroid = math.sqrt(sum((ca[f] - cb[f]) ** 2 for f in features))
    sa = math.sqrt(_mean([sum((z[f] - ca[f]) ** 2 for f in features) for z in za]))
    sb = math.sqrt(_mean([sum((z[f] - cb[f]) ** 2 for f in features) for z in zb]))
    pooled = math.sqrt((sa**2 + sb**2) / 2) or 1e-9
    return centroid / pooled


def cluster_analysis(rows: list[dict]) -> None:
    """The mid-stat cluster: report each pair's effect size on SIGNATURE
    features alone vs STAT features alone. If they overlap on stat features
    (expected — similar stats, derived mapping) but separate on signature
    features, the two-layer model is working: overlap on behavior shape,
    separate on fingerprint. If a pair fails to separate on signature features
    too, a signature isn't producing a measurable trace and needs fixing."""
    print("\n=== mid-stat cluster: signature vs stat effect size ===")
    print("  pair                              sig_only   stat_only")
    for i, a in enumerate(_CLUSTER):
        for b in _CLUSTER[i + 1 :]:
            sig = _subset_effect_size(rows, a, b, _SIGNATURE_FEATURES)
            stat = _subset_effect_size(rows, a, b, _STAT_FEATURES)
            an, bn = a.split("-", 1)[1], b.split("-", 1)[1]
            flag = "  <- weak sig" if sig < 1.0 else ""
            print(f"  {an + ' / ' + bn:32} {sig:8.2f}   {stat:8.2f}{flag}")


def signature_feature_check(rows: list[dict], villains: list[str], features: list[str]) -> None:
    """Verify a low-durability pair separates on SIGNATURE features even if
    tier/duration are high-variance (per the plan). Reports per-feature effect
    size (standardized mean difference) between the two villains."""
    by_villain: dict[str, list[dict]] = {}
    for r in rows:
        by_villain.setdefault(r["villain"], []).append(r)
    a, b = villains
    print(f"\n=== per-feature effect size, {a} vs {b} ===")
    for f in features:
        av = [float(x[f]) for x in by_villain.get(a, [])]
        bv = [float(x[f]) for x in by_villain.get(b, [])]
        if not av or not bv:
            continue
        pooled = math.sqrt((_std(av) ** 2 + _std(bv) ** 2) / 2) or 1e-9
        d = abs(_mean(av) - _mean(bv)) / pooled
        an, bn = a.split("-", 1)[1], b.split("-", 1)[1]
        print(f"  {f:28} d={d:6.2f}   ({an}={_mean(av):.2f}, {bn}={_mean(bv):.2f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--time-scale",
        type=float,
        default=0.02,
        help="multiply real pacing sleeps (normalized distances are scale-invariant)",
    )
    ap.add_argument("--runs", type=int, default=10, help="runs per villain (averaged)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    print(
        f"running 12 villains x {args.runs} runs (time-scale={args.time_scale}, seed={args.seed})"
    )
    mapping = run_corpus(args.time_scale, args.runs, args.seed)
    time.sleep(2.0)  # let the last produces flush through
    events = consume_events()
    rows = compute_features(events, mapping)
    print(f"consumed {len(events)} events across {len(rows)} sessions")
    report(rows)
    diagnostics(rows)
    cluster_analysis(rows)
    # Riddler and Two-Face (durability 14) are outcome-noisy by design; verify
    # their separation rests on the signature features, which are present
    # regardless of how far the run gets (plan / user directive).
    signature_feature_check(
        rows,
        ["558-riddler", "678-two-face"],
        ["riddle_param_count", "exact_duplicate_path_pairs", "max_path_tier", "duration_s"],
    )


if __name__ == "__main__":
    main()
