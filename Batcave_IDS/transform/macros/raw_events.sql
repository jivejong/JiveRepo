{#
  Reads one landed event_kind out of the Parquet lakehouse the Phase 4 consumer
  writes (docs/01, docs/02):

      data/raw/<event_kind>/dt=YYYY-MM-DD/hour=HH/part-<uuid>.parquet

  Two things here are not optional, both learned the hard way in Phase 4 and
  recorded in docs/06:

  - `union_by_name = true`. The schema-drift pathology (docs/03 row 8) bumps
    schema_version to v2 mid-run and adds `tls_fingerprint`, so sibling Parquet
    files in one directory legitimately differ by a column. DuckDB's default
    strict read fails across them.
  - The session must be UTC (set on the profile). `received_at` lands as
    TIMESTAMP WITH TIME ZONE and DuckDB renders those in the session's local
    zone, so hour comparisons silently disagree with the `hour=` partition
    anywhere but UTC.

  Absent event kinds are tolerated deliberately. `read_parquet` on a glob that
  matches zero files is a hard error, and Track A never produces `chat_turn` or
  `counterstrike` events — so a clean clone has no such directory at all. Rather
  than let that break the build, an absent kind yields a correctly-TYPED empty
  relation, so downstream models compile and run against a stable column
  contract whether or not the data exists yet. The column lists live in
  `empty_event_relation` and mirror services/consumer/schemas.py.
#}

{% macro raw_glob(event_kind) %}
    {{- var("data_root", "../data") }}/raw/{{ event_kind }}/**/*.parquet
{%- endmacro %}


{% macro raw_events_exist(event_kind) %}
    {#- glob() returns zero rows rather than erroring, which read_parquet won't do. -#}
    {%- if execute -%}
        {%- set found = run_query(
            "select count(*) as n from glob('" ~ raw_glob(event_kind) ~ "')"
        ).columns[0][0] -%}
        {{- return(found > 0) -}}
    {%- else -%}
        {#- Parse-time: assume present so the graph builds. -#}
        {{- return(true) -}}
    {%- endif -%}
{% endmacro %}


{% macro raw_events(event_kind) %}
    {%- if raw_events_exist(event_kind) -%}
        select * from read_parquet(
            '{{ raw_glob(event_kind) }}',
            hive_partitioning = true,
            union_by_name = true
        )
    {%- else -%}
        {{ empty_event_relation(event_kind) }}
    {%- endif -%}
{% endmacro %}
