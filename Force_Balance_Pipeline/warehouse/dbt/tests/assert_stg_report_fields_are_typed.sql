-- Phase 5. The report fields stg_bronze_events extracts have the types silver and gold rely on. A dbt unit test cannot see a wrong type here:
-- it casts every expected value to the column's actual type, so a relevance_score read as an integer (0.93 -> 0) would still "match" its
-- expected 0.93. This asks the engine for the column types themselves, on one row of the real view.

with sample as (

    select description, relevance_score, inference_model, inference_rationale, midichlorian_ppm, kyber_resonance, dark_side_activity
    from {{ ref('stg_bronze_events') }}
    limit 1

)

select 'description is not a string' as failure_reason from sample where typeof(description) <> 'string'
union all
select 'relevance_score is not a double' from sample where typeof(relevance_score) <> 'double'
union all
select 'inference_model is not a string' from sample where typeof(inference_model) <> 'string'
union all
select 'inference_rationale is not a string' from sample where typeof(inference_rationale) <> 'string'
union all
select 'a channel is not a double' from sample
where typeof(midichlorian_ppm) <> 'double' or typeof(kyber_resonance) <> 'double' or typeof(dark_side_activity) <> 'double'
