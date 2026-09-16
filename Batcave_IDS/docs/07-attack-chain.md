# 07 — Attack chain and technique catalog

**Track A. Headless.** Everything here runs without a user interface, driven by the simulator.

The interactive console, bat bot, finale, and dashboard are specified separately in
`docs/08-interactive-experience.md`. **Do not open that file during Track A.** The stage machine
below must work end to end with no UI before any of it is built.

---

## Stages

| # | Stage | ATT&CK tactic | Track A |
|---|---|---|---|
| 0 | Reconnaissance | TA0043 | Pre-completed in fiction; seeded as a stage-0 row |
| 1 | Network Intrusion | TA0043 / TA0007 Discovery | Simulated |
| 2 | Initial Exploit | TA0001 Initial Access / TA0006 Credential Access | Simulated |
| 3 | Lateral Movement | TA0008 / TA0004 Privilege Escalation | Simulated |
| 4 | Action on Objectives | TA0009 Collection / TA0010 Exfiltration / TA0011 C2 | Simulated |

`dim_stages` seed: `stage`, `name`, `attack_tactic_id`, `attack_tactic_name`,
`difficulty_multiplier`.

The stage machine: enter stage → select technique from the gated menu → attempt → resolve against
the probability model → on success advance, on failure decide retry or pivot → stall out when no
viable techniques remain or durability is exhausted.

---

## Technique catalog

Seed committed at `transform/seeds/techniques.csv`. 23 techniques across four stages.

| Column | Notes |
|---|---|
| `technique_id` | Internal slug |
| `attack_id` | MITRE ATT&CK technique ID |
| `attack_name` | Official name |
| `attack_tactic_id` | TA-prefixed tactic |
| `stage` | 1–4 |
| `display_name` | Label for the Track B console |
| `min_intelligence` | Primary gate |
| `min_power`, `min_strength` | Secondary gates, 0 when unused |
| `base_success_rate` | 0.0–1.0 before modifiers |
| `w_intelligence` … `w_combat` | Six stat weights, sum to exactly 1.0 |
| `noise_level` | 1–5 |
| `retry_penalty` | Success multiplier per repeat |
| `observability` | `high` / `partial` / `low` |
| `detection_signature` | What the honeypot logs when this runs |
| `produces_traffic` | Boolean, added Phase 2 — see "Does a failed attempt still hit the honeypot?" below |

**Verified against attack.mitre.org, Phase 2.** All 23 IDs, names, and tactic assignments are
correct — none wrong, none deprecated. Three are genuinely multi-tactic techniques where this
catalog's `attack_tactic_id` is one defensible choice among several, not the only one: `valid_accounts`
(T1078 — also Persistence, Privilege Escalation, Stealth), `external_remote_svc` (T1133 — also
Persistence), `input_capture` (T1056 — also Credential Access). Not silently changed to a different
valid tactic; the catalog's picks stand as reasonable choices.

### Observability — the column that makes this interesting

The honeypot is an HTTP sensor. Some techniques are loud in web logs; some are conducted entirely
off-platform or are indistinguishable from legitimate traffic by design. That is not a limitation of
the simulation, it is the actual condition of detection engineering.

| Tier | Count | Meaning | Fair expectation of the model |
|---|---|---|---|
| `high` | 7 | Distinctive, repeatable pattern in the request log | Should be recovered reliably |
| `partial` | 7 | Some signal, ambiguous or overlapping | Partial recovery expected |
| `low` | 9 | Little or nothing reaches an HTTP sensor | Near-blind; no analyst would get it either |

Reporting technique recall by tier converts a mediocre overall number into a **detection coverage
gap analysis**, which is a real deliverable security teams produce. See `docs/04-llm-triage.md`.

T1078 Valid Accounts sits at `low` for exactly the reason it is one of the most common real
initial-access techniques: a successful login with legitimate credentials looks like a successful
login. Worth calling out in the README as a genuine parallel rather than a simulation artifact.

**`low` is two different detection postures, not one bucket — split in Phase 2, once the
honeypot-traffic question below made the distinction concrete rather than theoretical.** Eight
techniques (`net_info_gather`, `identity_gather`, `open_source_search`, `phishing`,
`screen_capture`, `audio_capture`, `video_capture`, `input_capture`) never generate an HTTP request
at all — recall there is a hard ceiling, not a model failure, because no evidence exists to recover
from. One (`valid_accounts`, T1078, the case above) does land a real request; it just doesn't stand
out. Recall on that one is theoretically possible from session-level context; recall on the other
eight isn't possible from the event log at all, full stop. Carried into docs/04's detection coverage
framing and the README, not just here.

### Catalog summary

