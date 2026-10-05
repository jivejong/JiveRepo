-- Job 1 quota watch (doc 05 "Job topology"; Phase 4 Stage 3b). Run daily during the measurement week, in the
-- workspace SQL editor as yourself. Read-only -- both queries are plain SELECTs against Unity Catalog's own
-- system tables, no job or run is triggered by running this file.
--
-- Fill in the one placeholder first:
--   <force_pipeline_job_id>  numeric job id, from `databricks jobs list` or the job's URL in the workspace UI
--
-- Why system tables, not the Jobs API: `system.lakeflow.job_run_timeline` and `job_task_run_timeline` are
-- Unity-Catalog-queryable (no separate API client, token scope, or script to maintain -- consistent with every
-- other checkpoint file in this project being a plain .sql file), but they are NOT real-time. Databricks
-- documents a system-table ingestion lag (observed here: a run's `run_duration_seconds` can still read 0 for a
-- run already known SUCCEEDED minutes earlier -- not a bug in the run, a lag in the table). Don't read a day's
-- numbers as final until at least a few hours after that day ends.
--
-- Free Edition quota context (doc 05, "Job 1 timing"): no daily/monthly serverless compute quota number is
-- published anywhere found; the only known signal if one is ever hit is compute shutting down for the rest of
-- the day or month -- and even that signal is unreliable: exhaustion has been observed to be SILENT (no run
-- created at all once the day's budget is spent, not a failed run with a visible error -- see docs/ENGINEERING-LOG.md,
-- "Stage 4c", and the new open item on monitoring needing a freshness/gap check, not a failure count, for exactly
-- this reason). These two queries are how you'd notice a day with far fewer runs than the 24 an hourly cadence
-- implies, or a sudden run of FAILED/SKIPPED results -- neither is a direct view of the quota itself, and a day
-- with ZERO runs after some point won't show as FAILED/SKIPPED at all, only as missing rows.
--
-- Cadence changed 15 -> 30 min, Stage 4b, then 30 -> 60 min, Stage 4c (docs/ENGINEERING-LOG.md, "Stage 3d",
-- "Stage 4a"): the Free Edition quota was exhausted on two separate days under the original 15-minute (96-run)
-- cadence; halving to 30 minutes (48-run) was itself measured for 3 days by THIS file and still exhausted every
-- day (2026-10-02 through 2026-10-04) -- that 3-day measurement is what moved Job 1 to hourly (24-run). This
-- file's own expected-run count and baseline below are updated to the new 24-run cadence; this is what the next
-- measurement week watches for.

-- (quota-1) per-day run count and outcome, Job 1 only
SELECT
    date(period_start_time) AS day,
    count(*) AS runs,
    count_if(result_state = 'SUCCEEDED') AS succeeded,
    count_if(result_state = 'FAILED') AS failed,
    count_if(result_state = 'SKIPPED') AS skipped,
    count_if(result_state NOT IN ('SUCCEEDED', 'FAILED', 'SKIPPED')) AS other_result_state  -- CANCELED, TIMED_OUT, etc. -- catches anything the two lines above don't name
FROM system.lakeflow.job_run_timeline
WHERE job_id = '<force_pipeline_job_id>'
GROUP BY 1
ORDER BY 1;

-- (quota-2) per-day total task execution time, Job 1 only -- sum across both tasks (ingest_bronze, transform),
-- every run that day. Derived baseline (Phase 4 Stage 3a's one manual run, 2026-09-30): ingest_bronze 83.6s +
-- transform 187.9s = 271.5s, ~4.5 minutes per run -- about 7.5% of an hourly cycle's own wall-clock budget
-- (was ~15% of the 30-minute cycle, ~30% of the original 15-minute cycle), with more room to spare before the
-- cadence would start overlapping itself than at either earlier cadence. Compare each day's
-- total_task_execution_minutes / runs against this ~4.5 baseline; a sustained rise is the signal to watch for,
-- not a single day's number -- the Stage 4b/4c quota-watch measurements themselves found actual per-run transform
-- time running 335-378s (5.6-6.3 min), noticeably above this original single-run baseline; worth re-deriving the
-- baseline from that larger sample rather than the one manual run, in a future round.
SELECT
    date(period_start_time) AS day,
    count(*) AS task_runs,
    sum(execution_duration_seconds) AS total_task_execution_seconds,
    round(sum(execution_duration_seconds) / 60.0, 2) AS total_task_execution_minutes,
    round(avg(execution_duration_seconds), 1) AS avg_task_execution_seconds
FROM system.lakeflow.job_task_run_timeline
WHERE job_id = '<force_pipeline_job_id>'
GROUP BY 1
ORDER BY 1;
