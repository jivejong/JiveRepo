{#
  Phase 0 Q3 probe. Disabled unless enable_streaming_check is true (dbt_project.yml).
  Source is an NDJSON landing volume in force.phase0, created and fed by
  scripts/check_platform.py q3. The path is deliberately a literal: this is a throwaway
  probe, not a real model.
#}
{{ config(materialized='streaming_table') }}

select
    id,
    label,
    value,
    _metadata.file_name as _source_file
from stream read_files(
    '/Volumes/force/phase0/stream_src/',
    format => 'json'
)