| Stage | technique_id | attack_id | ATT&CK name | min_int | observability |
|---|---|---|---|---|---|
| 1 | `port_sweep` | T1046 | Network Service Discovery | 15 | partial |
| 1 | `active_scan` | T1595 | Active Scanning | 25 | **high** |
| 1 | `net_info_gather` | T1590 | Gather Victim Network Information | 40 | low |
| 1 | `identity_gather` | T1589 | Gather Victim Identity Information | 55 | low |
| 1 | `open_source_search` | T1593 | Search Open Websites/Domains | 65 | low |
| 2 | `brute_force` | T1110 | Brute Force | 10 | **high** |
| 2 | `exploit_public_app` | T1190 | Exploit Public-Facing Application | 70 | **high** |
| 2 | `valid_accounts` | T1078 | Valid Accounts | 60 | low |
| 2 | `phishing` | T1566 | Phishing | 50 | low |
| 2 | `external_remote_svc` | T1133 | External Remote Services | 65 | partial |
| 3 | `account_discovery` | T1087 | Account Discovery | 45 | **high** |
| 3 | `remote_services` | T1021 | Remote Services | 55 | partial |
| 3 | `privesc_exploit` | T1068 | Exploitation for Privilege Escalation | 75 | partial |
| 3 | `exploit_remote_svc` | T1210 | Exploitation of Remote Services | 80 | **high** |
| 3 | `alt_auth_material` | T1550 | Use Alternate Authentication Material | 85 | partial |
| 3 | `indicator_removal` | T1070 | Indicator Removal | 70 | partial |
| 4 | `data_local_system` | T1005 | Data from Local System | 30 | **high** |
| 4 | `screen_capture` | T1113 | Screen Capture | 50 | low |
| 4 | `audio_capture` | T1123 | Audio Capture | 50 | low |
| 4 | `video_capture` | T1125 | Video Capture | 50 | low |
| 4 | `input_capture` | T1056 | Input Capture | 60 | low |
| 4 | `exfil_over_c2` | T1041 | Exfiltration Over C2 Channel | 70 | partial |
| 4 | `deploy_batbot` | T1071 | Application Layer Protocol | 40 | **high** |

**`deploy_batbot` is never attempted in Track A** — corrected in Phase 5 against real data, where it
received 0 attempts across 144 sessions and 882 stage-4 attempts. This section previously claimed it
was "attempted as an ordinary technique," which was wrong, and wrong for a reason worth stating:
technique selection is deterministic, not random. `services/simulator/session.py` opens a stage with
`candidates[0]` and pivots to `untried[0]` — strict `techniques.csv` row order — so a run ends before
reaching the tail of a long stage list. `deploy_batbot` is row 7 of 7 at stage 4. Its gate is not the
obstacle: at `min_intelligence` 40 it is *lower* than three techniques above it that are attempted
hundreds of times.

Stage-4 attempts decline monotonically with row order, which is the evidence that this is structural
rather than sampling noise:

```
row  1 data_local_system  405     row  5 input_capture   32
row  2 screen_capture     210     row  6 exfil_over_c2    0
row  3 audio_capture      153     row  7 deploy_batbot    0
row  4 video_capture       82
```

Four of the twenty-three catalog techniques are unreachable this way — `identity_gather` and
`open_source_search` (stage 1, rows 4-5), `exfil_over_c2` and `deploy_batbot` (stage 4, rows 6-7) —
one in each observability tier. Stages 2 and 3 have no unreachable techniques, because sessions make
enough attempts there to work through the whole list. The consequence for measurement is in
`docs/04-llm-triage.md`; the fix is scheduled as Phase 6's first item in `docs/06`.

The finale's wiper maps to T1561.001 / T1561.002 (Disk Wipe) and T1529 (System Shutdown/Reboot),
executed **by the Batcomputer against the villain**. Those are counterstrike events, never attempts,
and they belong to Track B.

---

## Detection signatures

Every `high` technique needs a concrete signature the honeypot emits, landing in one of the **five**
evidence features in `int_session_features_observed`. This table is the authoritative mapping and is
implemented directly in `assert_high_observability_techniques_leave_evidence`:

| Technique | Signature in the request log | Evidence feature | Status |
|---|---|---|---|
| `active_scan` | Rapid sequential path enumeration, scanner-like UA | `path_enumeration_runs` | enforced |
| `brute_force` | Repeated POST `/login` with consecutive 401s | `repeated_auth_failure_runs` | enforced |
| `exploit_public_app` | Traversal strings in path, query, or body | `traversal_pattern_count` | enforced |
| `account_discovery` | Enumeration of user and account endpoints | `path_enumeration_runs` | enforced |
| `exploit_remote_svc` | Command-injection payloads against service endpoints | `injection_pattern_count` | enforced |
| `data_local_system` | Repeated GETs on tier-3/4 data endpoints | `sensitive_data_access_runs` | enforced |
| `deploy_batbot` | Chat session initiated | `chat_turns_completed` | **excluded (Track B)** |

