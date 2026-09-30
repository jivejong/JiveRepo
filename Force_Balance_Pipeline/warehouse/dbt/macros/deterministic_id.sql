{#
  disturbance_id only (doc 03, "Deviation from generate_ulid.sql") -- deployment_id is Phase 6 and unaffected.
  Deterministic, not a random ULID: 48 bits of the onset scan's event_time (epoch ms) followed by 80 bits from a
  hash of (sector_id, onset event_time), so two dbt runs over the same onset produce the same id, which the
  incremental gold.disturbance merge key needs.

  KNOWN SIMPLIFICATION, TO ESTABLISH ON DATABRICKS: conv() encodes base 32 as 0-9a-v, not Crockford's ULID
  alphabet (0-9A-HJKMNP-TV-Z, which excludes I, L, O, U). The id below is deterministic and time-sortable by its
  first 10 characters, but is not yet a byte-for-byte valid ULID string. Fix by translating conv()'s alphabet to
  Crockford's before shipping, or by accepting a documented "ULID-shaped, not ULID-valid" id -- undecided, flagged
  here rather than asserted either way.
#}
{% macro deterministic_id(onset_event_time, sector_id) %}
concat(
  lpad(conv(cast(unix_millis({{ onset_event_time }}) as string), 10, 32), 10, '0'),
  lpad(conv(substr(sha2(concat(cast({{ sector_id }} as string), cast(unix_millis({{ onset_event_time }}) as string)), 256), 1, 20), 16, 32), 16, '0')
)
{% endmacro %}
