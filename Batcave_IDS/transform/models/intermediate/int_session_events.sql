{{ config(tags=["triage_input"]) }}

/*
Per-event windows over a session, ordered by `received_at` (docs/02).

`received_at` and never `client_ts` — a hard constraint. `client_ts` is
attacker-supplied and the pathologies deliberately shuffle it within a 30s
window and backdate ~1% of it by 1-6 hours (docs/03 rows 2 and 4). Ordering on
it would let the attacker choose the sequence the analysis sees.

Quarantined rows are excluded from the window functions but not deleted — they
are carried in stg_attack_events and reported by fct_quarantined_events. A row
with a null path or a received_at an hour in the future would corrupt both the
ordering and the tier progression if it participated here.
*/

with ordered as (
    select
        event_id,
        session_id,
        run_id,
        received_at,
        path,
        path_tier,
        status_returned,
        source_ip,
        user_agent,
        query_string,
        request_body,
        body_bytes,
        response_time_ms,
        body_is_valid_json,
        client_ts,

        row_number() over (
            partition by session_id order by received_at
        ) as request_seq,

        lag(received_at) over (
            partition by session_id order by received_at
        ) as prev_received_at,

        /* The running max of path_tier INCLUDING this row — "how high has this
           session reached by now". */
        max(path_tier) over (
            partition by session_id order by received_at
            rows between unbounded preceding and current row
        ) as tier_reached_so_far,

        /* The same running max EXCLUDING this row, so "did this request
           advance the session?" is answerable. Lifted from the separability
           harness (services/simulator/separability.py), which defines a
           request as advancing iff its tier exceeds the max before it.
           -1 for the first request, which has nothing before it. */
        coalesce(
            max(path_tier) over (
                partition by session_id order by received_at
                rows between unbounded preceding and 1 preceding
            ),
            -1
        ) as tier_before
    from {{ ref('stg_attack_events') }}
    where not is_quarantined
)

select
    *,
    epoch(received_at) - epoch(prev_received_at) as gap_s
from ordered
