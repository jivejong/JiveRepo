{{ config(materialized='incremental', unique_key=['sector_id', 'scan_id'], incremental_strategy='merge') }}

/*
  gold_sector_reading (doc 03, "gold.sector_reading"; doc 07 Phase 4 step 5).

  Contract:
  - Inputs: ref('silver_probe_reading'), ref('gold_sector_baseline'), ref('dim_sector') (population, for
    civil_unrest).
  - Grain: one row per planet per scan -- no windowing (doc 03: at one scan per 15 minutes, the scan is the
    grain).
  - Materialization: incremental, merge. Recompute window: a trailing 48-hour window on every run (doc 05,
    "Incremental strategy" -- simpler and safer than a checkpoint-based lookback at this data volume), so a
    replayed row landing inside the window is recomputed, not just appended.
  - Unique key: (sector_id, scan_id).
  - Columns: midichlorian_ppm, kyber_resonance, dark_side_activity, z_midi, z_kyber, z_dark (signed),
    imbalance_score (macros.imbalance_score), signature (macros.classify_signature), channels_present.
  - Tests required: imbalance_score >= 0, channels_present between 1 and 3, accepted_values(signature, the doc 03
    signature list), not_null(sector_id, scan_id, event_time).
  - Unit-tested (models/gold/_gold__unit_tests.yml): the veiled_presence NULL grid -- a STEALTH-shaped reading
    (z_midi NULL, z_dark > 2.0, channels_present < 3) classifies veiled_presence; the same reading with z_midi
    numeric and > 1.0 does not.

  NOT YET IMPLEMENTED -- scaffolding only, Phase 4 planning round.
*/

select null as placeholder
where false
