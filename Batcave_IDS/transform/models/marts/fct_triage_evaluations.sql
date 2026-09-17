{{ config(tags=["ground_truth"]) }}

/*
Task 1 - Attribution (docs/04). Joins orders to ground truth via
run_id -> fct_attack_runs.run_id (the answer key), scored at three levels:

  exact     suspected_villain = villain_slug              random baseline 8.3%
  top_3     villain_slug in {suspected_villain} + alternates   random 25.0%
  archetype suspected_archetype = the true archetype            random ~20%

The four closest stat pairs are all within-archetype (docs/02), so exact
accuracy well below archetype accuracy is the expected, documented shape -
not a bug to chase.

GROUND TRUTH by construction: it reveals which guesses were right. Never
read by a triage_input model.
*/

select
    o.order_id,
    o.session_id,
    o.source,
    o.prompt_version,

    r.villain_slug as true_villain,
    r.archetype as true_archetype,

    o.suspected_villain,
    o.alternate_suspects,
    o.suspected_archetype,
    o.confidence,
    o.parse_failed,
    o.hallucinated_villain,

    (not o.parse_failed and o.suspected_villain = r.villain_slug) as exact_match,
    (
        not o.parse_failed
        and (
            o.suspected_villain = r.villain_slug
            -- alternate_suspects is a JSON array string (services/triage/store.py);
            -- parsed properly rather than substring-matched, so a slug that
            -- happens to be a text fragment of another can't false-positive.
            or list_contains(
                from_json(o.alternate_suspects, '["VARCHAR"]'), r.villain_slug
            )
        )
    ) as top_3_match,
    (not o.parse_failed and o.suspected_archetype = r.archetype) as archetype_match
from {{ ref('fct_intervention_orders') }} as o
inner join {{ ref('fct_attack_runs') }} as r on o.run_id = r.run_id
