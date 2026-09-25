{{ config(
    enabled=var('run_phase1_checks', false)
) }}

-- Failure mode 1: A row is flagged as unknown but does not have the 'uncharted' sector_id
select
    sector_id,
    'Row flagged as is_unknown but sector_id is not uncharted' as failure_reason,
    NULL as flagged_sector_id_count
from {{ ref('dim_sector') }}
where is_unknown = true
  and sector_id != 'uncharted'

union all

-- Failure mode 2: Table has an invalid count of flagged unknown rows (must be exactly 1)
select
    NULL as sector_id,
    'Expected exactly 1 row with is_unknown = true' as failure_reason,
    count(*) as flagged_sector_id_count
from {{ ref('dim_sector') }}
where is_unknown = true
having count(*) != 1
