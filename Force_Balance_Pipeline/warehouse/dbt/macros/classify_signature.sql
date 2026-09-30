{#
  Deterministic signature classification (doc 03, "Signature classification"). First match wins, in the doc's own
  order. `veiled_presence`'s first condition is COALESCE(ABS(z_midi), 0) < 1.0, not plain ABS(z_midi) < 1.0 -- a
  STEALTH reading has no midichlorian channel, so z_midi is NULL there, and only this one zero-bound condition
  treats an absent channel as satisfying it; every directional condition (>/<) on a missing channel is still NULL,
  still false, everywhere else in this table (doc 03, "Decided, Phase 4").

  edge/tests/test_dbt_doc_parity.py checks this CASE, condition for condition and in order, against
  _doc03.signature_table() (the same independent doc-03 parser edge/tests/test_doc_parity.py already uses for
  forcesim.signatures) -- keep the operators and thresholds in sync with the doc if either changes.

  z_midi, z_kyber, z_dark, population, channels_present are SQL expressions, not literal column names.
#}
{% macro classify_signature(z_midi, z_kyber, z_dark, population, channels_present) %}
CASE
  WHEN {{ z_dark }} > 2.5 AND {{ z_kyber }} < -1.0 THEN 'sith_presence'
  WHEN {{ z_dark }} > 2.0 AND {{ z_midi }} > 1.5 THEN 'dark_adept'
  WHEN {{ z_midi }} > 2.0 AND {{ z_kyber }} > 2.0 AND ABS({{ z_dark }}) < 1.5 THEN 'nexus_awakening'
  WHEN {{ z_midi }} < -2.0 AND {{ z_kyber }} < -2.0 THEN 'force_drain'
  WHEN {{ z_kyber }} > 2.5 AND ABS({{ z_midi }}) < 1.5 AND ABS({{ z_dark }}) < 1.5 THEN 'kyber_cache'
  WHEN {{ z_dark }} > 1.5 AND {{ population }} > 1e9 AND ABS({{ z_kyber }}) < 1.5 THEN 'civil_unrest'
  WHEN COALESCE(ABS({{ z_midi }}), 0) < 1.0 AND {{ z_dark }} > 2.0 AND {{ channels_present }} < 3 THEN 'veiled_presence'
  ELSE 'unclassified'
END
{% endmacro %}
