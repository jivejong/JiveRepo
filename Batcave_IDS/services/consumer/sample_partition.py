"""Regenerates the committed sample partition (docs/02, docs/05, docs/06).

A few hundred rows of REAL landed Parquet, committed so a reader can inspect
actual output and `dbt run` works straight from a clean clone without a broker
or a corpus run.

Written as a tool rather than done by hand because the sample has to be
regenerated whenever the corpus changes — Phase 6 re-runs the baseline after
fixing technique selection (docs/06), and a sample that silently went stale
would be worse than none.

Three properties the selection has to preserve, none of which a random slice
gives you:

- **Self-consistency.** Whole sessions, never loose rows. The sample carries
  every request, attempt, attack_run, chat_turn, and counterstrike row for
  each chosen session, so the marts join and `mart_killchain_funnel` is
  meaningful rather than empty.
- **Raw, not staged — with one deliberate exception.** Copied from the landed
  Parquet, so the committed sample still contains the duplicate-delivery
  copies, the null paths and the clock skew. A sample taken from
  `stg_attack_events` would already be deduplicated and quarantined, and a
  clean clone would never exercise the handling those models exist for. The
  one column this philosophy does not extend to is `chat_turn.user_text`
  (`_EXCLUDED_COLUMNS` below): docs/02 excludes it from the committed sample
  without exception, so it is redacted at the copy query itself rather than
  copied raw and relying on every downstream model to keep dropping it.
- **Coverage.** One session per villain (all twelve, so attribution has a full
  candidate set), preferring sessions that exercise a high-observability
  technique no other chosen session covers, then the widest pathology variety,
  then the smallest. Without the first preference the sample misses
  `exploit_remote_svc` entirely — it has only 6 attempts in a 2,102-attempt
  corpus — and `injection_pattern_count` reads zero on a clean clone. A real
  chat_turn conversation (Phase 9, docs/08) gets its own, higher-priority
  preference ahead of this one: `deploy_batbot` merely being covered by *some*
  session says nothing about which one, if any, has an actual bat bot
  conversation attached, and once real testing has run the technique dozens
  of times, leaving that to the pathology/row-count tiebreak was verified
  (against real output) to pick the wrong session more often than not. A real
  counterstrike sequence (Phase 10, docs/08) gets the same treatment one
  priority level down, found the identical way: regenerating the sample
  copied zero counterstrike rows despite real counterstrike data already in
  the corpus, because no session-selection preference knew to look for it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

EVENT_KINDS = ("request", "attempt", "attack_run", "chat_turn", "counterstrike")
SAMPLE_NAME = "sample-0.parquet"

# The one kind-specific exception to "raw, not staged" (module docstring
# below): chat_turn's raw copy still excludes user_text, the same way every
# other kind's copy preserves pathologies raw. docs/02's guarantee that
# user_text never reaches the committed sample partition is enforced right
# here, at the copy query itself, not filtered downstream.
_EXCLUDED_COLUMNS = {"chat_turn": ("user_text",)}


def _raw(data_root: Path, kind: str) -> str:
    pattern = (data_root / "raw" / kind / "**" / "*.parquet").as_posix()
    return (
        f"read_parquet('{pattern}', hive_partitioning = true, union_by_name = true, "
        "hive_types = {'dt': DATE, 'hour': VARCHAR})"
    )


def choose_sessions(con: duckdb.DuckDBPyConnection, data_root: Path) -> list[str]:
    """One session per villain, by the coverage rule in the module docstring."""
    con.execute(
        f"create or replace temp view raw_requests as select * from {_raw(data_root, 'request')}"
    )
    con.execute(
        """
        create or replace temp view session_profile as
        select
            r.villain_slug,
            q.session_id,
            count(*) as row_count,
            max(case when d.is_duplicate then 1 else 0 end)
            + max(case when q.path is null then 1 else 0 end)
            + max(case when q.source_ip is null then 1 else 0 end)
            + max(case when q.received_at > q.landed_at + interval 1 minute then 1 else 0 end)
            + max(case when q.response_time_ms < 0 then 1 else 0 end)
            + max(case when q.schema_version <> 'v1' then 1 else 0 end)
            + max(case when q.client_ts < q.received_at - interval 30 minute then 1 else 0 end)
                as pathology_variety
        from raw_requests as q
        join (
            select event_id, count(*) > 1 as is_duplicate
            from raw_requests group by event_id
        ) as d on q.event_id = d.event_id
        join fct_attack_runs as r on q.run_id = r.run_id
        group by r.villain_slug, q.session_id
        """
    )
    # High-observability techniques each session exercises.
    con.execute(
        """
        create or replace temp view session_high_techniques as
        select distinct a.session_id, a.technique_id
        from stg_attack_attempts as a
        join stg_techniques as t on a.technique_id = t.technique_id
        where t.observability = 'high' and t.produces_traffic
        """
    )

    rare = [
        row[0]
        for row in con.sql(
            """
            select technique_id from session_high_techniques
            group by technique_id order by count(*) asc
            """
        ).fetchall()
    ]

    # Sessions carrying a real bat bot conversation (Phase 9, docs/08).
    # `deploy_batbot` being covered (above) only means *some* session used
    # the technique - once real testing has run it dozens of times, many
    # sessions tie on that alone, and the existing tiebreak (pathology
    # variety, row count) has no idea which of them, if any, actually has a
    # chat_turn conversation attached. Read from stg_botchat_turns (already
    # built, correctly empty pre-Phase-9) rather than a raw glob, so this
    # doesn't need the same empty-glob guard write_sample() needs below.
    sessions_with_chat_turns = {
        r[0] for r in con.sql("select distinct session_id from stg_botchat_turns").fetchall()
    }

    # Same reasoning, same fix, one phase later (Phase 10, docs/08): a
    # session's finale/counterstrike sequence is real data the committed
    # sample should be able to demonstrate too, and "some session finished"
    # says nothing about which one, if any, actually has counterstrike rows
    # attached - discovered the exact same way chat_turn's gap was, by
    # regenerating the sample and finding zero counterstrike rows copied
    # despite real counterstrike data existing in the corpus.
    sessions_with_counterstrike = {
        r[0] for r in con.sql("select distinct session_id from stg_counterstrike_events").fetchall()
    }

    chosen: list[str] = []
    covered: set[str] = set()
    villains = [
        r[0]
        for r in con.sql("select distinct villain_slug from session_profile order by 1").fetchall()
    ]
    for villain in villains:
        candidates = con.execute(
            """
            select p.session_id, p.pathology_variety, p.row_count,
                   coalesce(list(h.technique_id), []) as techniques
            from session_profile as p
            left join session_high_techniques as h on p.session_id = h.session_id
            where p.villain_slug = ?
            group by p.session_id, p.pathology_variety, p.row_count
            """,
            [villain],
        ).fetchall()

        def score(row):
            session_id, variety, row_count, techniques = row
            # The left join yields [None] for a session using no high-
            # observability technique, so filter before comparing.
            present = [t for t in techniques if t is not None]
            # Prefer a session covering a high-observability technique nothing
            # else has covered yet, rarest first.
            new = [t for t in present if t not in covered]
            rarity = min((rare.index(t) for t in new), default=len(rare))
            # Ahead of all of that: a session with a real chat_turn
            # conversation always wins its villain's slot when one exists,
            # so the committed sample can actually demonstrate one rather
            # than leaving it to how the other tiebreaks happen to fall.
            # Counterstrike coverage is the next tiebreak, same reasoning -
            # a villain with no chat_turn session at all can still surface a
            # real counterstrike sequence if one of its sessions has one.
            no_chat_turns = session_id not in sessions_with_chat_turns
            no_counterstrike = session_id not in sessions_with_counterstrike
            return (no_chat_turns, no_counterstrike, -len(new), rarity, -variety, row_count)

        best = sorted(candidates, key=score)[0]
        chosen.append(best[0])
        covered.update(t for t in best[3] if t is not None)
    return chosen


def write_sample(con: duckdb.DuckDBPyConnection, data_root: Path, sessions: list[str]) -> int:
    ids = ", ".join(f"'{s}'" for s in sessions)
    total = 0
    for kind in EVENT_KINDS:
        # request/attempt/attack_run have existed since Track A and are
        # guaranteed present by the time this runs. chat_turn is new in
        # Phase 9 and genuinely optional - nothing has landed for it until a
        # real bat bot conversation has been played at least once.
        # `read_parquet` on a glob matching zero files is a hard DuckDB
        # error (the same reason `raw_events()` needs `raw_events_exist()`),
        # so skip a kind with nothing landed yet rather than crash a sample
        # regeneration that predates the first chat_turn data.
        if not list((data_root / "raw" / kind).rglob("*.parquet")):
            print(f"  (skipping {kind}: nothing landed for this kind yet)")
            continue
        relation = _raw(data_root, kind)
        partitions = con.sql(
            f"select distinct dt, hour from {relation} where session_id in ({ids})"
        ).fetchall()
        excluded = ("dt", "hour", *_EXCLUDED_COLUMNS.get(kind, ()))
        select_list = f"* exclude ({', '.join(excluded)})"
        # Every excluded column beyond dt/hour must still appear in the
        # output, as an explicit null - the point is redacting the value,
        # not dropping the column (a reader should see user_text is null,
        # not see it silently missing from the schema).
        select_list += "".join(f", null as {col}" for col in _EXCLUDED_COLUMNS.get(kind, ()))

        for dt, hour in partitions:
            directory = data_root / "raw" / kind / f"dt={dt}" / f"hour={hour}"
            directory.mkdir(parents=True, exist_ok=True)
            out = (directory / SAMPLE_NAME).as_posix()
            con.execute(
                f"""
                copy (
                    select {select_list} from {relation}
                    where session_id in ({ids}) and dt = '{dt}' and hour = '{hour}'
                ) to '{out}' (format parquet, compression snappy)
                """
            )
            rows = con.sql(f"select count(*) from read_parquet('{out}')").fetchone()[0]
            size_kb = Path(out).stat().st_size / 1024
            print(f"  {out}  rows={rows} size={size_kb:.1f}KB")
            total += rows
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("data"))
    ap.add_argument("--warehouse", type=Path, default=Path("data/warehouse.duckdb"))
    args = ap.parse_args()

    if not args.warehouse.exists():
        raise SystemExit(f"no warehouse at {args.warehouse} - run `make transform` first")

    con = duckdb.connect(str(args.warehouse), read_only=False)
    con.execute("SET TimeZone = 'UTC'")

    # Never regenerate from a corpus that is itself only the sample.
    existing = list((args.data_root / "raw").rglob("part-*.parquet"))
    if not existing:
        raise SystemExit(
            "no generated part-*.parquet found - regenerating the sample from the "
            "sample would shrink it each time. Run a corpus first."
        )

    for stale in (args.data_root / "raw").rglob(f"{SAMPLE_NAME}"):
        stale.unlink()

    sessions = choose_sessions(con, args.data_root)
    print(f"chosen sessions: {len(sessions)} (one per villain)")
    total = write_sample(con, args.data_root, sessions)
    print(f"TOTAL sample rows: {total}")
    con.close()


if __name__ == "__main__":
    main()
