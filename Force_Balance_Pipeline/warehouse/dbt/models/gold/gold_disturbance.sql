{{ config(materialized='incremental', unique_key='disturbance_id', incremental_strategy='merge') }}

/*
  gold_disturbance (doc 03, "gold.disturbance"; doc 07 Phase 4 step 6). Probe-sourced firing rules only this
  phase -- report-sourced rules are Phase 5.

  Contract:
  - Inputs: ref('gold_sector_reading').
  - Grain: one row per incident.
  - Materialization: incremental, merge on disturbance_id (macros.deterministic_id -- an onset-time + sector
    hash, not a random ULID, so a recompute produces the same id twice).
  - Unique key: disturbance_id.
  - Firing rules (probe-sourced, doc 03): imbalance_score > var('emergency_threshold'); sustained across at
    least var('sustained_scans') consecutive scans; cooldown -- no new incident for the same sector_id within
    var('cooldown_hours') hours.
  - Late-replayed onset inside an existing cooldown (doc 03, "Firing rules -- probe-sourced"): does not create a
    second incident or rewrite the existing one's detection fields -- sets cooldown_conflict = true on the
    EXISTING incident's row and stops.
  - severity = imbalance_score * (1 + LOG10(GREATEST(population, 10)) / 10).
  - Tests required: unique(disturbance_id), not_null(sector_id, detected_at, signature), assert_cooldown_respected
    (singular test) -- no two rows for the same sector_id within the cooldown window unless cooldown_conflict is
    true on the earlier one, agent_processed defaults false.

  NOT YET IMPLEMENTED -- scaffolding only, Phase 4 planning round.
*/

select null as placeholder
where false
