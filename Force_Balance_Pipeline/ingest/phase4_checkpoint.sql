-- Phase 4 checkpoint queries (doc 07, Phase 4; the plan file's own "Phase 4 checkpoint" table). Run in the workspace SQL
-- editor as yourself. Read-only throughout. p4-0 through p4-7 use known, already-verified values straight from this
-- phase's own work (Stage 1/2/3a real-data checks) -- they're checks you can run and compare against a fixed
-- expectation today, not placeholders. p4-8 through p4-12 need a live action (a scheduled replay landing, a
-- control-topic injection, the fault-injection period) that hasn't happened yet at the time of writing and so still
-- carry placeholders.
--
-- Remaining placeholders, fill in when you run that query:
--   <baseline_computed_at>                                 gold_sector_baseline's own computed_at, from the first
--                                                          query of p4-1 itself
--   <replay_window_start_utc>, <replay_window_end_utc>   the next scheduled DISCONNECTED window's own bounds,
--                                                          from mode_transitions.jsonl on the Pi (p4-8)
--   <inject_ts_utc>, <inject_sector>                      the control-topic injection's own timestamp and target (p4-9, p4-10)
--   <fault_seed_table>                                     the fault-injection seed's full table name, once
--                                                          scripts/faultlog_to_seed.py and `dbt seed` have built it
--                                                          (doc 05, "Fault injection still at --fault-rate 0" open
--                                                          item) -- not available yet (p4-12)

-- (p4-0) Job 1 green, informational -- not a SQL check, the job run output itself is the evidence. Recorded here so
--        it's in the same file as everything else: force_pipeline's first manual run (2026-09-30, Stage 3a) --
--        ingest_bronze SUCCESS 83.6s, transform SUCCESS 187.9s, commit d923a7c8. ingest/job1_quota_watch.sql is the
--        ongoing per-day version of this check once Job 1 is on its own schedule (it is, as of Stage 3b).

-- (p4-1) Baseline probe-only: the project's own most important test (assert_baseline_probe_only, already in the
--        dbt suite) says this is always 0 -- this is the same check run by hand. silver_probe_reading has no
--        source_type column to filter on (it's probe-only by construction in Phase 4 -- silver_force_report is a
--        separate, still-empty stub model); the real guarantee is sample_count matching the probe-only count for
--        the model's own window, which is what both this query and the singular test check.
--
-- Fixed, Stage 3c: comparing against the CURRENT silver_probe_reading (plain event_time filtering, no further
-- qualification) only holds right after a fresh gold_sector_baseline build. gold_sector_baseline is a `table`,
-- deliberately excluded from Job 1 and rebuilt only daily (doc 05) -- its stored computed_at and sample_count are
-- frozen at the last build, while silver_probe_reading keeps growing underneath it (both from Job 1's own
-- scheduled cadence and, independently, from buffered/replayed batches that can land in bronze well before
-- silver actually incorporates them -- checked directly: Stage 3b found 49 "mismatches" that way, 43 of them
-- is_replayed=true rows whose arrival predated the gold build by hours, yet weren't in silver_probe_reading as a
-- TABLE until some later, unrelated dbt run). Neither event_time nor arrival_ts can tell "was this row physically
-- in the table as of computed_at" -- only Databricks' own Delta time travel (`TIMESTAMP AS OF`) answers that
-- question exactly, because it reconstructs the table's real historical state rather than approximating it from
-- any column. Run these two in order:

SELECT max(computed_at) AS computed_at FROM force.gold.gold_sector_baseline;
-- Paste the result into '<baseline_computed_at>' below (both occurrences must match).

SELECT b.sector_id, b.channel, b.sample_count,
       (SELECT count(*) FROM force.silver.silver_probe_reading TIMESTAMP AS OF '<baseline_computed_at>' r
        WHERE r.sector_id = b.sector_id AND r.event_time >= dateadd(day, -90, b.computed_at) AND r.event_time < b.computed_at
       ) AS probe_only_count_as_of_build
FROM force.gold.gold_sector_baseline b
WHERE b.sample_count != (SELECT count(*) FROM force.silver.silver_probe_reading TIMESTAMP AS OF '<baseline_computed_at>' r
                          WHERE r.sector_id = b.sector_id AND r.event_time >= dateadd(day, -90, b.computed_at) AND r.event_time < b.computed_at);
