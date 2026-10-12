{{ config(materialized='incremental', unique_key='disturbance_id', incremental_strategy='merge', merge_exclude_columns='agent_processed') }}

{% raw %}
/*
  gold_disturbance (doc 03, "gold.disturbance"; doc 07 Phase 4 step 6, Phase 5 for the report rules).

  REPORT RULES (Phase 5; doc 03 "Firing rules -- report-sourced", with the decisions made for the parts it leaves
  open). A "qualifying report" is a gold_sector_reading row with source_type = 'report' whose imbalance_score is
  over var('emergency_threshold') and whose silver_force_report relevance_score is at least
  var('report_relevance_threshold'). There is no sustained requirement. Everything below is unchanged for probes:
    - The probe chain (flagged ... final) reads PROBE rows only (source_type filter in `flagged`). A report row never
      joins a run, breaks one, or enters a cooldown walk, and a probe incident is never suppressed by a report.
    - A qualifying report that falls INSIDE an accepted probe incident's window -- event_time from the incident's
      onset up to detected_at + var('cooldown_hours') -- ESCALATES that incident instead of creating one:
        severity            = GREATEST(the probe severity, the severity of each escalating report computed with the
                              same population weighting as the probe's) -- i.e. the highest of them all
        report_description, report_relevance
                            = those of the highest-relevance escalating report (ties: earliest event_time, then
                              lowest event_id)
        is_report_sourced   stays false (the evidence is still probe-led); cooldown_conflict is untouched.
      The description attaches even when the report's own severity is the lower one: corroboration is the point.
    - A qualifying report NOT inside any probe incident's window becomes its own incident, if it is the first in its
      cooldown: the same last-ACCEPTED walk as the probe cooldown (`report_walk`), over report incidents only. A
      later qualifying report inside that cooldown is absorbed: no row, no change to the incident. detected_at is
      the report's event_time, sustained_scans is 1, scan_id is null, is_report_sourced is true. Its id is a
      deterministic_id over the report's event_time and ('report:' + event_id), so it cannot collide with a probe
      incident's.
    - A report before a probe onset (outside the window above) leaves both rows: the report incident and, later,
      the probe incident -- the probe is never suppressed. assert_cooldown_respected exempts exactly these
      report-versus-probe pairs. KNOWN EDGE: if a report incident was recorded first and a late-replayed probe run
      later makes that report fall inside the probe incident's window, the report now escalates the probe incident
      but its earlier row stays (a merge never deletes); both rows exist, as for the replayed-onset case above.
    - Reads silver_force_report directly (relevance and description are not columns of gold_sector_reading, and no
      column was added there); doc 03's note that this table never reads silver is no longer true.
    - report_walk is recursive like `walked`: its depth is the number of qualifying, un-escalating reports for one
      sector over all history. Both walks take their recursion limit from var('walk_recursion_limit') and are guarded
      by assert_walk_depth_below_limit -- see "RECURSION DEPTH" below.

  RECURSION DEPTH (added Phase 5, with measurements). Both walks (`walked` over probe runs, `report_walk` over report
  incidents) are recursive CTEs, and a recursive CTE stops with RECURSION_LEVEL_LIMIT_EXCEEDED once its chain is
  longer than the limit. The chain is not shortened by the cooldown: an absorbed run or report is still a link in it.
    - The real bound: the number of sustained probe runs (or standalone qualifying reports) one sector has had over
      ALL history -- `flagged` has no time filter, so nothing caps it at 90 days. It grows without end, at a rate that
      today is a few runs per sector per month (the deepest probe chain in dev was 4 or 5 runs after about 3 weeks).
    - The limit: before Phase 5 neither walk set one, so both ran at the engine's default of 100 and the probe walk
      would have failed Job 1 outright once a sector passed it. Both now say MAX RECURSION LEVEL
      var('walk_recursion_limit') (500). Measured on the serverless warehouse: about 0.95 s per level (depth 150 in
      195 s, depth 400 in 433 s, about 52 s fixed), linear; Job 1's transform task times out at 2400 s. This is an
      interim measure, not a bound: at the intake's 200-reports-a-day cap one sector could in principle add 200 levels a
      day, so 500 is days of worst-case traffic, though years at the probe's present rate.
    - The guard: assert_walk_depth_below_limit FAILS (severity error) when any sector's chain in either walk reaches
      80% of the limit (400). An error fails Job 1, which stops its success ping, so the alert fires with headroom
      before the hard limit. When it fires, the fix is a bounded recompute (see the Engineering Log's open item), not
      a bigger number: raising the limit past about 1000 would exceed the task timeout.

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
  - Inputs: ref('gold_sector_reading'), ref('silver_force_report') (report relevance and description),
    ref('dim_sector') (population, for severity).
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
    assert_cooldown_respected (singular test, already written), assert_walk_depth_below_limit (severity error).
*/
{% endraw %}

