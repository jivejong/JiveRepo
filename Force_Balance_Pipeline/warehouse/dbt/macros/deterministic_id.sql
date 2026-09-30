{#
  disturbance_id only (doc 03, "Deviation from generate_ulid.sql") -- deployment_id is Phase 6 and unaffected.
  Deterministic, not a random ULID: 48 bits of the onset scan's event_time (epoch ms) followed by a hash of
  (sector_id, onset event_time), so two dbt runs over the same onset produce the same id, which the
  incremental gold.disturbance merge key needs.

  KNOWN SIMPLIFICATION #1, TO ESTABLISH ON DATABRICKS: conv() encodes base 32 as 0-9a-v, not Crockford's ULID
  alphabet (0-9A-HJKMNP-TV-Z, which excludes I, L, O, U). The id below is deterministic and time-sortable by its
  first 10 characters, but is not yet a byte-for-byte valid ULID string. Fix by translating conv()'s alphabet to
  Crockford's before shipping, or by accepting a documented "ULID-shaped, not ULID-valid" id -- undecided, flagged
  here rather than asserted either way.

  KNOWN SIMPLIFICATION #2, ESTABLISHED ON DATABRICKS (2026-09-30, a build failure, not guessed): the hash
  portion is 64 bits (16 hex characters of the sha2-256 digest), not the originally documented 80. conv() on
  Databricks operates on a 64-bit integer internally and raises ARITHMETIC_OVERFLOW past that -- confirmed
  live: conv() on a 16-hex-char (64-bit) input works, the same call on a 20-hex-char (80-bit) input fails
  every time. 48 (timestamp) + 64 (hash) = 112 bits, not the full 128 a real ULID carries. 64 bits of hash
  entropy is still far beyond any collision risk this project will ever produce (a few hundred incidents,
  total, ever) -- accepted as a real, permanent design point, not a placeholder to revisit.
#}
{% macro deterministic_id(onset_event_time, sector_id) %}
concat(
  lpad(conv(cast(unix_millis({{ onset_event_time }}) as string), 10, 32), 10, '0'),
  lpad(conv(substr(sha2(concat(cast({{ sector_id }} as string), cast(unix_millis({{ onset_event_time }}) as string)), 256), 1, 16), 16, 32), 13, '0')
)
{% endmacro %}
