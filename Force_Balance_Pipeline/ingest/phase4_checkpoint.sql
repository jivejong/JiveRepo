-- Phase 4 checkpoint queries (doc 07, Phase 4; the plan file's own "Phase 4 checkpoint" table). Run in the workspace SQL
-- editor as yourself. Read-only throughout. p4-0 through p4-7 use known, already-verified values straight from this
-- phase's own work (Stage 1/2/3a real-data checks) -- they're checks you can run and compare against a fixed
-- expectation today, not placeholders. p4-8 through p4-12 need a live action (a scheduled replay landing, a
-- control-topic injection, the fault-injection period) that hasn't happened yet at the time of writing and so still
-- carry placeholders.
--
-- Remaining placeholders, fill in when you run that query:
--   <replay_window_start_utc>, <replay_window_end_utc>   the next scheduled DISCONNECTED window's own bounds,
--                                                          from mode_transitions.jsonl on the Pi (p4-8)
--   <inject_ts_utc>, <inject_sector>                      the control-topic injection's own timestamp and target (p4-9, p4-10)
--   <fault_period_start_utc>, <fault_period_end_utc>       once fault injection is back on (doc 05, "Fault injection
--                                                          still at --fault-rate 0" open item) -- not available yet (p4-12)

-- (p4-0) Job 1 green, informational -- not a SQL check, the job run output itself is the evidence. Recorded here so
--        it's in the same file as everything else: force_pipeline's first manual run (2026-09-30, Stage 3a) --
--        ingest_bronze SUCCESS 83.6s, transform SUCCESS 187.9s, commit d923a7c8. ingest/job1_quota_watch.sql is the
--        ongoing per-day version of this check once Job 1 is on its own schedule (it is, as of Stage 3b).

-- (p4-1) Baseline probe-only: the project's own most important test (assert_baseline_probe_only, already in the
--        dbt suite) says this is always 0 -- this is the same check run by hand. silver_probe_reading has no
--        source_type column to filter on (it's probe-only by construction in Phase 4 -- silver_force_report is a
--        separate, still-empty stub model); the real guarantee is sample_count matching the probe-only count for
--        the model's own window, which is what both this query and the singular test check.
SELECT b.sector_id, b.channel, b.sample_count,
       (SELECT count(*) FROM force.silver.silver_probe_reading r
        WHERE r.sector_id = b.sector_id AND r.event_time >= dateadd(day, -90, b.computed_at) AND r.event_time < b.computed_at
       ) AS probe_only_count_for_window
FROM force.gold.gold_sector_baseline b
WHERE b.sample_count != (SELECT count(*) FROM force.silver.silver_probe_reading r
                          WHERE r.sector_id = b.sector_id AND r.event_time >= dateadd(day, -90, b.computed_at) AND r.event_time < b.computed_at);
-- Expected: 0 rows -- but only meaningful run right after a fresh gold_sector_baseline build (its own, or
-- Job 2's daily one), not standalone hours later. Verified 2026-09-30 immediately after a build (Stage 2): 0
-- mismatches, sample_count = 8455. Re-run standalone the same day (Stage 3b, ~9.5h after that build, with Job 1
-- live and running in between): 49 "mismatches" -- NOT a probe-only violation. gold_sector_baseline is a `table`,
-- deliberately excluded from Job 1 and rebuilt only daily (doc 05), so its stored computed_at and sample_count
-- are frozen at the last build while silver_probe_reading keeps growing underneath it; checked directly (one
-- sector): 43 of the 49 extra rows in that sector's window are is_replayed = true -- a real buffered/replayed
-- batch landed sometime after the baseline's last build, which is exactly the kind of event the baseline won't
-- see again until Job 2 next runs. A persistent, non-shrinking gap day over day would be the real signal to
-- investigate; a same-day gap that tracks Job 1's own uptime since the last baseline build is expected staleness.

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
SELECT count(*) AS n, count(DISTINCT event_id) AS distinct_event_ids
FROM force.gold.gold_sector_reading
WHERE event_time >= TIMESTAMP '<replay_window_start_utc>' AND event_time < TIMESTAMP '<replay_window_end_utc>';
-- Expected: n = distinct_event_ids (no duplicates). Compare bronze/silver/gold row counts before and after the NEXT
-- dbt build runs (ingest/job1_quota_watch.sql's own per-day run log has the run timestamps); expect zero movement.

-- (p4-9) sith_presence via the control-topic injector: within 2 scans, a gold.disturbance row for the injected
--        sector with the right signature.
SELECT sector_id, detected_at, signature, sustained_scans
FROM force.gold.gold_disturbance
WHERE sector_id = '<inject_sector>' AND detected_at >= TIMESTAMP '<inject_ts_utc>'
ORDER BY detected_at LIMIT 1;
-- Expected: 1 row, signature = 'sith_presence', sustained_scans >= 2, detected_at within 2 scans of <inject_ts_utc>.

-- (p4-10) Cooldown: two control-topic injections on the same sector 30 minutes apart produce one incident row, not
--        two -- the singular test (assert_cooldown_respected) already covers this structurally; this is the same
--        check against a real, live-injected pair instead of a dbt fixture.
SELECT count(*) AS incidents_in_window
FROM force.gold.gold_disturbance
WHERE sector_id = '<inject_sector>'
  AND detected_at >= TIMESTAMP '<inject_ts_utc>' AND detected_at < dateadd(hour, 2, TIMESTAMP '<inject_ts_utc>');
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
--        (doc 05 open item); the frozen-seed design this needs (docs/ENGINEERING-LOG.md, decision table (i)) isn't
--        built yet either. Placeholder, not a guess at numbers that don't exist.
SELECT
  (SELECT count(*) FROM force.silver.silver_rejects r
   WHERE r.event_id IN (SELECT event_id FROM force.silver.silver_probe_reading)  -- placeholder shape only
     AND EXISTS (SELECT 1 FROM force.bronze.events b WHERE b.event_id = r.event_id
                 AND b.event_time >= TIMESTAMP '<fault_period_start_utc>' AND b.event_time < TIMESTAMP '<fault_period_end_utc>')
  ) AS rejects_in_fault_period;
-- Expected, once the fault seed exists: rejects_in_fault_period = the seed's own row count for that period, 0 reason
-- mismatches on a full outer join against it (doc 05, decision table (i): "Frozen seed (seeds/fault_injection_<period>.csv)").
