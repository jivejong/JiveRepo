"""Pathology verification harness (Phase 3 Part 2 checkpoint).

Runs a corpus with all pathologies enabled, consumes the topic back RAW (the
undeserializable pathology emits non-JSON, which a JSON-parsing consumer would
choke on), and proves each of the ten pathologies is present in real data with
a count - a configured-but-absent pathology means broken injection and an
untested Phase 5. Also runs the three specific checks the review called out.

Corpus size: undeserializable at 0.2% is the binding constraint. runs=12
(~144 sessions, ~2600 request events) gives it a countable handful; ~4 min at
time_scale 0.02.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from confluent_kafka import Consumer, TopicPartition

from services.simulator.catalog import load_villains
from services.simulator.pathologies import PathologyConfig, PathologyInjector
from services.simulator.session import KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC, run_scripted_session


def run_corpus(runs: int, time_scale: float, seed: int) -> dict[str, str]:
    """Run every villain `runs` times with all pathologies enabled; return
    {session_id: villain_slug} for the runs we control."""
    mapping: dict[str, str] = {}
    for vi, slug in enumerate(load_villains()):
        for r in range(runs):
            rng = random.Random(seed + vi * 1000 + r)
            injector = PathologyInjector(PathologyConfig.load(), rng)
            result = run_scripted_session(
                slug,
                rng=rng,
                sleep_fn=lambda s: time.sleep(s * time_scale),
                injector=injector,
                timing_compression_factor=time_scale,
            )
            mapping[result.session_id] = slug
    return mapping


def consume_raw() -> list[tuple[bytes | None, bytes]]:
    """Every (key, value) on the topic as raw bytes - tolerant of the non-JSON
    undeserializable payloads, and keeps the key so `unkeyed` (null key) is
    countable."""
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": f"pathology-check-{time.time()}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    meta = consumer.list_topics(KAFKA_TOPIC, timeout=10)
    parts = list(meta.topics[KAFKA_TOPIC].partitions)
    consumer.assign([TopicPartition(KAFKA_TOPIC, p, 0) for p in parts])
    ends = {p: consumer.get_watermark_offsets(TopicPartition(KAFKA_TOPIC, p))[1] for p in parts}
    out: list[tuple[bytes | None, bytes]] = []
    done = {p: ends[p] == 0 for p in parts}
    try:
        while not all(done.values()):
            msg = consumer.poll(2.0)
            if msg is None:
                break
            if msg.error():
                continue
            out.append((msg.key(), msg.value()))
            if msg.offset() >= ends[msg.partition()] - 1:
                done[msg.partition()] = True
    finally:
        consumer.close()
    return out


def _parse(value: bytes) -> dict | None:
    try:
        return json.loads(value)
    except (ValueError, UnicodeDecodeError):
        return None


def report(raw: list[tuple[bytes | None, bytes]], mapping: dict[str, str]) -> bool:
    counts: Counter[str] = Counter()
    event_id_seen: Counter[str] = Counter()
    late_client_ts = 0
    shuffled_client_ts = 0
    malformed_body = 0
    unkeyed = 0
    attack_runs = 0
    now = datetime.now(UTC)

    twoface_sessions = {s for s, v in mapping.items() if v == "678-two-face"}
    twoface_paths: dict[tuple[str, str], set[str]] = defaultdict(set)  # (session,path)->event_ids
    run_ids_by_session: dict[str, set[str]] = defaultdict(set)
    received_by_session: dict[str, list[float]] = defaultdict(list)  # for burst detection
    requests_sent_reported = 0  # summed from attack_run events (ground truth)
    request_events = 0

    for key, value in raw:
        e = _parse(value)
        if e is None:
            counts["undeserializable (raw non-JSON bytes)"] += 1
            # A null key on the garbage message is the unkeyed pathology too,
            # but we count unkeyed on request events below where it's clean.
            continue
        kind = e.get("event_kind")
        if kind == "attack_run":
            attack_runs += 1
            requests_sent_reported += e.get("requests_sent", 0)
            continue
        if kind != "request":
            continue

        request_events += 1
        if key is None:
            unkeyed += 1
        eid = e["event_id"]
        event_id_seen[eid] += 1
        sid = e.get("session_id")
        if e.get("source_ip") is None:
            counts["missing_source_ip (null source_ip)"] += 1
        if e.get("path") is None:
            counts["missing_path (null path)"] += 1
        if e.get("schema_version") == "v2" or "tls_fingerprint" in e:
            counts["schema_drift (v2 + tls_fingerprint)"] += 1
        if (rt := e.get("response_time_ms")) is not None and rt < 0:
            counts["clock_skew_negative_response (clamp+count)"] += 1
        if (ra := e.get("received_at")) and datetime.fromisoformat(ra) > now + timedelta(
            minutes=30
        ):
            counts["clock_skew_future_received_at (quarantine)"] += 1
        if (cts := e.get("client_ts")) is not None:
            delta = datetime.fromisoformat(cts) - datetime.fromisoformat(e["received_at"])
            if delta < timedelta(minutes=-30):
                late_client_ts += 1
            elif abs(delta.total_seconds()) > 1:
                shuffled_client_ts += 1
        body = e.get("request_body")
        if body is not None and body.strip().startswith("{") and _parse(body.encode()) is None:
            malformed_body += 1
        if e.get("run_id") and sid:
            run_ids_by_session[sid].add(e["run_id"])
        if sid and (ra2 := e.get("received_at")):
            received_by_session[sid].append(datetime.fromisoformat(ra2).timestamp())
        if sid in twoface_sessions and e.get("path"):
            twoface_paths[(sid, e["path"])].add(eid)

    # Burst: the pathology fires requests back-to-back (~HTTP latency apart)
    # while normal compressed pacing leaves a larger gap. Count sessions with a
    # run of >= 5 consecutive requests spaced under 15ms.
    burst_sessions = 0
    for times in received_by_session.values():
        times.sort()
        run_len = 1
        for a, b in zip(times, times[1:], strict=False):
            run_len = run_len + 1 if (b - a) < 0.015 else 1
            if run_len >= 5:
                burst_sessions += 1
                break
    counts["burst (>=5 requests < 15ms apart)"] = burst_sessions

    counts["duplicate_delivery (identical event_id)"] = sum(
        c - 1 for c in event_id_seen.values() if c > 1
    )
    counts["unkeyed (null Kafka key)"] = unkeyed
    counts["late_arrival (client_ts 1-6h old)"] = late_client_ts
    counts["out_of_order_client_ts (shuffled)"] = shuffled_client_ts
    counts["malformed_body (invalid JSON body)"] = malformed_body

    print("\n=== pathology counts (real consumed data) ===")
    absent = []
    for label, n in sorted(counts.items()):
        flag = "  <-- ABSENT" if n == 0 else ""
        if n == 0:
            absent.append(label)
        print(f"  {label:44} {n:5}{flag}")
    print(f"  attack_run events published                  {attack_runs:5}")

    # --- the three specific checks ---
    print("\n=== specific checks ===")
    # 1. Two-Face distinct-event_id duplicate requests coexist with
    #    identical-event_id delivery duplicates. A (session, path) with >= 2
    #    distinct event_ids is Two-Face re-issuing the same request.
    tf_distinct = sum(1 for ids in twoface_paths.values() if len(ids) >= 2)
    delivery_dupes = sum(1 for c in event_id_seen.values() if c > 1)
    print(
        f"  Two-Face duplicate requests (distinct event_id): {tf_distinct} sessions; "
        f"delivery duplicates (identical event_id): {delivery_dupes}. "
        f"Distinguishable by event_id equality - dedupe removes the latter, keeps the former."
    )
    # 2. undeserializable emitted; handling is Phase 4.
    print(
        f"  Undeserializable emitted now: {counts['undeserializable (raw non-JSON bytes)']} raw "
        f"non-JSON messages on the topic. The CONSUMER quarantines them - that's Phase 4; here we "
        f"only prove they're emitted."
    )
    # 3. session-merge bug stays fixed: distinct runs -> distinct session_ids.
    shared = {s: rids for s, rids in run_ids_by_session.items() if len(rids) > 1}
    print(
        f"  Session-merge fixed: {len(run_ids_by_session)} sessions, "
        f"{len(shared)} shared by >1 run (want 0)."
    )

    # Reconciliation (the mart_reconciliation shape, docs/03). Computed here in
    # Python; Phase 5's dbt mart_reconciliation must reproduce these same
    # numbers on the same corpus (the harness/dbt cross-check).
    dup_extra = counts["duplicate_delivery (identical event_id)"]
    print("\n=== reconciliation (pre-consumer; Phase 5 dbt must reproduce) ===")
    print(f"  requests sent by simulator (attack_runs):    {requests_sent_reported}")
    print(f"  request events on topic:                     {request_events}")
    print(f"  of which duplicate-delivery copies:          {dup_extra}")
    print(f"  events with invalid JSON body:               {malformed_body}")
    print(f"  late arrivals detected:                      {late_client_ts}")
    undeser = counts["undeserializable (raw non-JSON bytes)"]
    print(f"  undeserializable (non-JSON) messages:        {undeser}")
    print(f"  attack_run rows:                             {attack_runs}")
    print(
        "  Note: request events on topic exceed requests-sent (attack_runs counts only technique-"
        "driven sends) by one session warm-up request each, plus duplicate-delivery copies, minus "
        "undeserializable substitutions. Dedupe/quarantine reconcile it in Phase 4/5."
    )

    ok = not absent and not shared
    summary = "every pathology present, checks pass"
    if not ok:
        summary = f"absent={absent} shared={len(shared)}"
    print(f"\n{'PASS' if ok else 'FAIL'}: {summary}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=12)
    ap.add_argument("--time-scale", type=float, default=0.02)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    print(f"running 12 villains x {args.runs} runs, all pathologies enabled ...")
    t0 = time.time()
    mapping = run_corpus(args.runs, args.time_scale, args.seed)
    print(f"corpus ran in {time.time() - t0:.1f}s")
    time.sleep(2.0)
    raw = consume_raw()
    print(f"consumed {len(raw)} messages across {len(mapping)} sessions")
    ok = report(raw, mapping)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
