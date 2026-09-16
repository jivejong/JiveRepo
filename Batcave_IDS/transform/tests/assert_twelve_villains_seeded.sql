/* docs/02: twelve villains, verified present in the akabab dataset. A short
   roster means a seed failed to load and every attribution baseline (1/12) is
   wrong. */

select count(*) as villain_count
from {{ ref('stg_villains') }}
having count(*) <> 12
