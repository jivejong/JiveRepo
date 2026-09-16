/*
Shared dimension: neither triage_input nor ground_truth.

The roster itself is public knowledge — twelve named villains and their
powerstats. What is ground truth is WHICH villain ran a given session, and that
lives in stg_attack_runs. A triage model is allowed to know the candidate list;
that is what makes attribution a twelve-way choice rather than open-ended.
*/

select
    slug,
    name,
    archetype,
    cast(intelligence as integer) as intelligence,
    cast(strength as integer) as strength,
    cast(speed as integer) as speed,
    cast(durability as integer) as durability,
    cast(power as integer) as power,
    cast(combat as integer) as combat
from {{ ref('villains') }}
