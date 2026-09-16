/* Shared dimension. */

select
    stage,
    name as stage_name,
    attack_tactic_id,
    attack_tactic_name,
    difficulty_multiplier
from {{ ref('stg_stages') }}