with recursive flagged as (

    select
        *,
        imbalance_score > {{ var('emergency_threshold') }} as is_above
    from {{ ref('gold_sector_reading') }}
    -- probe rows only: a report never joins or breaks a run (see the header). source_type is never null in the real
    -- table; the COALESCE only lets fixtures written before Phase 5, which omit the column, keep meaning "a probe scan".
    where coalesce(source_type, 'probe') = 'probe'

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

    -- a per-sector sequence number over the (already sustained_scans-filtered) candidate runs. Its maximum is the
    -- depth of the recursive walk below. NOT bounded by 90 days or anything else: `flagged` reads all of
    -- gold_sector_reading, so it is the number of sustained runs the sector has ever had (4 or 5 at the deepest in
    -- dev after about 3 weeks). The walk's limit is var('walk_recursion_limit') (500) and
    -- assert_walk_depth_below_limit errors at 80% of it -- see "RECURSION DEPTH" in the header.
    select
        *,
        row_number() over (partition by sector_id order by onset_event_time) as run_seq
    from runs

),

walked MAX RECURSION LEVEL {{ var('walk_recursion_limit') }} as (

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

),

report_source as (

    -- silver_force_report, one row per event_id (earliest arrival): a guard, silver already guarantees it
    select event_id, relevance_score, description
    from (
        select
            event_id, relevance_score, description,
            row_number() over (partition by event_id order by arrival_ts asc) as rn
        from {{ ref('silver_force_report') }}
    )
    where rn = 1

),

qualifying_reports as (

    -- every report that meets the firing rule, weighted like a probe incident (doc 03 severity formula)
    select
        r.event_id,
        r.sector_id,
        r.event_time,
        r.imbalance_score,
        r.z_midi,
        r.z_kyber,
        r.z_dark,
        r.signature,
        f.relevance_score,
        f.description,
        r.imbalance_score * (1 + log10(greatest(d.population, 10)) / 10) as report_severity
    from {{ ref('gold_sector_reading') }} r
    join report_source f on f.event_id = r.event_id
    left join {{ ref('dim_sector') }} d on r.sector_id = d.sector_id
    where r.source_type = 'report'
      and r.imbalance_score > {{ var('emergency_threshold') }}
      and f.relevance_score >= {{ var('report_relevance_threshold') }}

),

escalating as (

    -- qualifying reports that fall inside an accepted probe incident's window [onset, detected_at + cooldown).
    -- Probe incidents are at least cooldown_hours apart, so their windows do not overlap: a report is in at most one.
    select
        p.sector_id as incident_sector_id,
        p.onset_event_time as incident_onset_event_time,
        q.event_id,
        q.report_severity,
        q.relevance_score,
        q.description,
        row_number() over (
            partition by p.sector_id, p.onset_event_time
            order by q.relevance_score desc, q.event_time asc, q.event_id asc
        ) as relevance_rank
    from final p
    join qualifying_reports q
      on q.sector_id = p.sector_id
     and q.event_time >= p.onset_event_time
     and q.event_time < dateadd(hour, {{ var('cooldown_hours') }}, p.detected_at)

),

escalation as (

    select
        incident_sector_id,
        incident_onset_event_time,
        max(report_severity) as max_report_severity,
        max(case when relevance_rank = 1 then description end) as top_description,
        max(case when relevance_rank = 1 then relevance_score end) as top_relevance
    from escalating
    group by incident_sector_id, incident_onset_event_time

),

