{{ config(tags=["ground_truth"]) }}

-- noqa: disable=AL03
--
-- AL03 ("column expression without alias") fires on every branch of the UNION
-- ALL below. In a UNION only the FIRST branch's aliases name the output
-- columns; repeating `as metric` / `as value` on the other eighteen would be
-- noise that changes nothing. Disabled for this file only rather than
-- project-wide, because the rule is worth keeping everywhere else.

/*
The counts table from docs/03, readable in ten seconds: does every event the
simulator sent still exist somewhere, and is every pathology accounted for?

**This mart must reproduce the Phase 3 harness's numbers on the same seeded
corpus** (docs/06). `make pathology-check` (runs=12, time_scale=0.02, seed=0)
computes them in Python straight off the topic, before the consumer exists;
this recomputes them after landing, dedupe and quarantine. A divergence is a
bug in one of them, not an acceptable difference.

The chain is reported as SEPARATE ROWS rather than one number precisely because
the totals legitimately differ at each step, and collapsing them would hide
where. Landed -> deduped -> quarantined -> analysed, with the delta at each
step explained by a named cause:

  landed 2592 -> deduped 2552   (-40, the duplicate-delivery pathology)
  deduped 2552 -> clean 2517    (-35, quarantined: 16 null path, 19 clock skew)

Tagged ground_truth: it reads attack_runs for `requests_sent`.
*/

/* Counted straight off the Parquet, deliberately NOT from staging: a
   reconciliation that derives its "before" number from the thing it is checking
   cannot detect a fault in it.

   Counted by DISTINCT (kafka_partition, kafka_offset) rather than by physical
   row, because a Kafka offset uniquely identifies a message the consumer
   landed, and the same message can legitimately be on disk more than once: the
   committed sample partition (docs/05) is a subset of the corpus, so on a
   machine that has both, physical rows double-count the overlap. Offsets are
   immune to that — 2,857 physical rows here, 2,592 distinct messages, which is
   exactly what the Phase 3 harness counted on the topic. */
with landed_messages as (
    select distinct
        kafka_partition,
        kafka_offset,
        event_id,
        client_ts,
        received_at,
        response_time_ms
    from ({{ raw_events('request') }})
),

landed as (
    select count(*) as n from landed_messages
),

/* Raw pathology counts, before dedupe and quarantine. Present so the
   pathologies that SHRINK downstream show their before and after side by side:
   a reader comparing against the Phase 3 harness (which counts off the topic)
   would otherwise see 36 late arrivals there and 30 here with nothing
   explaining the six. */
landed_pathologies as (
    select
        sum(case when client_ts < received_at - interval 30 minute then 1 else 0 end)
            as late_arrivals_raw,
        sum(case when response_time_ms < 0 then 1 else 0 end) as negative_response_raw
    from landed_messages
),

staged as (
    select
        count(*) as deduped,
        sum(case when is_quarantined then 1 else 0 end) as quarantined,
        sum(case when not is_quarantined then 1 else 0 end) as clean,
        sum(case when quarantine_reason = 'null_path' then 1 else 0 end) as q_null_path,
        sum(case when quarantine_reason = 'future_received_at' then 1 else 0 end) as q_future,
        sum(case when body_is_valid_json = false then 1 else 0 end) as invalid_bodies,
        sum(case when source_ip is null then 1 else 0 end) as null_source_ip,
        sum(case when response_time_ms < 0 then 1 else 0 end) as negative_response_time,
        sum(case when schema_version <> 'v1' then 1 else 0 end) as drifted_schema
    from {{ ref('stg_attack_events') }}
),

runs as (
    select
        count(*) as run_count,
        sum(requests_sent) as requests_sent,
        sum(attempts_made) as attempts_made
    from {{ ref('stg_attack_runs') }}
),

attempts as (
    select count(*) as n from {{ ref('stg_attack_attempts') }}
),

features as (
    select
        count(*) as sessions,
        sum(late_arrival_count) as late_arrivals
    from {{ ref('int_session_features_observed') }}
),

metrics as (
    select
        1 as ord,
        'requests sent by simulator (attack_runs)' as metric,
        (select requests_sent from runs) as value,
        'ground truth: technique-driven sends only' as note
    union all
    select
        2,
        'request events landed (distinct kafka offsets)',
        (select n from landed),
        'includes duplicate-delivery copies and one warm-up request per session'
    union all
    select
        3,
        'request events after dedupe on event_id',
        (select deduped from staged),
        'at-least-once delivery: duplicates removed, Two-Face signature kept'
    union all
    select
        4,
        '  of which duplicate copies removed',
        (select n from landed) - (select deduped from staged),
        'the duplicate-delivery pathology (~2%)'
    union all
    select
        5,
        'request events quarantined',
        (select quarantined from staged),
        'flagged, never dropped: see fct_quarantined_events'
    union all
    select
        6,
        '  quarantined: null path',
        (select q_null_path from staged),
        'missing-required-field pathology'
    union all
    select
        7,
        '  quarantined: future received_at',
        (select q_future from staged),
        'clock-skew pathology'
    union all
    select
        8,
        'request events analysed (clean)',
        (select clean from staged),
        'what the feature models see'
    union all
    select
        9,
        'events with invalid JSON body',
        (select invalid_bodies from staged),
        'valid events, invalid bodies - not quarantine cases'
    union all
    select
        10,
        'events with null source_ip',
        (select null_source_ip from staged),
        'kept: feeds null_ip_ratio'
    union all
    select
        11,
        'events with negative response_time_ms (raw)',
        (select negative_response_raw from landed_pathologies),
        'clock-skew pathology: clamped and counted, not quarantined'
    union all
    select
        12,
        '  ...after dedupe',
        (select negative_response_time from staged),
        'delta is duplicate copies of the same skewed event'
    union all
    select
        13,
        'events with drifted schema_version',
        (select drifted_schema from staged),
        'schema-drift pathology: tolerated'
    union all
    select
        14,
        'late arrivals (raw)',
        (select late_arrivals_raw from landed_pathologies),
        'client_ts 1-6h old'
    union all
    select
        15,
        '  ...after dedupe and quarantine',
        (select late_arrivals from features),
        'feature models see clean events only; delta is dedupe + quarantined rows'
    union all
    select
        16,
        'attempt events landed',
        (select n from attempts),
        'ground truth'
    union all
    select
        17,
        'attempts_made (sum over attack_runs)',
        (select attempts_made from runs),
        'must equal the row above'
    union all
    select
        18,
        'attack_run rows',
        (select run_count from runs),
        'one per run'
    union all
    select
        19,
        'sessions with observed features',
        (select sessions from features),
        'one per session'
)

select
    ord,
    metric,
    value,
    note
from metrics
order by ord
