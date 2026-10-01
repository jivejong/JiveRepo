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
-- the day or month. These two queries are how you'd notice that happening -- a day with far fewer runs than the
-- 96 a 15-minute cadence implies, or a sudden run of FAILED/SKIPPED results -- not a way to see the quota itself.

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
-- transform 187.9s = 271.5s, ~4.5 minutes per 15-minute cycle -- about 30% of the cycle's own wall-clock budget,
-- with room to spare before a 15-minute cadence would start overlapping itself. Compare each day's
-- total_task_execution_minutes / runs against this ~4.5 baseline; a sustained rise is the signal to watch for,
-- not a single day's number.
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
