{{ config(tags=["ground_truth"]) }}

/*
The Batanalytics dashboard's remediation panel (docs/08): real ATT&CK
mitigation IDs for the techniques the triage model actually identified, not
the full 23-technique catalog regardless of relevance. Reads
fct_technique_evaluations (ground truth - it's the only place "did the
model predict this technique at least once" lives) purely for reporting,
the same precedent mart_detection_coverage and mart_reconciliation already
set for a pure-output mart touching ground truth - never fed back to the
LLM, so the leakage boundary this tag enforces isn't at risk here.

One row per (technique, source) predicted at least once - source stays on
the grain so baseline and llm can be compared side by side, the same shape
fct_intervention_orders already established. mitigation_id/mitigation_name
are real null for the four techniques with no listed ATT&CK mitigation
(stg_mitigations) - shown as "no mitigation," not hidden or defaulted.
*/

with predicted as (
    select distinct
        technique_id,
        attack_id,
        source
    from {{ ref('fct_technique_evaluations') }}
    where predicted
)

select
    p.source,
    p.technique_id,
    p.attack_id,
    t.display_name,
    t.observability,
    m.mitigation_id,
    m.mitigation_name
from predicted as p
inner join {{ ref('dim_techniques') }} as t on p.technique_id = t.technique_id
left join {{ ref('stg_mitigations') }} as m on p.attack_id = m.attack_id
