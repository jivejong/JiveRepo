{#
  Typed empty relations for event kinds that have landed nothing yet.

  Track A produces `request`, `attempt`, and `attack_run`. `chat_turn` and
  `counterstrike` are Track B (docs/08) and nothing emits them, but their
  staging models are built now on purpose: `int_session_features_observed` is
  the triage model's entire input contract, and if its three chat features
  appeared in Phase 6 but not Phase 5, every Track A accuracy number would
  become silently non-comparable to Track B.

  So the columns are declared here rather than inferred from data. Types mirror
  services/consumer/schemas.py — the consumer will write exactly these when
  Track B lands, and tests/test_dbt_empty_relations.py checks the two agree.

  Envelope columns (services/common/envelope.py) plus the consumer-added
  kafka_partition / kafka_offset / landed_at, plus the kind's own fields from
  docs/02. `dt` and `hour` are the Hive partition columns read_parquet exposes.
#}

{% macro envelope_columns() %}
    cast(null as varchar) as event_id,
    cast(null as varchar) as event_kind,
    cast(null as varchar) as run_id,
    cast(null as varchar) as session_id,
    cast(null as timestamp with time zone) as received_at,
    cast(null as timestamp with time zone) as client_ts,
    cast(null as varchar) as schema_version,
    cast(null as integer) as kafka_partition,
    cast(null as bigint) as kafka_offset,
    cast(null as timestamp with time zone) as landed_at,
    cast(null as date) as dt,
    cast(null as varchar) as hour
{%- endmacro %}


{% macro empty_event_relation(event_kind) %}
    {%- if event_kind == "chat_turn" -%}
        select
            {{ envelope_columns() }},
            cast(null as integer) as turn_number,
            cast(null as varchar) as speaker,
            cast(null as varchar) as objective,
            cast(null as varchar) as bot_text,
            cast(null as varchar) as user_text,
            cast(null as varchar) as extracted_intent_flags,
            cast(null as boolean) as refused,
            cast(null as double) as latency_ms,
            cast(null as bigint) as input_tokens,
            cast(null as bigint) as output_tokens
        where false
    {%- elif event_kind == "counterstrike" -%}
        select
            {{ envelope_columns() }},
            cast(null as integer) as sequence,
            cast(null as varchar) as readout_line,
            cast(null as varchar) as attributed_villain_slug,
            cast(null as double) as attributed_confidence,
            cast(null as varchar) as attack_id
        where false
    {%- else -%}
        {{ exceptions.raise_compiler_error(
            "No empty-relation column list for event_kind '" ~ event_kind ~ "'. "
            ~ "Add one here (mirroring services/consumer/schemas.py) rather than "
            ~ "letting an absent kind fail at read_parquet."
        ) }}
    {%- endif -%}
{% endmacro %}
