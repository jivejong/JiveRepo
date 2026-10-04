{#
  Overrides dbt's default generate_schema_name (which would concatenate the target schema with a model's own
  `+schema` config, e.g. `gold_silver` when the target schema is `gold` and a model sets `+schema: silver`).
  A custom schema is used exactly as given; a model with no custom schema falls back to the target's own schema.
  This is dbt's own documented override for exactly this case -- not project-specific logic.

  Target-aware, added Stage 4a (docs/ENGINEERING-LOG.md, "Stage 4a... schema isolation"): a bug, found the hard
  way, not guessed at -- "custom schema used exactly as given" means EVERY target resolves a `+schema: gold`
  model to literally `gold`, including `--target dev`, which was assumed (never checked) to isolate into
  `dev_jivejong` instead. It didn't: `dbt build --target dev --full-refresh` on `gold_sector_reading` wrote
  straight into the real `force.gold.gold_sector_reading` prod table Job 1 reads and writes, colliding with a
  live scheduled run and failing it (schema out of sync) until a worktree-based rebuild from `main` restored it.

  Only `dev` is special-cased, and only to fully ignore custom_schema_name and use target.schema alone -- every
  OTHER target (`prod`, `local`, and Job 1's own Databricks-generated `databricks_cluster` profile, which sets
  `catalog`/`schema` directly on the dbt_task rather than naming a target at all) keeps today's literal-schema
  behavior byte-for-byte unchanged. This is deliberately narrow: `dev` is the only target anyone runs ad hoc,
  exploratory builds against, so it's the only one that needs real isolation; broadening the condition risks
  silently changing a target this project depends on behaving exactly as it does today.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set default_schema = target.schema -%}
    {%- if target.name == 'dev' -%}
        {{ default_schema }}
    {%- elif custom_schema_name is none -%}
        {{ default_schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
