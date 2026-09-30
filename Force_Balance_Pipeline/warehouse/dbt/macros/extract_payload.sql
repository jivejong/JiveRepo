{#
  Payload extraction (doc 05, "Portability macro"; doc 07 Phase 4 risk note). Phase 4 ships the Databricks branch
  only -- the Postgres branch, and the rest of the `local` target, are deferred to Phase 8. `local` raises a
  compiler error rather than running untested SQL, so a `local` build fails loudly and early, not with a wrong
  answer.

  try_variant_get, not a plain CAST: CAST(variant AS DOUBLE) on a non-numeric value raises under ANSI mode;
  try_variant_get returns NULL instead, matching bronze's own "malformed becomes NULL" convention
  (ingest/autoloader_bronze.py, try_parse_json). Whether a numeric *string* value returns NULL or the parsed
  number is not asserted here -- TO ESTABLISH ON DATABRICKS, see the extract_payload unit tests.
#}
{% macro extract_payload(column, field, type) %}
  {% if target.type == 'databricks' %}
    CAST(try_variant_get({{ column }}, '$.{{ field }}', '{{ type }}') AS {{ type }})
  {% else %}
    {{ exceptions.raise_compiler_error("extract_payload: the Postgres branch is deferred to Phase 8 (doc 07); `local` cannot build silver/gold yet") }}
  {% endif %}
{% endmacro %}