-- Expected: 0 rows, at any time, since this reconstructs the exact table state the baseline's own build saw.
-- Re-verified live, 2026-10-01 (Stage 3c), ~11h after the build this time-travels to: 0 mismatches, where the
-- plain-event_time version above still showed 49. Any mismatch here (not just elapsed time since the build) would
-- be a genuine finding, not expected staleness.

-- (p4-2) Dedup, the known 13-vs-47 fixture: the 2026-09-27 23:45Z scan had 13 ids land early (the partial file),
--        the other 47 only via the later drain. A bare "=" against the scan boundary misses most rows -- they
--        carry sub-second synthetic jitter (same finding as p4-7, below) -- use a window.
SELECT is_replayed, count(*) AS n
FROM force.silver.silver_probe_reading
WHERE event_time >= TIMESTAMP '2026-09-27T23:44:55Z' AND event_time < TIMESTAMP '2026-09-27T23:45:10Z'
GROUP BY is_replayed ORDER BY is_replayed;
-- Expected: false -> 13, true -> 47 (60 total). Verified 2026-09-30/10-01 (Stage 1 and again at Stage 3b): exactly this.

-- (p4-3) Replay flag, the 2026-09-29 unplanned outage (U1/U2 in PHASE3-RESULTS.md): 300 rows buffered, 180 over the
--        1,800 s cutoff (the 18:15/18:30/18:45Z scans; the 19:00Z and 19:15Z scans clear at ~1,788s and ~888s, under
--        the cutoff -- see the dated correction in PHASE3-RESULTS.md, the 240-vs-180 figure).
SELECT count(*) AS n, count_if(is_replayed) AS replayed
FROM force.silver.silver_probe_reading
WHERE event_time >= TIMESTAMP '2026-09-29T18:14:16.192Z' AND event_time < TIMESTAMP '2026-09-29T19:29:47.517Z';
-- Expected: n=300, replayed=180. Verified 2026-09-30 (Stage 1, and again at Stage 1 re-check after the full-refresh).

-- (p4-4) Replay flag, the Phase 3 checkpoint outage: 180 rows buffered, only the oldest (14:30Z) scan over the cutoff.
SELECT count(*) AS n, count_if(is_replayed) AS replayed
FROM force.silver.silver_probe_reading
WHERE event_time >= TIMESTAMP '2026-09-29T14:19:05.866Z' AND event_time < TIMESTAMP '2026-09-29T15:01:57.431Z';
-- Expected: n=180, replayed=60.

-- (p4-5) The 4 injected backfill emergencies, on the manifest's own days (edge/backfill_manifest.json), each with
--        sustained_scans >= 2 and the correct signature.
SELECT sector_id, detected_at, signature, sustained_scans, round(imbalance_score, 3) AS peak_score
FROM force.gold.gold_disturbance
WHERE (sector_id, date(detected_at)) IN (
  ('tatooine', DATE '2026-07-18'), ('dantooine', DATE '2026-08-05'),
  ('kamino', DATE '2026-08-25'), ('coruscant', DATE '2026-09-15')
)
ORDER BY detected_at;
-- Expected: 4 rows -- tatooine/sith_presence, dantooine/nexus_awakening, kamino/force_drain, coruscant/civil_unrest,
-- every sustained_scans >= 2. Verified 2026-09-30 (Stage 2): 6.777 / 6.297 / 6.297 / 7.284 peak scores, sustained_scans
-- 5/4/5/4 -- all match the manifest's own recorded composites closely.

-- (p4-6) The slow riser (mon_cala): last backfill day's mean imbalance_score stays under the anomaly threshold, and
--        it never fires a disturbance.
SELECT round(avg(imbalance_score), 3) AS last_day_mean_score, count(*) AS n_rows
FROM force.gold.gold_sector_reading
WHERE sector_id = 'mon_cala'
  AND event_time >= TIMESTAMP '2026-09-25T14:15:00Z' AND event_time < TIMESTAMP '2026-09-26T14:15:00Z';
SELECT count(*) AS mon_cala_disturbances FROM force.gold.gold_disturbance WHERE sector_id = 'mon_cala';
-- Expected: mean < 4.0, 0 disturbances. Verified 2026-09-30 (Stage 2): mean 2.52 (96 rows, one full day), 0 disturbances.

