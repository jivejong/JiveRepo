/* Shared reference, same precedent as stg_techniques/stg_villains — the
ATT&CK mitigation catalog is public, not derived from any observed or
ground-truth data in this project.

Scoped to the 23 techniques actually reachable in the game (dim_techniques),
not an exhaustive mitigation catalog — enough to demonstrate the mechanism
(docs/08), the same scoping the bat bot's flag-word list already used.
Verified by hand against live attack.mitre.org technique pages, one primary
mitigation per technique where MITRE lists more than one — never guessed
from memory (docs/09's own precedent: verify ATT&CK data against the live
source, the same discipline Phase 2 applied to the 23 attack_ids themselves).

Four techniques genuinely have no listed mitigation on their real ATT&CK
page — T1056 (Input Capture), T1113 (Screen Capture), T1123 (Audio
Capture), T1125 (Video Capture) all state mitigation is impractical because
the technique abuses a legitimate system feature rather than a flaw. Left
as a real null here, not papered over with a placeholder — the remediation
panel (mart_remediation) must show these as "no mitigation," a genuine and
reportable finding, not a rendering gap.
*/

select
    attack_id,
    nullif(mitigation_id, '') as mitigation_id,
    nullif(mitigation_name, '') as mitigation_name
from {{ ref('mitigations') }}
