{{ config(materialized='table') }}

select
    id,
    label,
    value,
    value * 2 as value_doubled
from {{ ref('phase0_model_a') }}
