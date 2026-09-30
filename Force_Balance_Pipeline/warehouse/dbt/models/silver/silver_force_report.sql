{{ config(materialized='incremental', unique_key='event_id', incremental_strategy='merge') }}

/*
  silver_force_report -- stub until Phase 5 (doc 07, Phase 4 step 1: "force_report (stub until Phase 5)").

  Contract:
  - Inputs: ref('stg_bronze_events').
  - Filter: `source_type = 'report'`.
  - Grain: one row per report event.
  - Materialization: incremental, merge on event_id, same insert-only/earliest-arrival-wins rule as the rest of
    silver.
  - Unique key: event_id.
  - Columns (doc 03, "silver.force_report"): description (verbatim), relevance_score, inference_model,
    inference_rationale, plus the three inferred channel values -- all populated in Phase 5. This round just
    needs the model to exist so gold.disturbance's report-sourced branch (Phase 5) has somewhere to ref() later;
    it is never read by anything built in Phase 4.
  - Tests required: none meaningful until Phase 5 fills in real columns.

  NOT YET IMPLEMENTED -- stub, Phase 4 planning round.
*/

select null as placeholder
where false
