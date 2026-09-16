/* Shared dimension — see stg_villains for why this is untagged. */

select
    slug as villain_slug,
    name as villain_name,
    archetype,
    intelligence,
    strength,
    speed,
    durability,
    power,
    combat
from {{ ref('stg_villains') }}
