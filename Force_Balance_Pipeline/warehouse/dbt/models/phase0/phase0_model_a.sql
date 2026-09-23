select
    id,
    label,
    value
from {{ ref('phase0_seed') }}
