-- Phase 8 outage check (doc 07, Phase 8; engineering log, Phase 8 Round 7): did the 2026-11-03 to 2026-11-16 stop of Job 1 lose or duplicate
-- any probe event? Job 1 made no scheduled run from 2026-11-03 16:03Z until 2026-11-17 02:03Z (a Free Edition stop; the bridge and the
-- probe kept running), so bronze should hold every scan the probe took, ingested late. Run in the workspace SQL editor as yourself, or with
-- the scratch run_sql_file.py helper. Read-only throughout: every statement is a SELECT or a WITH ... SELECT. No placeholders.
--
-- Window: probe events with event_time from 2026-11-02 00:00:00Z (inclusive) to 2026-11-17 00:00:00Z (exclusive), so 2026-11-02 is a
-- normal day before the stop and 2026-11-03 is the day it began. Live readings only: source_type 'probe', not synthetic, and no
-- payload.kind (housekeeping events are not readings; p3-9 in phase3_checkpoint.sql counts those). A scan is the 60 readings that share a
-- scan_id; its time is the earliest event_time among them (the 60 rows spread about 3 s of jitter around the quarter hour).
-- The logic follows phase3_checkpoint.sql: p3-4 (scans and gaps) and p3-5 (duplicates), and phase4_checkpoint.sql p4-8 (one row per event_id).
--
-- (p8-1) per UTC day: scans and events landed against what the cadence implies. DERIVED expectation: the probe scans every 15 minutes, so
--        96 scans a day, and each scan is 60 planets, so 5760 events a day (the expected_* columns are these constants, not a query).
--        STEALTH is an hourly dark-channel-only scan, so a day with STEALTH hours has fewer scans: stealth_scans shows how many.
--        Expect scans_vs_expected = 0 and events_vs_expected = 0 on every day without STEALTH, and scans_not_60 = 0 everywhere.
WITH scans AS (
  SELECT scan_id, min(event_time) AS t, count(*) AS n, max(CASE WHEN mode = 'STEALTH' THEN 1 ELSE 0 END) AS stealth
  FROM force.bronze.events
  WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL
    AND event_time >= TIMESTAMP '2026-11-02T00:00:00Z' AND event_time < TIMESTAMP '2026-11-17T00:00:00Z'
  GROUP BY scan_id
)
SELECT CAST(t AS DATE) AS day_utc, count(*) AS scans_landed, sum(n) AS events_landed, sum(stealth) AS stealth_scans,
       96 AS expected_scans, 5760 AS expected_events,
       count(*) - 96 AS scans_vs_expected, sum(n) - 5760 AS events_vs_expected, count_if(n <> 60) AS scans_not_60
FROM scans
GROUP BY CAST(t AS DATE)
ORDER BY day_utc;

-- (p8-2) duplicates: event_id values with more than one bronze row in the window (bronze keeps duplicates; silver deduplicates on event_id).
--        Expect duplicate_event_ids = 0, extra_rows = 0 and an empty examples list. Otherwise examples holds up to 5 of the ids.
WITH dups AS (
  SELECT event_id, count(*) AS copies
  FROM force.bronze.events
  WHERE source_type = 'probe' AND NOT is_synthetic
    AND event_time >= TIMESTAMP '2026-11-02T00:00:00Z' AND event_time < TIMESTAMP '2026-11-17T00:00:00Z'
  GROUP BY event_id
  HAVING count(*) > 1
)
SELECT count(*) AS duplicate_event_ids, coalesce(sum(copies) - count(*), 0) AS extra_rows, slice(collect_list(event_id), 1, 5) AS examples
FROM dups;

-- (p8-3) gaps: the interval between each scan and the one before it. Shows the largest interval in the window (is_largest) and every
--        interval over 20 minutes, with nothing excluded (an hourly STEALTH scan would show here as 60). A normal interval is 15 minutes,
--        give or take the jitter. Expect no row except the largest, and the largest close to 15.
WITH scans AS (
  SELECT scan_id, min(event_time) AS t
  FROM force.bronze.events
  WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL
    AND event_time >= TIMESTAMP '2026-11-02T00:00:00Z' AND event_time < TIMESTAMP '2026-11-17T00:00:00Z'
  GROUP BY scan_id
), gaps AS (
  SELECT lag(t) OVER (ORDER BY t) AS gap_start, t AS gap_end,
         round((unix_timestamp(t) - unix_timestamp(lag(t) OVER (ORDER BY t))) / 60, 2) AS minutes
  FROM scans
), ranked AS (
  SELECT gap_start, gap_end, minutes, row_number() OVER (ORDER BY minutes DESC) AS rank_by_length
  FROM gaps
  WHERE gap_start IS NOT NULL
)
SELECT gap_start, gap_end, minutes, rank_by_length = 1 AS is_largest
FROM ranked
WHERE minutes > 20 OR rank_by_length = 1
ORDER BY gap_start;

-- (p8-4) arrival, per UTC day of event time: how long after the event the row was ingested into bronze (_ingest_ts, when Job 1's notebook
--        processed the file) and, separately, how long after it the file landed in the volume (_file_modified_ts, when the bridge wrote it;
--        null on rows ingested before that column existed, so those are ignored in the landed_* columns). Hours. first_ingest is the
--        earliest ingest time among the day's rows. Expect: landed_* small on every day (the bridge kept landing files during the stop),
--        and for the days of the stop (2026-11-03 from 16:xx, through 2026-11-16) first_ingest at or after 2026-11-17 02:03Z and
--        ingest_max_h up to about 13.4 days (about 322 hours): the events were ingested late, none dropped.
SELECT CAST(event_time AS DATE) AS day_utc, count(*) AS events,
       min(_ingest_ts) AS first_ingest,
       round(min((unix_timestamp(_ingest_ts) - unix_timestamp(event_time)) / 3600), 2) AS ingest_min_h,
       round(percentile_approx((unix_timestamp(_ingest_ts) - unix_timestamp(event_time)) / 3600, 0.5), 2) AS ingest_median_h,
       round(max((unix_timestamp(_ingest_ts) - unix_timestamp(event_time)) / 3600), 2) AS ingest_max_h,
       round(min((unix_timestamp(_file_modified_ts) - unix_timestamp(event_time)) / 3600), 2) AS landed_min_h,
       round(percentile_approx((unix_timestamp(_file_modified_ts) - unix_timestamp(event_time)) / 3600, 0.5), 2) AS landed_median_h,
       round(max((unix_timestamp(_file_modified_ts) - unix_timestamp(event_time)) / 3600), 2) AS landed_max_h
FROM force.bronze.events
WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL
  AND event_time >= TIMESTAMP '2026-11-02T00:00:00Z' AND event_time < TIMESTAMP '2026-11-17T00:00:00Z'
GROUP BY CAST(event_time AS DATE)
ORDER BY day_utc;
