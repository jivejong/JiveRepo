{#
  Overrides dbt's default generate_schema_name (which would concatenate the target schema with a model's own
  `+schema` config, e.g. `gold_silver` when the target schema is `gold` and a model sets `+schema: silver`).
  A custom schema is used exactly as given; a model with no custom schema falls back to the target's own schema.
  This is dbt's own documented override for exactly this case -- not project-specific logic.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}
        {{ default_schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
