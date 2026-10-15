{{ config(severity='error') }}

-- Phase 6 (doc 03, "dbt tests": every gold.deployment references a gold.disturbance with agent_processed = true). A deployment row
-- whose incident is still marked unprocessed means the agent wrote the decision and did not mark the incident, or a full refresh of
-- gold_disturbance reset the flag. The agent's next cycle repairs both (it marks every incident that already has a deployment row),
-- and it does not run while Job 1 does, so a row returned here after Job 1's own build is a real finding. It returns one row per
-- deployment.

select
    d.deployment_id,
    d.disturbance_id
from {{ source('agent', 'deployment') }} d
join {{ ref('gold_disturbance') }} g
  on g.disturbance_id = d.disturbance_id
where g.agent_processed = false
