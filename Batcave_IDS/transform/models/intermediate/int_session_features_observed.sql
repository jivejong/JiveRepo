{{ config(tags=["triage_input"]) }}

/*
The triage model's entire world: one row per session, derived only from what
the honeypot's HTTP sensor recorded (docs/02).

Nothing here may come from the attempt log. The feature expressions are LIFTED
from the separability harness (services/simulator/separability.py) rather than
re-derived, because docs/06 makes a divergence between the two on the same
corpus a bug in one of them, not an acceptable difference. But the harness
computes observed and truth features in ONE query — its retry_ratio,
pivot_ratio and attempts_per_stage_reached come from attempt events — so the
lift is SPLIT at the boundary: those live in int_session_features_truth, tagged
ground_truth. Lifting the harness query whole would be the exact leak this
phase exists to prevent.

Ambiguity flags from docs/02's Phase 3 pass are resolved here, not left to
prose. Where the harness already pinned a definition it is lifted verbatim
(wasted_request_ratio, exact_duplicate_path_pairs, body_bytes_trend); the rest
are pinned in place with the reason.
*/

with events as (
    select * from {{ ref('int_session_events') }}
),

events_with_peak as (
    select
        *,
        max(path_tier) over (partition by session_id) as max_tier
    from events
),

peak as (
    select
        session_id,
        max(path_tier) as peak_tier,
        /* request_seq at which the session FIRST reached its own peak tier. */
        min(case when path_tier = max_tier then request_seq end) as first_peak_seq
    from events_with_peak
    group by session_id
),

first_error as (
    select
        session_id,
        min(case when status_returned >= 400 then request_seq end) as first_error_seq
    from events
    group by session_id
),

path_counts as (
    select
        session_id,
        path,
        count(*) as path_hits
    from events
    group by session_id, path
),

path_totals as (
    select
        session_id,
        sum(path_hits) as total
    from path_counts
    group by session_id
),

path_shape as (
    select
        pc.session_id,
        count(*) as distinct_paths,
        /* Shannon entropy over the session's path distribution. */
        -sum(
            (pc.path_hits * 1.0 / totals.total)
            * log2(pc.path_hits * 1.0 / totals.total)
        ) as path_entropy,
        /* [def] docs/02: the name says "pairs" but the pinned definition is the
           count of PATHS APPEARING MORE THAN ONCE, not n-choose-2 over them.
           Lifted from the harness, which already resolved it this way. */
        sum(case when pc.path_hits > 1 then 1 else 0 end) as exact_duplicate_path_pairs
    from path_counts as pc
    inner join path_totals as totals on pc.session_id = totals.session_id
    group by pc.session_id
),

/* --- streak features -------------------------------------------------
   Gaps-and-islands: a row is "in" the streak if it matches, and consecutive
   matching rows share (request_seq - row_number over matching rows). Each
   feature is the COUNT OF MAXIMAL RUNS meeting a minimum length, not the total
   number of matching requests — "runs" in docs/02 means streaks, and counting
   matching rows instead would just re-count volume. Thresholds and their
   observed basis are recorded in docs/02.
*/
streak_rows as (
    select
        session_id,
        request_seq,
        status_returned,
        path,
        path_tier,
        -- brute force: consecutive auth failures
        status_returned = 401 as is_auth_failure,
        -- collection: consecutive reads of tier-3+ (sensitive) endpoints
        path_tier >= 3 as is_sensitive_access,
        -- scanning: a path not yet seen in this session, i.e. enumeration
        row_number() over (partition by session_id, path order by request_seq) = 1
            as is_new_path
    from events
),

/* All three streak kinds in one pass, labelled, rather than three near-identical
   nested blocks. Same gaps-and-islands arithmetic, one place to get it right. */
streak_members as (
    select
        session_id,
        request_seq,
        'auth_failure' as kind
    from streak_rows
    where is_auth_failure
    union all
    select
        session_id,
        request_seq,
        'sensitive_access' as kind
    from streak_rows
    where is_sensitive_access
    union all
    select
        session_id,
        request_seq,
        'new_path' as kind
    from streak_rows
    where is_new_path
),

