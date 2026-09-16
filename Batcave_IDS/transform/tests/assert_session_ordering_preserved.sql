/*
Per-session ordering must hold (docs/02). Messages are keyed by session_id so a
session lands on one partition and keeps its order — EXCEPT the ~1% published
with a null key (the unkeyed pathology, docs/03 row 3), which round-robin
across partitions.

This asserts the consequence that matters for analysis: within a session,
request_seq must increase with received_at, with no ties that would make the
order ambiguous. It does NOT assert single-partition residence, because the
unkeyed pathology deliberately breaks that and documenting the consequence is
the point rather than pretending it does not happen.
*/

select
    session_id,
    request_seq,
    received_at,
    prev_received_at
from {{ ref('int_session_events') }}
where
    prev_received_at is not null
    and received_at < prev_received_at