Three corrections made in Phase 5, all against real data:

- **`data_local_system` had no evidence feature.** It was mapped to `max_path_tier` and
  `distinct_paths`, neither of which is an evidence feature — so the technique was effectively
  uncovered. `sensitive_data_access_runs` (docs/02) was added because repeated reads of tier-3/4
  endpoints is exactly what a detector keys on for collection, so the gap was real rather than a
  test artifact.
- **`exploit_public_app` produces traversal evidence only, not both.** Its payload is
  `?file=../../etc/passwd` — pure traversal. Matching `/etc/passwd` as *injection* as well made one
  payload register as two different techniques' signatures, which would hand Phase 6's
  reconstruction injection evidence for a session that only performed traversal. Sensitive file
  targets are not injection syntax; the two matchers are now orthogonal, and a test asserts it.
- **`deploy_batbot` is excluded, structurally.** Its evidence is `chat_turns_completed`, and Track A
  produces no chat turns — it POSTs to `/api/v1/assistant`, which 404s until Track B builds the
  assistant. The exclusion lifts in Track B. (It is also never attempted at all — see above.)

`assert_high_observability_techniques_leave_evidence` enforces this: every `high` technique must
produce a non-zero evidence feature in sessions that used it. If it does not, either the tier is
wrong or the signature is not being emitted. Measured on the seeded corpus, all six enforced
mappings are non-zero.

---

## Gating

A technique appears in a villain's menu when the villain meets `min_intelligence` and any secondary
gates. Gating is derived from the seeds, never hardcoded per villain.

The story this produces:

- **Killer Croc** (intelligence 19) reaches `port_sweep` (stage 1) and `brute_force` (stage 2).
  Two blunt options, partial and high observability. He is loud and easy to reconstruct — and he
  stalls there: no stage-3 technique's `min_intelligence` is low enough for him (the lowest,
  `account_discovery`, is 45), so he never clears stage 3 and never reaches `data_local_system`.
  Verified by computing `gated_techniques` for all twelve villains, not asserted from the catalog by
  eye — an earlier draft of this section claimed he reached `data_local_system`, which the actual
  thresholds don't support. Corrected here rather than loosening a threshold to fit the sentence;
  see docs/06's Phase 3 section for the open question of whether that's the intended story.
- **Ra's al Ghul** (intelligence 100) clears every `min_intelligence` gate in the catalog — but not
  the full catalog outright: `privesc_exploit`'s secondary gate, `min_power=40`, excludes him (his
  power is 27). One cerebral, combat-100 villain who still can't force his way past a
  strength-flavored gate is arguably a better story than "reaches literally everything," and it's
  what the seed data actually produces.
- **Two-Face** (intelligence 88) reaches nearly everything, but durability 14 means he stops after
  the first failure.

**The most capable villain is the hardest to detect** — with one exception it's still cheap to
build. That falls out of the stat gating rather than being arranged, and it is worth pointing at in
the README.

**Gated technique counts per villain per stage**, computed via `gated_techniques` for all twelve —
the real distribution the "who can ever reach stage 4" question in docs/06's Phase 3 section refers
to:

```
villain           stage1  stage2  stage3  stage4
Joker                 5      4      6      7
Riddler               5      4      5      7
Bane                  5      5      6      7
Catwoman              5      3      2      6
Poison Ivy            5      4      5      7
Scarecrow             5      4      5      7
Penguin               5      4      3      7
Harley Quinn          5      4      6      7
Mister Freeze         5      5      3      7
Killer Croc           1      1      0      0
Ra's Al Ghul          5      5      5      7
Two-Face              5      4      5      7
```

Killer Croc is the only villain with an empty stage-3 set — everyone else, including the lowest
non-Croc count (Catwoman, 2), has *some* path forward. This isn't a marginal case worth hand-waving
past; it's a structural, one-villain outlier that Phase 3 needs to look at directly.

**A note on TA0005:** MITRE has renamed this tactic from "Defense Evasion" to "Stealth" (and split
off a new "Defense Impairment," TA0112) since this catalog was drafted. `indicator_removal` (T1070)
is still correctly tagged `TA0005` — the ID didn't move, only the tactic's display name. Mentioned
here in case a reader familiar with the classic ATT&CK tactic names does a double-take.

---

## Success probability model

```
p = base_success_rate
    × (Σ stat_weight_i × villain_stat_i / 100) normalized to [0.5, 1.5]
    × retry_penalty ^ (attempts_on_this_technique)
    × stage_difficulty_multiplier
```

Clamp to [0.02, 0.95]. Nothing certain, nothing impossible.

