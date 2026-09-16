/* Shared dimension. See stg_villains. */

select
    cast(stage as integer) as stage,
    name,
    attack_tactic_id,
    attack_tactic_name,
    cast(difficulty_multiplier as double) as difficulty_multiplier
from {{ ref('stages') }}
