/* docs/02: threat_score is 0-100 by construction (eight weighted components
   summing to 100, each sub-normalized into [0, 1] before its weight is
   applied). A score outside that range means a component's cap or
   normalization broke - the components are supposed to be un-clippable by
   design (every raw feature is least(x, cap)-guarded), so this should never
   fire; it exists to catch that guarantee breaking silently. */

select session_id, threat_score
from {{ ref('mart_threat_scores') }}
where threat_score < 0 or threat_score > 100
