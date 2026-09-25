{#
  Phase 1 review check 3 (doc 08, doc 07 step 5): does the Jedi roster cover every specialty,
  and is it the expected size?

  Run after `dbt seed`, once the Phase 1 seeds exist:
    dbt show --select phase1_check_3_jedi --limit 100 --vars "{run_phase1_checks: true}" --profiles-dir .

  Disabled by default, for the same reason as check 1: the ref does not resolve until the seeds
  exist.

  Pass criteria: at least 3 Jedi per primary_specialty (the column the constraint layer uses),
  and exactly 17 rows.
#}
{{ config(enabled=var('run_phase1_checks', false) | as_bool) }}

with roster as (
    select * from {{ ref('dim_jedi') }}
),

specialties as (
    select * from values
        ('combat'),
        ('diplomacy'),
        ('investigation'),
        ('stealth')
    as t(specialty)
),

per_specialty as (
    select s.specialty, count(r.jedi_id) as jedi_count
    from specialties s
    left join roster r on r.primary_specialty = s.specialty
    group by s.specialty
)

select
    concat('primary_specialty:', specialty) as check_name,
    jedi_count as observed,
    3 as required,
    (jedi_count >= 3) as ok
from per_specialty

union all

select 'row_count', count(*), 17, (count(*) = 17)
from roster

order by check_name
