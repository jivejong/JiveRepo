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

  Cooldown -- KNOWN LIMITATION, not silently glossed over: each run's cooldown check compares only against
  its immediately preceding run for the same sector (a LAG(), not a running "last accepted incident"
  pointer), because a true "last accepted" walk needs sequential/recursive logic Databricks SQL does not
  support (no WITH RECURSIVE). This correctly handles the documented fixture (two runs 30 minutes apart, one
  incident, doc 07 checkpoint C4) and the general two-run case, but a THIRD run arriving soon after a
  second run that was itself suppressed could be incorrectly accepted as a new incident instead of also being
  caught by the first run's cooldown. Not fixed this round; noted for a future one.

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
  - cooldown_conflict = true on the EARLIER (lower onset_event_time) of two runs that would otherwise both
    qualify within var('cooldown_hours') of each other for the same sector (doc 03, "Late-replayed onset
    inside an existing cooldown") -- the later run never gets its own row at all.
  - severity = imbalance_score * (1 + LOG10(GREATEST(population, 10)) / 10), doc 03's own formula.
  - agent_processed: defaults false for a row never seen before; carried forward unchanged for a row already
    in the table (both the SELECT's own LEFT JOIN against `this` and merge_exclude_columns enforce this).
  - Tests required: unique(disturbance_id), not_null(sector_id, detected_at, signature),
    assert_cooldown_respected (singular test, already written).
*/
{% endraw %}

with flagged as (

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
        count(*) as sustained_scans,
        max(imbalance_score) as peak_imbalance_score,
        max_by(z_midi, imbalance_score) as z_midi,
        max_by(z_kyber, imbalance_score) as z_kyber,
        max_by(z_dark, imbalance_score) as z_dark,
        max_by(signature, imbalance_score) as signature,
        max(event_time) as detected_at  -- the run's last (so far) qualifying scan -- see the header's cooldown note
    from grouped
    group by sector_id, run_id
    having count(*) >= {{ var('sustained_scans') }}

),

with_cooldown as (

    select
        r.*,
        lag(r.detected_at) over (partition by r.sector_id order by r.onset_event_time) as prev_detected_at
    from runs r

),

accepted as (

    select
        *,
        (prev_detected_at is not null
         and onset_event_time < dateadd(hour, {{ var('cooldown_hours') }}, prev_detected_at)) as suppressed
    from with_cooldown

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
        -- this row's own suppressed status is irrelevant to ITS OWN cooldown_conflict flag; that flag belongs
        -- to the EARLIER run in a suppressed pair. exists_later_suppressed_by_me: true when the next run for
        -- this sector was suppressed because of this one.
        exists (
            select 1 from accepted later
            where later.sector_id = a.sector_id
              and later.onset_event_time > a.onset_event_time
              and later.suppressed
              and later.prev_detected_at = a.detected_at
        ) as cooldown_conflict
    from accepted a
    left join {{ ref('dim_sector') }} d on a.sector_id = d.sector_id
    where not a.suppressed

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