The weighted stat sum's raw range is `[0, 1]` (weights sum to exactly 1.0, each stat/100 is
`[0, 1]`), stretched linearly to `[0.5, 1.5]` as `0.5 + raw`. `attempts_on_this_technique` in the
retry-penalty exponent is `technique_attempt_seq - 1` — 0 on the first attempt, so no penalty
before any repetition has happened. Neither was pinned down precisely enough to implement from this
doc alone; both decided in Phase 2 (`services/simulator/probability.py`).

`retry_penalty` (default 0.68–0.95 per technique) creates the interesting decision. Repeating gets
worse; pivoting needs alternatives. Killer Croc grinds because gating leaves him nothing to pivot
to, producing the highest `retry_ratio` (confirmed against real runs, Phase 3). The highest
`pivot_ratio` is **Joker's**, not Ra's al Ghul's — an earlier draft had that backwards. A villain who
pivots on every failure still only accumulates pivot decisions if it *fails often*; Ra's al Ghul's
combat 100 clears stages fast, so he makes few decisions of either kind. His actual discriminator is
reaching tier 4 in very few requests with near-zero waste, not pivot volume. `pivot_ratio` /
`retry_ratio` only discriminate within the frequently-failing population — see docs/03.

Record `computed_probability`, `roll`, and `outcome` on every attempt so
`assert_probability_calibration` can verify observed success rates converge on computed
probabilities over 30+ attempts. **That test validates the simulation itself**, which is an unusual
thing to have and worth mentioning.

### What makes an attempt `detected`?

Named in the `outcome` enum below, never defined in this doc until now. Decided in Phase 2:
`detected` is **orthogonal to success/failure**, not a subtype of either — a loud attempt can
succeed and still be flagged detected, matching how a real security team catches loud failures and
loud successes alike.

```
noise_generated = noise_level × technique_attempt_seq
detection_probability = clamp(noise_generated / 10, 0, 0.9)
outcome = 'detected' if detection_roll < detection_probability
          else ('success' if roll < computed_probability else 'failure')
```

Two independent rolls internally (detection and success) — but only `roll` (the success roll) is
persisted on the attempt event, matching the schema below, which has exactly one `roll` field, not
two. Detection evidence is instead carried by `noise_generated`, which is already in the schema.

**Deliberately simple, and a Phase 3 recalibration point** (docs/06): uses only the catalog's static
`noise_level`, not villain intelligence — Layer 1 above says high intelligence "adds timing jitter"
and reduces noise, which belongs to `BehaviorProfile`, not this phase's pure stage-machine
mechanics.

### Does a failed attempt still generate HTTP traffic?

Decided in Phase 2 via a new `produces_traffic` column on the catalog (`transform/seeds/
techniques.csv`): whether a technique touches the honeypot at all is a fixed property of the
technique, independent of the attempt's outcome. All `high` and `partial` techniques require an
HTTP-visible pattern by definition → always `true`; outcome only changes what the honeypot returns,
never whether a request happens. `low` splits — see "Observability" above — 8 are genuinely
off-platform → always `false`; `valid_accounts` is on-platform but camouflaged → `true`.

### Wire formats this phase invented

The spec left these open; decided and built in Phase 2, both on `services/honeypot/app.py`:

| Header | Purpose |
|---|---|
| `X-Attempt-Id` (request) | The simulator tags every driven HTTP request with the attempt that generated it, threaded into the emitted `request` event's `attempt_id` — the actual correlation mechanism `mart_detection_correlation` depends on. |
| `X-Session-Id` (response) | The honeypot echoes back the session_id it derived for this request. The simulator reads it off a bootstrap call before starting the stage machine, since it doesn't control session_id itself (docs/02: gap-based, keyed on source_ip/user_agent) but `attack_attempts` and `attack_events` join on it. |

---

## Attempt event fields

Beyond the shared envelope in `docs/02-data-model.md`:

| Field | Notes |
|---|---|
| `attempt_id` | uuid |
| `stage` | 1–4 |
| `technique_id`, `attack_id` | catalog reference |
| `attempt_seq` | sequence within the session |
| `technique_attempt_seq` | sequence within this technique |
| `decision` | `initial` / `retry` / `pivot` |
| `parameters` | JSON of whatever was tuned. Always `{}` in Phase 2 — there's nothing to tune yet without per-villain behavioral richness; Phase 3 populates it. |
| `computed_probability`, `roll` | float |
| `outcome` | `success` / `failure` / `detected` |
| `noise_generated` | int |
| `stage_entered_at`, `attempt_at` | timestamps |

The honeypot also emits the technique's `detection_signature` as ordinary HTTP traffic carrying the
same `attempt_id`. Two views of one action: what the attacker did, and what the sensor saw.
`mart_detection_correlation` joins them.

**`attempt_id` is stripped from every model tagged `triage_input`.** It is an evaluation join key,
not a feature. Attempts are ground truth. See "The observed / truth boundary" in
`docs/02-data-model.md`.
