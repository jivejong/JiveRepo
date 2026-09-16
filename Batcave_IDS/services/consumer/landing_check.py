"""Landing verification (Phase 4 checkpoint).

Read-only over whatever is already on disk and on the topic — it does not
generate a corpus, so it runs in seconds. Reports:

- the partition directories actually written, and rows per event_kind
- counts reconciled against `requests_sent` / `attempts_made` from the landed
  `attack_run` rows (ground truth for what the simulator believed it sent)
- topic-vs-disk reconciliation: the harnesses in Phase 3 read events straight
  off the topic while this reads them off disk after the consumer, so a
  divergence between the two would otherwise be silent
- the two duplicate populations Phase 5's dedupe must treat oppositely
- quarantined messages, and evidence the consumer *continued* past them

Everything is queried through DuckDB with `hive_partitioning`, which is how
Phase 5's dbt sources will read it — so this also proves the layout is legible
to the warehouse, not just that files exist.

`union_by_name` is required: the schema-drift pathology means sibling Parquet
files in one directory legitimately differ by a column.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
from confluent_kafka import Consumer, TopicPartition

from services.simulator.session import KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC

EVENT_KINDS = ("request", "attempt", "attack_run")


def _glob(data_root: Path, event_kind: str) -> str:
    return str(data_root / "raw" / event_kind / "**" / "*.parquet").replace("\\", "/")


def _read(con: duckdb.DuckDBPyConnection, data_root: Path, event_kind: str) -> str | None:
    """A DuckDB relation over one event_kind's landed files, or None if the
    kind hasn't landed anything."""
    pattern = _glob(data_root, event_kind)
    if not list((data_root / "raw" / event_kind).rglob("*.parquet")):
        return None
    # union_by_name tolerates the drift column; hive_partitioning exposes
    # dt/hour as columns the way dbt will read them.
    return f"read_parquet('{pattern}', hive_partitioning = true, union_by_name = true)"


def topic_counts() -> dict[str, int]:
    """Count messages on the topic by event_kind, plus non-JSON messages.

    Deliberately a separate path from the landed Parquet: comparing the two is
    the point (see module docstring).
    """
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": "landing-check-readonly",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    meta = consumer.list_topics(KAFKA_TOPIC, timeout=10)
    parts = list(meta.topics[KAFKA_TOPIC].partitions)
    consumer.assign([TopicPartition(KAFKA_TOPIC, p, 0) for p in parts])
    ends = {p: consumer.get_watermark_offsets(TopicPartition(KAFKA_TOPIC, p))[1] for p in parts}

    counts: dict[str, int] = {}
    done = {p: ends[p] == 0 for p in parts}
    try:
        while not all(done.values()):
            msg = consumer.poll(2.0)
            if msg is None:
                break
            if msg.error():
                continue
            try:
                kind = json.loads(msg.value()).get("event_kind", "unknown")
            except (ValueError, UnicodeDecodeError):
                kind = "undeserializable"
            counts[kind] = counts.get(kind, 0) + 1
            if msg.offset() >= ends[msg.partition()] - 1:
                done[msg.partition()] = True
    finally:
        consumer.close()
    return counts


