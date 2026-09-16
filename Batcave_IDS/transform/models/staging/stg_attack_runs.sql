{{ config(tags=["ground_truth"]) }}

/*
GROUND TRUTH, and the single most sensitive model in the project: it carries
`villain_slug`, the identity the triage model is asked to reconstruct. A
triage_input model with this anywhere in its ancestry invalidates every
attribution number in the repository.

Also carries `timing_compression_factor` (Phase 3) so a reader can tell from
the data alone whether a run's timing was faithful or compressed.
*/

with raw_events as (
    {{ raw_events('attack_run') }}
),

deduplicated as (
    select
        *,
        row_number() over (
            partition by event_id
            order by received_at asc, kafka_offset asc
        ) as _delivery_seq
    from raw_events
)

select
    event_id,
    event_kind,
    run_id,
    session_id,
    received_at,
    client_ts,
    schema_version,
    villain_slug,
    started_at,
    ended_at,
    duration_s,
    requests_sent,
    attempts_made,
    max_stage_reached,
    run_outcome,
    pathologies_enabled,
    timing_compression_factor,
    kafka_partition,
    kafka_offset,
    landed_at,
    dt,
    cast(hour as integer) as hour
from deduplicated
where _delivery_seq = 1
