{{ config(materialized='incremental', unique_key='disturbance_id', incremental_strategy='merge', merge_exclude_columns='agent_processed') }}

{% raw %}
/*
  gold_disturbance (doc 03, "gold.disturbance"; doc 07 Phase 4 step 6). Probe-sourced firing rules only this
  phase -- report-sourced rules are Phase 5.

  DBT CONCEPTS USED HERE:
  - merge_exclude_columns='agent_processed' (a dbt-databricks incremental config, not core dbt): tells the
    generated MERGE's WHEN MATCHED clause to update every column except this one, no matter what this
    model's own SELECT computes for it. Belt-and-suspenders with the SELECT-side carry-forward below --
    either one alone would keep agent_processed from being overwritten by a recompute; both together mean a
    future bug in the SELECT logic can't silently start overwriting it.
  - "Gaps and islands" (two ROW_NUMBER() calls, one over every row and one over only the qualifying rows,
    subtracted): the standard SQL trick for finding runs of consecutive rows that share a property -- here,
    imbalance_score over the emergency threshold, per sector, in scan order. Where the two numbering schemes
    stay in step (a run of consecutive qualifying rows), the difference between them is constant, which is
    what group-by picks out as one "island."
  - Full rescan every run, not an incremental window (doc 05's own "simpler and safer... optimize only if it
    becomes slow" reasoning, applied here rather than a windowed recompute): a run can span an arbitrary
    number of scans and an arbitrary amount of wall-clock time (STEALTH's hourly cadence, doc 02, does not
    shrink "2 consecutive scans" to 30 minutes the way CONNECTED's does), so scoping this model's own input to
    a time window risks cutting a real run in half. gold_sector_reading is small enough at this data volume
    (roughly one row per planet per 15 minutes) that rescanning all of it every build is cheap; this is a
    documented, deliberate choice to keep correctness over incremental cleverness, not an oversight.

  "Consecutive" -- OPEN QUESTION, doc 03 does not say, decided here: consecutive ROWS for a sector in
  gold_sector_reading, ordered by event_time -- not consecutive 15-minute clock slots. This is what makes
  "2 consecutive scans" work unchanged for a STEALTH sector (hourly cadence, so "2 consecutive scans" spans
  2 hours of wall-clock time, not 30 minutes) and across a gap where a scan never landed at all (a missing row
  is simply absent from the ordering, not a below-threshold row that would break a run) -- but note it means a
  genuine below-threshold READING breaks a run even if it is immediately followed by another above-threshold
  one; only a true GAP (no row at all) is invisible to this definition. Flagged for confirmation, not asserted
  as doc 03's own intent.

  Cooldown -- RESOLVED, 2026-09-30, via a recursive CTE (`walked`, below): each run is now compared against
  the last ACCEPTED incident's detected_at, carried forward through any chain of intervening suppressed runs
  -- not against the immediately preceding run's own detected_at (a LAG(), the original implementation, which
  could make a following run's cooldown window too LONG when an intervening suppressed run was itself a long
  one, and so incorrectly SUPPRESS a run genuinely clear of the true originating incident's cooldown; verified
  with a 3-run fixture, 2026-09-30, before this fix). WITH RECURSIVE is supported on this project's serverless
  SQL warehouse (confirmed live, DBSQL 2026.36) -- an earlier version of this comment assumed otherwise without
  checking. `basis_detected_at` on each row of `walked` records which accepted incident's detected_at a run was
  actually tested against (NULL for the sector's first run, which is never suppressed).

  `detected_at` semantics -- decided Stage 4a, doc 03 (current behavior is intended, not an oversight): it is the
  `event_time` of the run's latest qualifying scan at build time, so it advances on every rebuild while the incident is
  still ongoing. `onset_scan_id` (-> `scan_id` in the final SELECT) is unaffected -- always the run's onset scan, never
  moving -- so the two columns deliberately refer to different scans in the same row. The 2-hour cooldown (below) is
  measured from `detected_at`, i.e. from the end of the qualifying run so far, not from its onset. A fixed `confirmed_at`
  (stamped once, at the run's second qualifying scan) was considered and is deferred to Phase 6 -- a schema change, not a
  correction to this behavior.

  `cooldown_conflict` -- "definition B", decided Stage 4a (doc 03's own wording describes a replayed onset
  specifically; the PRE-Stage-4a implementation set this flag for ANY suppressed run, replayed or not -- a
  genuine doc/code mismatch, now resolved in the doc's favor). Set true on an accepted incident only when BOTH:
  (1) a later run was suppressed by this incident's cooldown (its `basis_detected_at` matches this row's own
  `detected_at` -- correct even across a chain of several consecutive suppressed runs, since they all carry the
  same original `basis_detected_at` forward unchanged), AND (2) that suppressed run's own ONSET scan arrived via
  replay (`onset_is_replayed`, from `gold_sector_reading.is_replayed`, itself from `silver_probe_reading` --
  `min_by(is_replayed, event_time)` in the `runs` CTE picks the EARLIEST scan's flag, not any scan's). An
  ordinary, in-order second onset suppressed by an active cooldown is the cooldown working exactly as designed,
  not a conflict -- only a replayed onset, discovered late after the suppressing incident was already accepted,
  is the case doc 03's "Late-replayed onset inside an existing cooldown" actually describes.

  Contract:
  - Inputs: ref('gold_sector_reading'), ref('dim_sector') (population, for severity).
  - Grain: one row per accepted incident.
  - Materialization: incremental, merge on disturbance_id (macros.deterministic_id -- an onset-time + sector
    hash, not a random ULID, so recomputing the same run twice produces the same id and merges as an update,
    not a duplicate).
  - Unique key: disturbance_id.
  - Firing rule (probe-sourced, doc 03): imbalance_score > var('emergency_threshold'), sustained across at
    least var('sustained_scans') consecutive rows (see "Consecutive" above), cooldown of var('cooldown_hours')
    hours per sector.
  - cooldown_conflict = true on the ACCEPTED incident whose cooldown a later, REPLAYED run's onset would
    otherwise have qualified within (doc 03, "Late-replayed onset inside an existing cooldown" -- "definition
    B", Stage 4a) -- an ordinary in-order suppression is not a conflict. The later run never gets its own row at
    all either way. Correct across a chain of several suppressed runs in a row, not just a single pair (see
    "`cooldown_conflict`" above).
  - severity = imbalance_score * (1 + LOG10(GREATEST(population, 10)) / 10), doc 03's own formula.
  - agent_processed: defaults false for a row never seen before; carried forward unchanged for a row already
    in the table (both the SELECT's own LEFT JOIN against `this` and merge_exclude_columns enforce this).
  - Tests required: unique(disturbance_id), not_null(sector_id, detected_at, signature),
    assert_cooldown_respected (singular test, already written).
*/
{% endraw %}