-- (p4-7) STEALTH: channels_present = 1, a non-NULL score, and 0 in rejects, for the known 2026-09-28 19:00Z scan.
-- Equality against a bare timestamp misses most of the 60 rows -- they carry sub-second synthetic jitter (found
-- 2026-09-30, Stage 2); use a window, not "=".
SELECT count(*) AS n_rows, min(channels_present) AS min_cp, max(channels_present) AS max_cp,
       count_if(imbalance_score IS NULL) AS n_null_score
FROM force.gold.gold_sector_reading
WHERE sector_id IN (SELECT sector_id FROM force.silver.silver_probe_reading
                     WHERE event_time >= TIMESTAMP '2026-09-28T18:59:55Z' AND event_time < TIMESTAMP '2026-09-28T19:00:10Z')
  AND event_time >= TIMESTAMP '2026-09-28T18:59:55Z' AND event_time < TIMESTAMP '2026-09-28T19:00:10Z';
SELECT count(*) AS stealth_rows_in_rejects FROM force.silver.silver_rejects
WHERE event_id IN (SELECT event_id FROM force.silver.silver_probe_reading
                    WHERE event_time >= TIMESTAMP '2026-09-28T18:59:55Z' AND event_time < TIMESTAMP '2026-09-28T19:00:10Z');
-- Expected: 60 rows, channels_present=1 on all, 0 null scores, 0 in rejects. Verified 2026-09-30 (Stage 2): exactly this.

-- (p4-8) Replay recompute: after the next scheduled DISCONNECTED window's buffer replays, gold.sector_reading still
--        has exactly one row per event_id (no duplicates from the recompute), and running dbt build again changes
--        no row counts anywhere. Needs <replay_window_start_utc>/<replay_window_end_utc> from mode_transitions.jsonl
--        once a scheduled outage has actually happened and replayed since Job 1 went live.
--        The upper bound pads +10s past <replay_window_end_utc> itself, not the literal boundary -- found live,
--        2026-10-05/06: a scan's own 60 rows spread ~3s of sub-second jitter around their nominal event_time, so a
--        literal "< <replay_window_end_utc>" clips most of the LAST scan's rows if that boundary lands exactly on
--        one. Confirmed against the real backlog figure both times (1560, then 300) only after padding.
SELECT count(*) AS n, count(DISTINCT event_id) AS distinct_event_ids
FROM force.gold.gold_sector_reading
WHERE event_time >= TIMESTAMP '<replay_window_start_utc>'
  AND event_time < dateadd(second, 10, TIMESTAMP '<replay_window_end_utc>');
