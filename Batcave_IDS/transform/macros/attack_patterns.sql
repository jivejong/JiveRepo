{#
  Matchers for the two evidence features that detect exploit payloads
  (docs/02: `traversal_pattern_count`, `injection_pattern_count`).

  These are pinned to what the simulator ACTUALLY emits, not to a generic
  WAF-rule wishlist. The authoritative source is `_REQUEST_SPECS` in
  services/simulator/traffic.py — Phase 3's villain signatures *transform*
  those specs (adding ?riddle=, duplicating requests, swapping methods) rather
  than replacing them, so the payloads survive into every villain's traffic:

      exploit_public_app  GET  /cave/archives/case-0001?file=../../etc/passwd
      exploit_remote_svc  POST /cave/vehicle-bay/status?cmd=;cat%20/etc/shadow

  Two details that decide whether these ever match:

  - `query_string` is stored RAW, from `request.url.query` (honeypot app.py),
    so it keeps its percent-encoding: the injection payload arrives as
    `cmd=;cat%20/etc/shadow`, with %20 rather than a space.
  - The traversal marker `../` appears in the QUERY, not the path.

  A matcher that silently matches nothing is the specific failure mode here:
  the feature computes zero forever, and
  `assert_high_observability_techniques_leave_evidence` then fails for a reason
  nobody connects back to a regex. tests/test_dbt_attack_patterns.py runs these
  matchers against the literal payload strings imported from traffic.py, so
  drift fails immediately and on its own terms.

  Matched case-insensitively across path, query string, and body, since a
  payload can legitimately arrive in any of the three.
#}

{% macro _haystack() %}
    lower(
        coalesce(path, '')
        || ' ' || coalesce(query_string, '')
        || ' ' || coalesce(request_body, '')
    )
{%- endmacro %}


{% macro is_traversal_pattern() %}
    (
        {#- Literal '../' and its common encodings. The simulator emits the
            literal form; the encodings cost nothing and are what a real
            detector would look for. -#}
        {{ _haystack() }} like '%../%'
        or {{ _haystack() }} like '%..\\%'
        or {{ _haystack() }} like '%25%2e%2e%2f%'
        or {{ _haystack() }} like '%2e%2e%2f%'
        or {{ _haystack() }} like '%2e%2e/%'
    )
{%- endmacro %}


{% macro is_injection_pattern() %}
    (
        {#- Shell command chaining: the simulator's `;cat%20/etc/shadow`, plus
            the pipe/backtick/$() forms of the same idea. -#}
        regexp_matches({{ _haystack() }}, ';\s*[a-z/]')
        or regexp_matches({{ _haystack() }}, '%3b\s*[a-z/]')
        or {{ _haystack() }} like '%|%'
        or {{ _haystack() }} like '%`%'
        or {{ _haystack() }} like '%$(%'
        {#- Deliberately NOT matching '/etc/passwd' or '/etc/shadow'. Those are
            sensitive file TARGETS, not injection SYNTAX, and matching them made
            exploit_public_app's traversal payload
            (file=../../etc/passwd) register as injection as well —
            caught by test_every_benign_catalog_path_is_clean. The two features
            map to two different techniques (docs/02), so overlapping them would
            hand Phase 6's technique reconstruction injection evidence for a
            session that only ever performed traversal. The traversal matcher
            already catches those payloads by their '../'. -#}
        {#- Classic SQL / script injection, for completeness. Nothing in the
            current catalog emits these; they are here so a technique added
            later is detected rather than silently uncovered. -#}
        or regexp_matches({{ _haystack() }}, 'union\s+select')
        or regexp_matches({{ _haystack() }}, '''\s*or\s+1\s*=\s*1')
        or {{ _haystack() }} like '%<script%'
    )
{%- endmacro %}