with recursive flagged as (

    select
        *,
        imbalance_score > {{ var('emergency_threshold') }} as is_above
    from {{ ref('gold_sector_reading') }}

),

ranked as (

    select
        *,
        row_number() over (partition by sector_id order by event_time) as seq,
        row_number() over (partition by sector_id, is_above order by event_time) as seq_within_flag
    from flagged

),

grouped as (

    select
        *,
        seq - seq_within_flag as run_id
    from ranked
    where is_above

),

runs as (

    select
        sector_id,
        run_id,
        min(event_time) as onset_event_time,
        min(scan_id) as onset_scan_id,  -- ULIDs sort lexically = chronologically, so MIN() picks the earliest
        min_by(is_replayed, event_time) as onset_is_replayed,  -- doc 03, cooldown_conflict "definition B"
        count(*) as sustained_scans,
        max(imbalance_score) as peak_imbalance_score,
        max_by(z_midi, imbalance_score) as z_midi,
        max_by(z_kyber, imbalance_score) as z_kyber,
        max_by(z_dark, imbalance_score) as z_dark,
        max_by(signature, imbalance_score) as signature,
        max(event_time) as detected_at  -- the run's last (so far) qualifying scan
    from grouped
    group by sector_id, run_id
    having count(*) >= {{ var('sustained_scans') }}

),

runs_seq as (

    -- a per-sector sequence number over the (already sustained_scans-filtered) candidate runs -- small,
    -- bounded by how many qualifying runs one sector can have in 90 days (single digits in practice), which
    -- is what keeps the recursive walk below cheap and well under any recursion-depth limit.
    select
        *,
        row_number() over (partition by sector_id order by onset_event_time) as run_seq
    from runs

),

