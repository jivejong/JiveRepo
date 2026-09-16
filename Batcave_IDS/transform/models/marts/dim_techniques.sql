/* Shared dimension. `observability` is what makes detection coverage analysis
   possible and is central to how technique recall is reported (docs/02). */

select
    technique_id,
    attack_id,
    attack_name,
    attack_tactic_id,
    stage,
    display_name,
    min_intelligence,
    min_power,
    min_strength,
    base_success_rate,
    noise_level,
    retry_penalty,
    observability,
    detection_signature,
    produces_traffic
from {{ ref('stg_techniques') }}
