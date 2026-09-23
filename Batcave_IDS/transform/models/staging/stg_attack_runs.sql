{{ config(tags=["ground_truth"]) }}

/*
GROUND TRUTH, and the single most sensitive model in the project: it carries
`villain_slug`, the identity the triage model is asked to reconstruct. A
triage_input model with this anywhere in its ancestry invalidates every
attribution number in the repository.

Also carries `timing_compression_factor` (Phase 3) so a reader can tell from
the data alone whether a run's timing was faithful or compressed, and
`session_source` (Phase 8, docs/06 Track B) so a reader can tell a human-paced
console run from a corpus-grade headless one - `coalesce`d to 'headless' here
because every row landed before Phase 8 predates the column: `union_by_name`
reads those back with `session_source` null, and null is a run this project
generated before the console existed, which is a headless run by definition.

Two SELECT layers, not one, specifically around `session_source`. sqlfluff's
ST06 rule wants calculated columns after simple passthrough ones, which is
why the `coalesce(...) as session_source` expression sits last in `typed`
below - but a single flat SELECT list makes that position load-bearing in a
way that isn't obvious from reading it: any later column added ahead of it
that also needs to reference `session_source` would define the alias before
it's available in that same list, a real DuckDB binder error ("column
referenced before it is defined"), not a hypothetical one - docs/09 has the
CI run that hit exactly this. Splitting `session_source`'s definition into
its own CTE (`typed`) means the outer SELECT consumes it as an ordinary
passthrough column, not a same-list forward reference, so any future column
that needs it can go anywhere in the outer list without reintroducing this
fragility - a structural fix, not a reorder that just moves the same trap.
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
),

typed as (
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
        cast(hour as integer) as hour,
        _delivery_seq,
        coalesce(session_source, 'headless') as session_source
    from deduplicated
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
    hour,
    session_source
from typed
where _delivery_seq = 1
