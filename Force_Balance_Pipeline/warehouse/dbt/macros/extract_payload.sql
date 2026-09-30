{#
  Payload extraction (doc 05, "Portability macro"; doc 07 Phase 4 risk note). Phase 4 ships the Databricks branch
  only -- the Postgres branch, and the rest of the `local` target, are deferred to Phase 8. `local` raises a
  compiler error rather than running untested SQL, so a `local` build fails loudly and early, not with a wrong
  answer.

  try_variant_get, not a plain CAST: CAST(variant AS DOUBLE) on a non-numeric value raises under ANSI mode;
  try_variant_get returns NULL instead, matching bronze's own "malformed becomes NULL" convention
  (ingest/autoloader_bronze.py, try_parse_json). ESTABLISHED ON DATABRICKS (2026-09-30, dbt show against a
  literal VARIANT, then confirmed by this macro's own unit tests): a numeric-*string* value ("14200.5" instead
  of 14200.5) is coerced to the number, not returned as NULL -- the outer CAST(... AS {{ type }}) is doing the
  coercion (Databricks CAST(STRING AS DOUBLE) parses a numeric string), not try_variant_get itself. A
  non-numeric string ("not-a-number") still returns NULL, not an error -- confirmed the same way. Neither the
  probe nor the bridge sends a numeric channel value as a JSON string today (doc 02); this only matters if that
  ever changes.

  This macro reads `{{ column }}` directly, assuming it is already a genuine VARIANT -- true for bronze's real
  `payload` column (Auto Loader's own try_parse_json, ingest/autoloader_bronze.py). It is NOT true of dbt's
  default unit-test fixture format: a plain `CAST(<json text literal> AS VARIANT)` does not parse the JSON, it
  wraps the literal text as a STRING-typed variant scalar, so every try_variant_get(...) path lookup on it
  silently returns NULL (found running this macro's own unit tests -- the wrong answer, not an error). Fixed
  in the fixtures, not here: models/staging/_staging__unit_tests.yml uses `format: sql` with our own
  `parse_json(...)` for every `payload` fixture value, which produces a real object and needs no change to
  this macro. (An earlier version of this macro worked around it here instead, with
  `try_parse_json(cast(column as string))` on every call -- confirmed harmless on real data, but unnecessary
  once the fixtures were fixed at the source, and it cost a round-trip on every real read for no reason.)
#}
{% macro extract_payload(column, field, type) %}
  {% if target.type == 'databricks' %}
    CAST(try_variant_get({{ column }}, '$.{{ field }}', '{{ type }}') AS {{ type }})
  {% else %}
    {{ exceptions.raise_compiler_error("extract_payload: the Postgres branch is deferred to Phase 8 (doc 07); `local` cannot build silver/gold yet") }}
  {% endif %}
{% endmacro %}
