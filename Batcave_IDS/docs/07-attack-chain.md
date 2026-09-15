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

**Verify every `attack_id` against attack.mitre.org during Phase 2.** The Impact-tactic IDs were
checked; the rest were not. A wrong ID is worse than no ID, since the whole point is that the
mapping is real.

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

`deploy_batbot` is attempted in Track A as an ordinary technique. It just produces no conversation
until Track B exists.

The finale's wiper maps to T1561.001 / T1561.002 (Disk Wipe) and T1529 (System Shutdown/Reboot),
executed **by the Batcomputer against the villain**. Those are counterstrike events, never attempts,
and they belong to Track B.

---

## Detection signatures

Every `high` technique needs a concrete signature the honeypot emits, landing in one of the four
evidence features in `int_session_features_observed`:

| Technique | Signature in the request log | Evidence feature |
|---|---|---|
| `active_scan` | Rapid sequential path enumeration, scanner-like UA | `path_enumeration_runs` |
| `brute_force` | Repeated POST `/login` with consecutive 401s | `repeated_auth_failure_runs` |
| `exploit_public_app` | Traversal and injection strings in path, query, or body | `traversal_pattern_count`, `injection_pattern_count` |
| `account_discovery` | Enumeration of user and account endpoints | `path_enumeration_runs` |
| `exploit_remote_svc` | Exploit payloads against service endpoints | `injection_pattern_count` |
| `data_local_system` | Repeated GETs on tier-3/4 data endpoints | `max_path_tier`, `distinct_paths` |
| `deploy_batbot` | Chat session initiated | `chat_turns_completed` |

`assert_high_observability_techniques_leave_evidence` enforces this: every `high` technique must
produce a non-zero evidence feature in sessions that used it. If it does not, either the tier is
wrong or the signature is not being emitted.

---

## Gating

A technique appears in a villain's menu when the villain meets `min_intelligence` and any secondary
gates. Gating is derived from the seeds, never hardcoded per villain.

The story this produces:

- **Killer Croc** (intelligence 19) reaches `port_sweep`, `brute_force`, and `data_local_system`.
  Three blunt options, all high or partial observability. He is loud and easy to reconstruct.
- **Ra's al Ghul** (intelligence 100) reaches everything, including the quiet techniques.
- **Two-Face** (intelligence 88) reaches nearly everything, but durability 14 means he stops after
  the first failure.

**The most capable villain is the hardest to detect.** That falls out of the stat gating rather than
being arranged, and it is worth pointing at in the README.

---

## Success probability model

```
p = base_success_rate
    × (Σ stat_weight_i × villain_stat_i / 100) normalized to [0.5, 1.5]
    × retry_penalty ^ (attempts_on_this_technique)
    × stage_difficulty_multiplier
```

Clamp to [0.02, 0.95]. Nothing certain, nothing impossible.

`retry_penalty` (default 0.68–0.95 per technique) creates the interesting decision. Repeating gets
worse; pivoting needs alternatives. Killer Croc grinds because he has nothing else, producing the
highest `retry_ratio`. Ra's al Ghul pivots immediately, producing the highest `pivot_ratio`.

Record `computed_probability`, `roll`, and `outcome` on every attempt so
`assert_probability_calibration` can verify observed success rates converge on computed
probabilities over 30+ attempts. **That test validates the simulation itself**, which is an unusual
thing to have and worth mentioning.

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
| `parameters` | JSON of whatever was tuned |
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
