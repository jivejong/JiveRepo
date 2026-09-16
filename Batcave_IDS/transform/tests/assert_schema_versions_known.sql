/* docs/02: assert every distinct schema_version is in a known-versions list.

   The drift pathology bumps v1 -> v2 mid-run and adds tls_fingerprint, which
   staging tolerates by design. What must NOT pass silently is a THIRD version
   appearing, which would mean a producer changed the contract without anyone
   updating the consumer or these models. */

select distinct schema_version
from {{ ref('stg_attack_events') }}
where schema_version not in ('v1', 'v2')