streak_groups as (
    select
        session_id,
        kind,
        /* Consecutive request_seq values share this difference, so it
           identifies one maximal run. */
        request_seq
        - row_number() over (partition by session_id, kind order by request_seq) as streak_id
    from streak_members
),

streak_lengths as (
    select
        session_id,
        kind,
        count(*) as run_length
    from streak_groups
    group by session_id, kind, streak_id
),

streak_counts as (
    select
        session_id,
        count(*) filter (
            where kind = 'auth_failure'
            and run_length >= {{ var('auth_failure_run_min_length') }}
        ) as repeated_auth_failure_runs,
        count(*) filter (
            where kind = 'sensitive_access'
            and run_length >= {{ var('sensitive_access_run_min_length') }}
        ) as sensitive_data_access_runs,
        count(*) filter (
            where kind = 'new_path'
            and run_length >= {{ var('path_enumeration_run_min_length') }}
        ) as path_enumeration_runs
    from streak_lengths
    group by session_id
),

/* Track B (docs/08) produces no chat_turn events, so these are structurally
   zero in Track A — see docs/02. Carried anyway so the triage model's input
   contract does not change shape when Track B lands, which would make every
   Track A accuracy number non-comparable to every Track B one. */
chat as (
    select
        session_id,
        count(*) as chat_turns_completed,
        coalesce(avg(case when refused then 0.0 else 1.0 end), 0.0) as probe_engagement_ratio,
        count(extracted_intent_flags) as intent_flags_triggered
    from {{ ref('stg_botchat_turns') }}
    group by session_id
),

base as (
    select
        e.session_id,
        min(e.run_id) as run_id,
        count(*) as request_count,
        min(e.received_at) as session_started_at,
        max(e.received_at) as session_ended_at,

        /* [def] duration floor of 0.001s, lifted from the harness: a burst
           session spanning zero measurable time would divide by zero. */
        greatest(epoch(max(e.received_at)) - epoch(min(e.received_at)), 0.001) as duration_s,

        max(e.path_tier) as max_path_tier,
        avg(case when e.status_returned >= 400 then 1.0 else 0.0 end) as error_ratio,
        count(distinct e.source_ip) as distinct_source_ips,
        count(distinct e.user_agent) as distinct_user_agents,
        avg(case when e.source_ip is null then 1.0 else 0.0 end) as null_ip_ratio,
        stddev_samp(e.gap_s) * 1000.0 as inter_request_stddev_ms,
        avg(e.response_time_ms) as mean_response_time_ms,
        avg(e.body_bytes) as mean_body_bytes,
        max(e.body_bytes) as max_body_bytes,

        /* invalid_body_ratio averages only over bodies where "is this valid
           JSON?" is a meaningful question — see the is_valid_json macro. */
        avg(case when e.body_is_valid_json then 0.0 else 1.0 end) as invalid_body_ratio,

        sum(case when e.query_string like '%riddle=%' then 1 else 0 end) as riddle_param_count,

        sum(case when {{ is_traversal_pattern() }} then 1 else 0 end) as traversal_pattern_count,
        sum(case when {{ is_injection_pattern() }} then 1 else 0 end) as injection_pattern_count,

        /* Late arrival (docs/03 row 4): client_ts backdated 1-6h relative to
           when the sensor actually received the request. */
        sum(
            case
                when
                    e.client_ts is not null
                    and e.client_ts < e.received_at - interval 30 minute
                    then 1
                else 0
            end
        ) as late_arrival_count,

        /* [def] body_bytes_trend is the Pearson correlation of body_bytes with
           request order, guarded to 0 for constant bodies — not a slope and not
           last-minus-first, which would rank villains differently. Lifted. */
        case
            when count(*) > 1 and stddev_pop(e.body_bytes) > 0
                then corr(e.body_bytes, e.request_seq)
            else 0.0
        end as body_bytes_trend,

        sum(case when e.status_returned >= 400 then 1 else 0 end) as error_count
    from events as e
    group by e.session_id
),

