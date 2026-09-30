{#
  Epoch-seconds conversion, the other place the two targets diverge (doc 05's "Portability macro" names only
  extract_payload, but silver's ingest_lag_seconds needs unix_timestamp() too, which has no Postgres equivalent of
  the same name -- extract_epoch_from() is not defined here since `local` is deferred to Phase 8, same as
  extract_payload above; raising keeps that deferral consistent across every per-target macro, not just the one
  doc 05 happened to name first).
#}
{% macro epoch_seconds(timestamp_expr) %}
  {% if target.type == 'databricks' %}
    unix_timestamp({{ timestamp_expr }})
  {% else %}
    {{ exceptions.raise_compiler_error("epoch_seconds: local target is deferred to Phase 8 (doc 07)") }}
  {% endif %}
{% endmacro %}