walked as (

    -- base case: each sector's first candidate run has nothing before it, so it's always accepted.
    select
        sector_id, run_seq, onset_event_time, onset_scan_id, onset_is_replayed, detected_at, sustained_scans,
        peak_imbalance_score, z_midi, z_kyber, z_dark, signature,
        cast(null as timestamp) as basis_detected_at,
        detected_at as last_accepted_detected_at,
        true as accepted
    from runs_seq
    where run_seq = 1

    union all

    -- recursive case: walk forward per sector, testing each run against the last ACCEPTED incident's
    -- detected_at (w.last_accepted_detected_at), not the immediately preceding run's own -- see the header's
    -- "Cooldown -- RESOLVED" note for why that distinction matters.
    select
        sector_id, run_seq, onset_event_time, onset_scan_id, onset_is_replayed, detected_at, sustained_scans,
        peak_imbalance_score, z_midi, z_kyber, z_dark, signature,
        basis_detected_at,
        case when suppressed then basis_detected_at else detected_at end as last_accepted_detected_at,
        not suppressed as accepted
    from (
        select
            r.sector_id, r.run_seq, r.onset_event_time, r.onset_scan_id, r.onset_is_replayed, r.detected_at,
            r.sustained_scans, r.peak_imbalance_score, r.z_midi, r.z_kyber, r.z_dark, r.signature,
            w.last_accepted_detected_at as basis_detected_at,
            r.onset_event_time < dateadd(hour, {{ var('cooldown_hours') }}, w.last_accepted_detected_at) as suppressed
        from walked w
        join runs_seq r on r.sector_id = w.sector_id and r.run_seq = w.run_seq + 1
    )

),

final as (

    select
        a.sector_id,
        a.onset_event_time,
        a.onset_scan_id,
        a.detected_at,
        a.sustained_scans,
        a.peak_imbalance_score as imbalance_score,
        a.z_midi,
        a.z_kyber,
        a.z_dark,
        a.signature,
        d.population,
        -- true on an accepted incident whenever a later, suppressed run was tested against (and fell inside
        -- the cooldown of) THIS row's own detected_at, AND that suppressed run's own onset arrived via replay
        -- (doc 03, cooldown_conflict "definition B", Stage 4a) -- an ORDINARY in-order onset suppressed by the
        -- cooldown is the cooldown working as designed, not a conflict; only a replayed onset discovered late,
        -- after this incident was already accepted, is the "late-replayed onset" case doc 03 actually means.
        -- Correct across a chain of several suppressed runs, since they all carry the same original
        -- basis_detected_at forward unchanged.
        exists (
            select 1 from walked later
            where later.sector_id = a.sector_id
              and not later.accepted
              and later.basis_detected_at = a.detected_at
              and later.onset_is_replayed
        ) as cooldown_conflict
    from walked a
    left join {{ ref('dim_sector') }} d on a.sector_id = d.sector_id
    where a.accepted

)

select
    {{ deterministic_id('onset_event_time', 'sector_id') }} as disturbance_id,
    sector_id,
    detected_at,
    onset_scan_id as scan_id,
    imbalance_score,
    z_midi,
    z_kyber,
    z_dark,
    signature,
    imbalance_score * (1 + log10(greatest(population, 10)) / 10) as severity,
    cast(false as boolean) as is_report_sourced,
    cast(null as string) as report_description,
    cast(null as double) as report_relevance,
    sustained_scans,
    cooldown_conflict,
    {% if is_incremental() %}
    coalesce(
        (select max(existing.agent_processed)  -- MAX, not a bare column: Databricks requires a correlated
                                                 -- scalar subquery to be aggregated; disturbance_id is unique,
                                                 -- so MAX() over at most one matching row changes nothing.
         from {{ this }} existing
         where existing.disturbance_id = {{ deterministic_id('final.onset_event_time', 'final.sector_id') }}),
        false
    ) as agent_processed
    {% else %}
    cast(false as boolean) as agent_processed  -- first build: nothing in `this` yet to carry forward
    {% endif %}
from final
