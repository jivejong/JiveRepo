{#
  The composite deviation score (doc 03, "Composite deviation score"). Weighted Euclidean distance over signed
  z-scores, dark side double-weighted, scaled by SQRT(3/channels_present) so a partial (STEALTH) reading isn't
  automatically lower-scoring than a full one. The literal structure below (weights, COALESCE-to-zero per term,
  the 3.0 divisor) is checked against doc 03's own SQL block by edge/tests/test_dbt_doc_parity.py -- keep the
  numeric literals and the POWER(COALESCE(...), 2) shape in sync with the doc if either changes.

  z_midi, z_kyber, z_dark, channels_present are SQL expressions (column references or sub-expressions), not
  literal column names -- callers pass whatever the model's own aliases are.
#}
{% macro imbalance_score(z_midi, z_kyber, z_dark, channels_present) %}
SQRT(
    1.0 * POWER(COALESCE({{ z_midi }},  0), 2)
  + 1.0 * POWER(COALESCE({{ z_kyber }}, 0), 2)
  + 2.0 * POWER(COALESCE({{ z_dark }},  0), 2)
) * SQRT(3.0 / {{ channels_present }})
{% endmacro %}
