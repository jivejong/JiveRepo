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
        row_number() over (partition by session_id order by received_at) as rn,
        epoch(received_at) - lag(epoch(received_at))
            over (partition by session_id order by received_at) as gap_s
    from req
),
req_features as (
    select
        session_id,
        count(*) as request_count,
        greatest(epoch(max(received_at)) - epoch(min(received_at)), 0.001) as duration_s,
        count(*) / (greatest(epoch(max(received_at)) - epoch(min(received_at)), 0.001) / 60.0)
            as requests_per_min,
        max(path_tier) as max_path_tier,
        avg(case when status_returned >= 400 then 1.0 else 0.0 end) as error_ratio,
        count(distinct source_ip) as distinct_source_ips,
        count(distinct user_agent) as distinct_user_agents,
        stddev_samp(gap_s) * 1000.0 as inter_request_stddev_ms,
        sum(case when query_string like '%riddle=%' then 1 else 0 end) as riddle_param_count,
        -- corr(body_bytes, request order): +1 monotonic growth (Poison Ivy).
        -- Guard the degenerate case: constant body_bytes (all-GET techniques)
        -- makes corr undefined (NaN, which coalesce won't catch) — return 0.
        case
            when count(*) > 1 and stddev_pop(body_bytes) > 0 then corr(body_bytes, rn)
            else 0.0
        end as body_bytes_trend,
        -- wasted: fraction of requests that hit nothing useful (403/404)
        avg(case when status_returned in (403, 404) then 1.0 else 0.0 end) as wasted_request_ratio
    from ordered
    group by session_id
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
    r.riddle_param_count,
    r.body_bytes_trend,
    r.wasted_request_ratio,
    coalesce(e.path_entropy, 0.0) as path_entropy,
    coalesce(e.exact_duplicate_path_pairs, 0) as exact_duplicate_path_pairs,
    coalesce(a.retry_ratio, 0.0) as retry_ratio,
    coalesce(a.pivot_ratio, 0.0) as pivot_ratio
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
]


def run_corpus(time_scale: float, seed: int = 0) -> dict[str, str]:
    """Run every villain once; return {session_id: villain_slug}."""
    scaled_sleep = lambda s: time.sleep(s * time_scale)  # noqa: E731
    mapping: dict[str, str] = {}
    for i, slug in enumerate(load_villains()):
        result = run_scripted_session(slug, rng=random.Random(seed + i), sleep_fn=scaled_sleep)
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
        "query_string varchar, body_bytes int, decision varchar)"
    )
    con.executemany(
        "insert into events values (?,?,?,?,?,?,?,?,?,?,?)",
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


def _normalize(rows: list[dict]) -> dict[str, dict[str, float]]:
    z: dict[str, dict[str, float]] = {r["villain"]: {} for r in rows}
    for f in _NORMALIZED_FEATURES:
        vals = [float(r[f]) for r in rows]
        mean = sum(vals) / len(vals)
        std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        for r in rows:
            z[r["villain"]][f] = (float(r[f]) - mean) / std if std > 1e-9 else 0.0
    return z


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
    "distinct_user_agents",
    "riddle_param_count",
    "exact_duplicate_path_pairs",
    "body_bytes_trend",
]


def report(rows: list[dict]) -> None:
    # Early-stallers (reached only tier 1 or less -> few requests) are reported
    # separately: their sessions look alike regardless of mapping quality
    # (docs/06), a gating consequence not a mapping failure.
    early = {r["villain"] for r in rows if r["max_path_tier"] <= 1}

    print("\n=== per-villain features ===")
    print("villain".ljust(16) + "".join(c[:9].rjust(11) for c in _REPORT_COLS))
    for r in sorted(rows, key=lambda r: r["villain"]):
        line = r["villain"].ljust(16) + "".join(f"{float(r[c]):11.2f}" for c in _REPORT_COLS)
        print(line + ("  (early)" if r["villain"] in early else ""))

    z = _normalize(rows)
    names = list(z)
    pairs = sorted(
        (_distance(z[a], z[b]), a, b) for i, a in enumerate(names) for b in names[i + 1 :]
    )
    print("\n=== closest pairs in normalized feature space (excluding early-stallers) ===")
    full = [(d, a, b) for d, a, b in pairs if a not in early and b not in early]
    for d, a, b in full[:8]:
        print(f"  {d:6.2f}  {a} / {b}")
    if early:
        print(f"\nearly-stallers reported separately: {sorted(early)}")
        for d, a, b in pairs:
            if (a in early or b in early) and not (a in early and b in early):
                print(f"  {d:6.2f}  {a} / {b}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--time-scale",
        type=float,
        default=0.02,
        help="multiply real pacing sleeps (normalized distances are scale-invariant)",
    )
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    print(f"running all twelve villains (time-scale={args.time_scale}) ...")
    mapping = run_corpus(args.time_scale, args.seed)
    time.sleep(2.0)  # let the last produces flush through
    events = consume_events()
    print(f"consumed {len(events)} events across {len(mapping)} sessions")
    rows = compute_features(events, mapping)
    report(rows)


if __name__ == "__main__":
    main()
