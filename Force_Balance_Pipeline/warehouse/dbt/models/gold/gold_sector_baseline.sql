{{ config(materialized='table') }}

/*
  gold_sector_baseline (doc 03, "gold.sector_baseline"; doc 07 Phase 4 step 4).

  Contract:
  - Inputs: ref('silver_probe_reading') WHERE source_type = 'probe' (mandatory -- doc 03: "the most important
    test in the project" is that report-sourced data never enters here), ref('dim_sector').
  - Grain: one row per sector_id per channel (midichlorian | kyber | dark_side).
  - Materialization: table, full refresh. Rebuilt daily (doc 05, Job 2), NOT on Job 1's 15-minute cadence --
    excluded from Job 1's transform task (doc 05, Job 1 table; Q11).
  - Unique key: (sector_id, channel).
  - Columns: mean_90d, stddev_90d, sample_count, computed_at.
  - Tests required: assert_baseline_probe_only (singular test, tests/assert_baseline_probe_only.sql):
    sample_count must equal the probe-only row count for the window -- 0 report-sourced rows ever counted.
    not_null on every column.

  NOT YET IMPLEMENTED -- scaffolding only, Phase 4 planning round.
*/

select null as placeholder
where false