standalone_reports as (

    select
        q.*,
        row_number() over (partition by q.sector_id order by q.event_time asc, q.event_id asc) as report_seq
    from qualifying_reports q
    where q.event_id not in (select event_id from escalating)

),

report_walk MAX RECURSION LEVEL {{ var('walk_recursion_limit') }} as (

    -- the same last-ACCEPTED walk as `walked`, over report incidents only: a report is absorbed if it falls inside the
    -- cooldown of the last report incident that was actually accepted; a suppressed report does not extend anything.
    select
        sector_id, report_seq, event_id, event_time, imbalance_score, z_midi, z_kyber, z_dark, signature,
        relevance_score, description, report_severity,
        event_time as last_accepted_event_time,
        true as accepted
    from standalone_reports
    where report_seq = 1

    union all

    select
        sector_id, report_seq, event_id, event_time, imbalance_score, z_midi, z_kyber, z_dark, signature,
        relevance_score, description, report_severity,
        case when suppressed then basis_event_time else event_time end as last_accepted_event_time,
        not suppressed as accepted
    from (
        select
            r.sector_id, r.report_seq, r.event_id, r.event_time, r.imbalance_score, r.z_midi, r.z_kyber, r.z_dark,
            r.signature, r.relevance_score, r.description, r.report_severity,
            w.last_accepted_event_time as basis_event_time,
            r.event_time < dateadd(hour, {{ var('cooldown_hours') }}, w.last_accepted_event_time) as suppressed
        from report_walk w
        join standalone_reports r on r.sector_id = w.sector_id and r.report_seq = w.report_seq + 1
    )

),

incidents as (

    select
        {{ deterministic_id('f.onset_event_time', 'f.sector_id') }} as disturbance_id,
        f.sector_id,
        f.detected_at,
        f.onset_scan_id as scan_id,
        f.imbalance_score,
        f.z_midi,
        f.z_kyber,
        f.z_dark,
        f.signature,
        -- GREATEST skips a NULL argument: with no escalating report this is the probe severity unchanged.
        greatest(f.imbalance_score * (1 + log10(greatest(f.population, 10)) / 10), e.max_report_severity) as severity,
        cast(false as boolean) as is_report_sourced,
        e.top_description as report_description,
        e.top_relevance as report_relevance,
        f.sustained_scans,
        f.cooldown_conflict
    from final f
    left join escalation e
      on e.incident_sector_id = f.sector_id
     and e.incident_onset_event_time = f.onset_event_time

    union all

    select
        {{ deterministic_id('w.event_time', "concat('report:', w.event_id)") }} as disturbance_id,
        w.sector_id,
        w.event_time as detected_at,
        cast(null as string) as scan_id,
        w.imbalance_score,
        w.z_midi,
        w.z_kyber,
        w.z_dark,
        w.signature,
        w.report_severity as severity,
        cast(true as boolean) as is_report_sourced,
        w.description as report_description,
        w.relevance_score as report_relevance,
        cast(1 as bigint) as sustained_scans,
        cast(false as boolean) as cooldown_conflict
    from report_walk w
    where w.accepted

)

select
    disturbance_id,
    sector_id,
    detected_at,
    scan_id,
    imbalance_score,
    z_midi,
    z_kyber,
    z_dark,
    signature,
    severity,
    is_report_sourced,
    report_description,
    report_relevance,
    sustained_scans,
    cooldown_conflict,
    {% if is_incremental() %}
    coalesce(
        (select max(existing.agent_processed)  -- MAX, not a bare column: Databricks requires a correlated
                                                 -- scalar subquery to be aggregated; disturbance_id is unique,
                                                 -- so MAX() over at most one matching row changes nothing.
         from {{ this }} existing
         where existing.disturbance_id = incidents.disturbance_id),
        false
    ) as agent_processed
    {% else %}
    cast(false as boolean) as agent_processed  -- first build: nothing in `this` yet to carry forward
    {% endif %}
from incidents