def report(data_root: Path, check_topic: bool = True) -> bool:
    con = duckdb.connect()
    # `received_at` lands as TIMESTAMP WITH TIME ZONE, and DuckDB renders those
    # in the session's local zone. Without this, strftime(received_at, '%H')
    # returns the LOCAL hour and disagrees with the UTC `hour=` partition for
    # every row anywhere but UTC. Phase 5's dbt profile needs the same setting.
    con.execute("SET TimeZone = 'UTC'")
    ok = True

    print("\n=== landed partitions (real directories on disk) ===")
    any_landed = False
    for event_kind in EVENT_KINDS:
        directories = sorted(
            {p.parent for p in (data_root / "raw" / event_kind).rglob("*.parquet")}
        )
        files = list((data_root / "raw" / event_kind).rglob("part-*.parquet"))
        if not directories:
            print(f"  {event_kind:11} (nothing landed)")
            continue
        any_landed = True
        shown = ", ".join(str(d.relative_to(data_root / "raw")) for d in directories[:3])
        more = f" (+{len(directories) - 3} more)" if len(directories) > 3 else ""
        print(f"  {event_kind:11} {len(files):4} files across {len(directories):3} partitions")
        print(f"              {shown}{more}")
    if not any_landed:
        print("  NOTHING LANDED - run a corpus with the consumer up first.")
        return False

    print("\n=== rows per event_kind (DuckDB over the Hive layout) ===")
    landed: dict[str, int] = {}
    for event_kind in EVENT_KINDS:
        relation = _read(con, data_root, event_kind)
        if relation is None:
            continue
        landed[event_kind] = con.sql(f"SELECT count(*) FROM {relation}").fetchone()[0]
        print(f"  {event_kind:11} {landed[event_kind]:6} rows")

    # -- reconciliation against ground truth -----------------------------
    run_relation = _read(con, data_root, "attack_run")
    print("\n=== reconciliation vs attack_runs (ground truth) ===")
    if run_relation is None:
        print("  no attack_run rows landed - cannot reconcile")
        ok = False
    else:
        runs, sent, attempts = con.sql(
            f"SELECT count(*), sum(requests_sent), sum(attempts_made) FROM {run_relation}"
        ).fetchone()
        print(f"  attack_run rows landed:               {runs:6}")
        print(f"  requests_sent (sum over runs):        {sent:6}")
        print(f"  request rows landed:                  {landed.get('request', 0):6}")
        print(f"  attempts_made (sum over runs):        {attempts:6}")
        print(f"  attempt rows landed:                  {landed.get('attempt', 0):6}")
        if landed.get("attempt", 0) != attempts:
            print("  NOTE: attempt rows should equal attempts_made exactly (no pathology")
            print("        touches the ground-truth stream); a delta here is duplicate delivery")
            print("        from a replay, which is expected only after a restart exercise.")

    # -- partition values come from received_at --------------------------
    request_relation = _read(con, data_root, "request")
    if request_relation is not None:
        mismatched = con.sql(
            f"""
            SELECT count(*) FROM {request_relation}
            WHERE dt::VARCHAR <> strftime(received_at, '%Y-%m-%d')
               OR hour::VARCHAR <> strftime(received_at, '%H')
            """
        ).fetchone()[0]
        print("\n=== partition values derive from received_at, never client_ts ===")
        print(f"  rows whose dt/hour disagree with received_at: {mismatched}")
        if mismatched:
            print("  FAIL: partitioning is not keyed on received_at")
            ok = False

    # -- the two duplicate populations -----------------------------------
    if request_relation is not None:
        print("\n=== duplicates: two populations Phase 5 must treat oppositely ===")
        replayed = con.sql(
            f"""
            SELECT coalesce(sum(n - 1), 0) FROM (
                SELECT count(*) AS n FROM {request_relation}
                GROUP BY event_id HAVING count(*) > 1
            )
            """
        ).fetchone()[0]
        twoface = con.sql(
            f"""
            SELECT count(*) FROM (
                SELECT session_id, path FROM {request_relation}
                WHERE path IS NOT NULL
                GROUP BY session_id, path
                HAVING count(DISTINCT event_id) > 1
            )
            """
        ).fetchone()[0]
        print(f"  same event_id, >1 row (delivery dup + replay): {replayed:5}  -> dedupe REMOVES")
        print(
            f"  same (session, path), distinct event_ids (Two-Face): {twoface:5}  -> dedupe KEEPS"
        )

    # -- quarantine, and proof the consumer continued --------------------
    quarantine_dir = data_root / "quarantine" / "undeserializable"
    files = sorted(quarantine_dir.glob("*.json")) if quarantine_dir.exists() else []
    print("\n=== quarantine (undeserializable) ===")
    print(f"  quarantined messages: {len(files)}")
    for path in files[:5]:
        document = json.loads(path.read_text(encoding="utf-8"))
        partition, offset = document["kafka_partition"], document["kafka_offset"]
        survived = 0
        for event_kind in EVENT_KINDS:
            relation = _read(con, data_root, event_kind)
            if relation is None:
                continue
            survived += con.sql(
                f"SELECT count(*) FROM {relation} "
                f"WHERE kafka_partition = {partition} AND kafka_offset > {offset}"
            ).fetchone()[0]
        verdict = "consumer CONTINUED" if survived else "NOTHING LANDED AFTER IT"
        print(
            f"    partition={partition} offset={offset}: {survived} later rows on that "
            f"partition -> {verdict}"
        )
        if not survived:
            ok = False

    # -- topic vs disk ---------------------------------------------------
    if check_topic:
        print("\n=== topic vs disk (two paths over the same events) ===")
        on_topic = topic_counts()
        for event_kind in EVENT_KINDS:
            t, d = on_topic.get(event_kind, 0), landed.get(event_kind, 0)
            flag = "" if t == d else "   <-- DIVERGENCE"
            print(f"  {event_kind:11} topic {t:6}  disk {d:6}{flag}")
            if t != d:
                ok = False
        undeser = on_topic.get("undeserializable", 0)
        print(f"  {'undeserializable':11} topic {undeser:6}  quarantined {len(files):6}")
        print("  (undeserializable messages are on the topic by design and in quarantine,")
        print("   never in Parquet - that is the only legitimate topic/disk difference)")

    print(f"\n{'PASS' if ok else 'FAIL'}: landing verification")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("data"))
    ap.add_argument(
        "--no-topic",
        action="store_true",
        help="skip the topic read (use when the broker is down)",
    )
    args = ap.parse_args()
    raise SystemExit(0 if report(args.data_root, check_topic=not args.no_topic) else 1)


if __name__ == "__main__":
    main()