-- Expected: n = distinct_event_ids (no duplicates). Compare bronze/silver/gold row counts before and after the NEXT
-- dbt build runs (ingest/job1_quota_watch.sql's own per-day run log has the run timestamps); expect zero movement.

-- (p4-9) sith_presence via the control-topic injector: a gold.disturbance row for the injected sector with the
--        right signature, sustained_scans >= 2, detected_at from the FIRST Job 1 run at or after the ONSET's
--        second qualifying scan (the first qualifying scan is found empirically -- doc 07's C3 bullet,
--        "Clarification, Phase 4 Stage 3d") -- not of <inject_ts_utc> itself, and not guaranteed to be prompt:
--        detected_at is whichever scan was LAST qualifying as of whenever gold_disturbance actually rebuilt
--        (gold_disturbance.sql's own `max(event_time) as detected_at`), so a missed Job 1 cycle -- or simply
--        Job 1's own hourly cadence (Stage 4c; was 30 (Stage 4b), then 15) -- can push both detected_at and sustained_scans past
--        the two-scan healthy-pipeline minimum even with no outage at all.
SELECT sector_id, detected_at, signature, sustained_scans
FROM force.gold.gold_disturbance
WHERE sector_id = '<inject_sector>' AND detected_at >= TIMESTAMP '<inject_ts_utc>'
ORDER BY detected_at LIMIT 1;
-- Expected: 1 row, signature = 'sith_presence', sustained_scans >= 2, detected_at from the first Job 1 run at or
-- after onset's second qualifying scan, under healthy Job 1 operation (not a fixed offset from <inject_ts_utc>,
-- and not reliably "within 2 scans" at the current hourly cadence -- see above).
--
-- Real result, 2026-10-01 (Stage 3d): naboo, injected 02:59Z (T = 03:00Z boundary). Onset (first scan >= 5.75)
-- was 03:15Z (T+15, score 6.729 -- inside the default 2-scan `ramp`, not only once `hold` began; onset is found
-- empirically, per sector, not assumed from ramp/hold scan counts). A healthy pipeline would have shown
-- detected_at ~03:30Z, sustained_scans=2. The actual row: detected_at = 2026-10-01T04:15:01.650Z,
-- sustained_scans = 5 -- 75 minutes after T, not ~30 -- because Job 1's 03:18Z and 03:33Z runs both failed
-- (RESOURCE_EXHAUSTED, docs/ENGINEERING-LOG.md "Stage 3d"), so no build incorporated the injection until a
-- manual catch-up run well after the signature had already decayed back down (04:30Z, score 3.867). The row is
-- correct (signature, sustained_scans, the onset's own scan_id) -- the pass/fail criterion held; only the
-- wall-clock latency was abnormal, from infrastructure, not detection logic.

-- (p4-10) Cooldown: two control-topic injections on the same sector 30 minutes apart produce one incident row, not
--        two -- the singular test (assert_cooldown_respected) already covers this structurally; this is the same
--        check against a real, live-injected pair instead of a dbt fixture.
--        No upper bound on detected_at (removed, found live, 2026-10-05/06, same session as the p4-8 padding fix
--        above): a delayed or re-attempted run can push detected_at past a fixed "+2h" window and produce a false
--        "0 incidents" that is really just a timing artifact, not the cooldown failing. doc 05's own copy of this
--        query (C4 runbook) carries the same fix.
SELECT count(*) AS incidents_in_window
FROM force.gold.gold_disturbance
WHERE sector_id = '<inject_sector>'
  AND detected_at >= TIMESTAMP '<inject_ts_utc>';
-- Expected: 1.

-- (p4-11) Housekeeping: silver_probe_event's count should equal bronze's own payload:kind-tagged row count, with
--        nothing landing in rejects.
SELECT
  (SELECT count(*) FROM force.silver.silver_probe_event) AS silver_probe_event_rows,
  (SELECT count(*) FROM force.bronze.events WHERE payload:kind::string IS NOT NULL) AS bronze_housekeeping_rows,
  (SELECT count(*) FROM force.silver.silver_rejects r JOIN force.bronze.events b ON b.event_id = r.event_id
   WHERE b.payload:kind::string IS NOT NULL) AS housekeeping_rows_in_rejects;
-- Expected: the first two equal, the third 0. Verified 2026-09-30: 0 = 0 (no housekeeping events in the real data
-- yet -- the buffer has never overflowed), 0 in rejects. Already covered structurally by a unit test (doc 03 (e));
-- this is the live-data version of the same check.

-- (p4-12) Fault reconciliation (last, per doc 07's own ordering): rejects match the fault-injection log exactly,
--        reason for reason, over the fault period. NOT YET AVAILABLE -- fault injection is still at --fault-rate 0
--        (doc 05 open item), so the seed named below (<fault_seed_table>, built by scripts/faultlog_to_seed.py
--        and `dbt seed` once a real fault_injection.jsonl exists -- doc 04, "Fault injection") doesn't exist yet.
--        Its two columns, event_id and expected_reject_reason, are exactly what this full outer join needs.
SELECT
  count_if(f.event_id IS NOT NULL AND r.event_id IS NOT NULL) AS matched,
  count_if(f.event_id IS NOT NULL AND r.event_id IS NULL) AS fault_not_rejected,
  count_if(f.event_id IS NULL AND r.event_id IS NOT NULL) AS rejected_not_a_logged_fault,
  count_if(f.event_id IS NOT NULL AND r.event_id IS NOT NULL AND f.expected_reject_reason <> r.reject_reason) AS reason_mismatches
FROM <fault_seed_table> f
FULL OUTER JOIN force.silver.silver_rejects r ON r.event_id = f.event_id;
-- Expected, once the seed exists: fault_not_rejected = 0, reason_mismatches = 0. rejected_not_a_logged_fault > 0
-- is NOT necessarily a problem -- silver_rejects also catches real, non-injected bad data (doc 03's own 5 reject
-- reasons exist independently of fault injection); only fault_not_rejected and reason_mismatches are the actual
-- reconciliation claim doc 04 makes ("the count of rejects must equal the count in this log" -- read as "every
-- logged fault appears in rejects with its expected reason," not "every reject came from a logged fault").
