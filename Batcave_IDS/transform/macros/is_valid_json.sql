{#
  docs/02's `parse_json_safe()` — the safe-parse half of it. The malformed-body
  pathology (~3%, docs/03 row 5) sends truncated JSON like `{"truncated": `, and
  those are valid EVENTS with an invalid body, not quarantine cases, so the
  parse must never raise.

  DuckDB-specific and therefore confined to macros/, per docs/02's portability
  note: json_valid() is the DuckDB spelling.

  Returns NULL — not false — for a body that never claimed to be JSON, so
  `invalid_body_ratio` averages only over bodies where "is this valid JSON?" is
  a meaningful question:

  - no body at all (a GET)
  - a body that isn't JSON-SHAPED (doesn't start with { or [)

  That second case matters more than it looks. Most POST bodies here are filler
  bytes ('xxxx...') sizing body_bytes for the strength signature, and they are
  not JSON by construction. Judging them with a bare json_valid() marks 43 of 59
  requests invalid in a corpus containing zero malformed-JSON pathology rows —
  invalid_body_ratio would sit near 1.0 for every villain and completely drown
  the ~3% pathology (docs/03 row 5) it exists to surface.

  This matches how the Phase 3 harness already counts the pathology
  (services/simulator/pathology_check.py: body starts with '{' and fails to
  parse), so the dbt number and the harness number mean the same thing.
#}

{#
  Implementation note: the shape check uses starts_with() rather than LIKE
  because a '{' immediately followed by '%' is a Jinja tag opener and breaks
  rendering before SQL ever sees the string.
#}
{% macro is_valid_json(column_name) %}
    case
        when {{ column_name }} is null then null
        when not starts_with(ltrim({{ column_name }}), '{')
            and not starts_with(ltrim({{ column_name }}), '[')
            then null
        else cast(json_valid({{ column_name }}) as boolean)
    end
{%- endmacro %}
