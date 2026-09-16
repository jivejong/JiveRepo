/* Shared dimension — the ATT&CK catalog is public. See stg_villains. */

select
    technique_id,
    attack_id,
    attack_name,
    attack_tactic_id,
    cast(stage as integer) as stage,
    display_name,
    cast(min_intelligence as integer) as min_intelligence,
    cast(min_power as integer) as min_power,
    cast(min_strength as integer) as min_strength,
    cast(base_success_rate as double) as base_success_rate,
    cast(w_intelligence as double) as w_intelligence,
    cast(w_strength as double) as w_strength,
    cast(w_speed as double) as w_speed,
    cast(w_durability as double) as w_durability,
    cast(w_power as double) as w_power,
    cast(w_combat as double) as w_combat,
    cast(noise_level as integer) as noise_level,
    cast(retry_penalty as double) as retry_penalty,
    observability,
    detection_signature,
    cast(produces_traffic as boolean) as produces_traffic
from {{ ref('techniques') }}
