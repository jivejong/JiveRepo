{{ config(severity='error') }}

-- Phase 6 (doc 03, "gold.deployment"; doc 06, "ETA"). A deploy row names at least one Jedi, a starship, an ETA and a release time
-- (released_at = decided_at + eta_hours + 24 hours, set at write time); a stand_down row holds nothing, so its released_at is null.
-- It returns one row per deployment that breaks either rule.

select
    deployment_id,
    'a deploy row is missing starship_id, eta_hours or released_at, or has no Jedi' as failure_reason
from {{ source('agent', 'deployment') }}
where decision = 'deploy'
  and (   starship_id is null
       or eta_hours is null
       or released_at is null
       or jedi_ids is null
       or size(jedi_ids) = 0)

union all

select
    deployment_id,
    'a stand_down row has a released_at'
from {{ source('agent', 'deployment') }}
where decision = 'stand_down'
  and released_at is not null
