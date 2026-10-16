{{ config(severity='warn') }}
-- warn, not error: Job 1 runs at :03 and the agent's repair only at :15, so an unmarked row can legitimately exist in between.

-- Phase 6 (doc 03, "dbt tests": every gold.deployment references a gold.disturbance with agent_processed = true). A deployment row
-- whose incident is still marked unprocessed means the agent wrote the decision and did not mark the incident, or a full refresh of
-- gold_disturbance reset the flag. The agent's next cycle repairs both (it marks every incident that already has a deployment row),
-- and it does not write while Job 1 runs. A row seen here is a warning to read, not a failure: it clears at the next :15 cycle. It
-- returns one row per deployment.

select
    d.deployment_id,
    d.disturbance_id
from {{ source('agent', 'deployment') }} d
join {{ ref('gold_disturbance') }} g
  on g.disturbance_id = d.disturbance_id
where g.agent_processed = false
