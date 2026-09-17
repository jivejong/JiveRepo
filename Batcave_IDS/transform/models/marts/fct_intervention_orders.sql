/*
Neither triage_input nor ground_truth: this is the triage system's OUTPUT
(both LLM and baseline), not something fed to a model or an answer key.
docs/04's field table: `threat_score` comes from the analytics layer, never
the LLM - joined in here from mart_threat_scores, not read from the
predictions table.

One row per (session_id, source) - both `llm` and `baseline` orders live
here side by side (the `source` column), same shape, so
fct_triage_evaluations and fct_technique_evaluations can score both without
a fork.
*/

select
    p.order_id,
    p.session_id,
    p.run_id,
    p.source,
    p.prompt_version,
    p.model_name,
    p.issued_at,

    t.threat_score,

    p.threat_level,
    p.suspected_villain,
    p.alternate_suspects,
    p.suspected_archetype,
    p.confidence,
    p.identified_techniques,
    p.identified_tactics,
    p.reconstructed_stage_reached,
    p.reasoning,
    p.in_person_intervention_required,
    p.recommended_countermeasures,
    p.attack_pattern_summary,

    p.parse_failed,
    p.hallucinated_villain,
    p.hallucinated_technique_count,
    p.latency_ms,
    p.input_tokens,
    p.output_tokens
from {{ source('triage', 'raw_triage_predictions') }} as p
left join {{ ref('mart_threat_scores') }} as t on p.session_id = t.session_id
