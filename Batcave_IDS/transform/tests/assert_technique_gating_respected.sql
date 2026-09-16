/*
No attempt may use a technique whose stat floors exceed the villain's
(docs/02). Catches simulator bugs directly: if gating regressed, villains would
be attempting techniques they cannot reach and every probability downstream
would be computed from an impossible premise.

Checks all three gates, not just intelligence - docs/07's Phase 2 finding was
that Ra's al Ghul clears every min_intelligence gate but fails
privesc_exploit's min_power, so an intelligence-only check would have missed it.
*/

select
    a.attempt_id,
    a.technique_id,
    r.villain_slug,
    v.intelligence,
    v.power,
    v.strength,
    t.min_intelligence,
    t.min_power,
    t.min_strength
from {{ ref('stg_attack_attempts') }} as a
inner join {{ ref('stg_attack_runs') }} as r on a.run_id = r.run_id
inner join {{ ref('stg_villains') }} as v on r.villain_slug = v.slug
inner join {{ ref('stg_techniques') }} as t on a.technique_id = t.technique_id
where
    v.intelligence < t.min_intelligence
    or v.power < t.min_power
    or v.strength < t.min_strength
