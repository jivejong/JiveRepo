/*
Every technique marked `high` observability must leave a non-zero evidence
feature in the sessions that used it (docs/02). If it does not, either the
tier is wrong or the detection signature is not being emitted — and this is the
test that catches a matcher which silently matches nothing.

**The technique -> evidence mapping is explicit here rather than assumed.**
Phase 5 found that docs/02's four evidence features covered only five of the
seven `high` techniques:

  active_scan          -> path_enumeration_runs
  account_discovery    -> path_enumeration_runs
  brute_force          -> repeated_auth_failure_runs
  exploit_public_app   -> traversal_pattern_count
  exploit_remote_svc   -> injection_pattern_count
  data_local_system    -> sensitive_data_access_runs   (feature ADDED in Phase 5)
  deploy_batbot        -> chat_turns_completed          (EXCLUDED, see below)

`sensitive_data_access_runs` was added because repeated GETs against tier-3/4
endpoints is exactly what a detector keys on for collection and exfiltration —
the gap was real, not a test artifact.

`deploy_batbot` is excluded for a STRUCTURAL reason, not a convenient one: its
evidence is `chat_turns_completed`, and Track A produces no chat turns at all
(it POSTs to /api/v1/assistant, which 404s until Track B builds the assistant).
The exclusion lifts in Track B, when chat_turn events exist.
*/

with evidence_mapping as (
    select * from (
        values
        ('active_scan', 'path_enumeration_runs'),
        ('account_discovery', 'path_enumeration_runs'),
        ('brute_force', 'repeated_auth_failure_runs'),
        ('exploit_public_app', 'traversal_pattern_count'),
        ('exploit_remote_svc', 'injection_pattern_count'),
        ('data_local_system', 'sensitive_data_access_runs')
    ) as t (technique_id, evidence_feature)
),

sessions_using as (
    select distinct
        a.technique_id,
        a.session_id
    from {{ ref('stg_attack_attempts') }} as a
    inner join {{ ref('stg_techniques') }} as tech on a.technique_id = tech.technique_id
    where tech.observability = 'high' and tech.produces_traffic
),

evidence_per_session as (
    select
        s.technique_id,
        m.evidence_feature,
        sum(
            case m.evidence_feature
                when 'path_enumeration_runs' then f.path_enumeration_runs
                when 'repeated_auth_failure_runs' then f.repeated_auth_failure_runs
                when 'traversal_pattern_count' then f.traversal_pattern_count
                when 'injection_pattern_count' then f.injection_pattern_count
                when 'sensitive_data_access_runs' then f.sensitive_data_access_runs
            end
        ) as total_evidence,
        count(*) as sessions
    from sessions_using as s
    inner join evidence_mapping as m on s.technique_id = m.technique_id
    inner join {{ ref('int_session_features_observed') }} as f on s.session_id = f.session_id
    group by s.technique_id, m.evidence_feature
)

select
    technique_id,
    evidence_feature,
    sessions,
    total_evidence
from evidence_per_session
where total_evidence = 0 or total_evidence is null