/* [pair] wasted_request_ratio: fraction of requests that did NOT increase
   tier_reached_so_far, counted only up to first reaching the session's peak
   tier. Requests after the peak are not waste — there is nothing left to
   advance toward (Bane's post-escalation hammering is the objective, not
   waste). Only interpretable alongside max_path_tier: it measures
   efficient-vs-flailing, not how high. Lifted from the harness verbatim. */
wasted as (
    select
        e.session_id,
        max(p.first_peak_seq) as first_peak_seq,
        sum(
            case
                when e.request_seq <= p.first_peak_seq and e.path_tier <= e.tier_before then 1
                else 0
            end
        ) as wasted_numerator
    from events as e
    inner join peak as p on e.session_id = p.session_id
    group by e.session_id
),

/* [def] First arrival at tier 3, measured from the session's own start.
   Absent (and therefore NULL below) for a session that never got there. */
tier3 as (
    select
        session_id,
        min(received_at) as first_tier3_at
    from events
    where path_tier >= 3
    group by session_id
)

select
    b.session_id,
    b.run_id,
    b.request_count,
    b.session_started_at,
    b.session_ended_at,
    b.duration_s,

    /* [def] requests_per_min: duration floor above, PLUS a 600/min cap. The
       cap is not decoration — the harness applies it in Python after the SQL
       (separability.py _REQ_PER_MIN_CAP), so lifting only the SQL would have
       silently diverged from the harness on exactly the burst sessions the cap
       exists for. */
    b.max_path_tier,

    b.error_ratio,
    b.distinct_source_ips,
    b.distinct_user_agents,
    b.null_ip_ratio,
    b.mean_body_bytes,
    b.max_body_bytes,
    b.riddle_param_count,
    b.traversal_pattern_count,
    b.injection_pattern_count,
    b.late_arrival_count,
    b.body_bytes_trend,
    least(b.request_count / (b.duration_s / 60.0), 600.0) as requests_per_min,
    coalesce(b.inter_request_stddev_ms, 0.0) as inter_request_stddev_ms,
    coalesce(b.mean_response_time_ms, 0.0) as mean_response_time_ms,
    coalesce(b.invalid_body_ratio, 0.0) as invalid_body_ratio,

    coalesce(ps.distinct_paths, 0) as distinct_paths,
    coalesce(ps.path_entropy, 0.0) as path_entropy,
    coalesce(ps.exact_duplicate_path_pairs, 0) as exact_duplicate_path_pairs,

    w.wasted_numerator * 1.0 / greatest(w.first_peak_seq, 1) as wasted_request_ratio,

    /* [def] time_to_tier3_s is NULL when the session never reached tier 3 —
       most stallers. Explicitly not 0, which would read as "reached it
       instantly" and make the fastest and the never-arrived indistinguishable.
       Aggregates over it must exclude nulls rather than coalesce them. */
    epoch(t3.first_tier3_at) - epoch(b.session_started_at) as time_to_tier3_s,

    fe.first_error_seq is not null as had_error,
    coalesce(b.request_count - fe.first_error_seq, 0) as requests_after_first_error,

    coalesce(sk.repeated_auth_failure_runs, 0) as repeated_auth_failure_runs,
    coalesce(sk.path_enumeration_runs, 0) as path_enumeration_runs,
    coalesce(sk.sensitive_data_access_runs, 0) as sensitive_data_access_runs,

    coalesce(c.chat_turns_completed, 0) as chat_turns_completed,
    coalesce(c.probe_engagement_ratio, 0.0) as probe_engagement_ratio,
    coalesce(c.intent_flags_triggered, 0) as intent_flags_triggered
from base as b
inner join wasted as w on b.session_id = w.session_id
left join path_shape as ps on b.session_id = ps.session_id
left join first_error as fe on b.session_id = fe.session_id
left join tier3 as t3 on b.session_id = t3.session_id
left join streak_counts as sk on b.session_id = sk.session_id
left join chat as c on b.session_id = c.session_id
