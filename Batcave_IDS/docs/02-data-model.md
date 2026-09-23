# 02 — Data model

Three grains joining on `session_id`: HTTP request events, attack attempts, and bat bot chat turns.
More than one fact grain is closer to real warehouse work than a single event stream.

**The central structural rule of this project:** requests and chat turns are what the sensor
*observed*. Attempts and runs are *ground truth*. Nothing derived from ground truth may reach the
triage model. See "The observed / truth boundary" below — get this wrong and every evaluation
number in the repository is worthless.

---

## Shared event envelope

Every Kafka message carries this, discriminated by `event_kind`
(`request` | `attempt` | `chat_turn` | `counterstrike` | `attack_run`).

> **`attack_run` is a fifth kind, added in Phase 3.** The original design listed four and kept
> `attack_runs` as a separate ground-truth source without saying how it's transported. It's published
> through the same topic as a distinct `event_kind` so there's one data path (Kafka → consumer →
> Parquet → dbt) and Phase 4's consumer lands it like the others. It carries `villain_slug` — the
> ground-truth identity the request/attempt envelopes must never contain — which is fine: it's landed
> separately and tagged `ground_truth` in dbt, and `assert_no_ground_truth_leakage` keeps it out of
> triage. It also carries `timing_compression_factor` (Phase 3) so a reader can tell, from the data
> alone, whether a run's timing was real or compressed.

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `event_id` | string (uuid) | no | Deduplication key. Will legitimately appear twice — at-least-once delivery. |
| `event_kind` | string | no | Discriminator |
| `run_id` | string (uuid) | yes | Links to `attack_runs` |
| `session_id` | string | no | Kafka message key |
| `received_at` | timestamp (UTC) | no | **Only trustworthy timestamp. Partition and order on this.** |
| `client_ts` | timestamp | yes | Attacker-supplied, deliberately unreliable |
| `schema_version` | string | no | Starts `v1`; bumped mid-run to exercise drift handling |

Consumer adds: `kafka_partition`, `kafka_offset`, `landed_at`.

**Never in any of these:** the villain identity. It lives only in `attack_runs`.

### Session derivation

`session_id` is derived by the honeypot (`services/honeypot/session.py`) from an **opaque session
cookie** (`batcave_sid`), gap-enforced. On first contact the honeypot mints a random token, sets it
as the cookie, and uses it as the `session_id`. A request presenting a known cookie continues that
session — **whatever its source IP or user agent** — while the gap rule still applies on top: a
cookie whose last request is older than `SESSION_GAP_SECONDS` (default **120s**, env-configurable)
starts a fresh session rather than resuming. Gaps are measured against `received_at` only, never
`client_ts`. A request with no cookie, or an unknown/expired one, mints a fresh session, so curl and
manual tests need no cookie handling.

**This is the second change to session derivation, and it exists because rotation breaks a key-based
scheme.** Phase 1 keyed on `(source_ip, user_agent)` with the same gap rule (itself a correction of
docs/06's original "time bucket," which would have fragmented any session straddling a fixed
window). But Phase 3's villains *rotate* those identifiers: Penguin rotates his source IP within a
run ("he sends henchmen"), high-intelligence villains rotate their user agent as evasion. Under an
`(ip, ua)` key, each rotation would start a new session — which is exactly backwards, since
`distinct_source_ips` and `distinct_user_agents` (below) are meant to *detect* the rotation within
one session. Keeping the key would have meant deleting two features and two villain signatures. The
cookie carries identity through rotation; the rotation still shows up in the counts.

State is a small in-memory `dict` in the honeypot process, keyed on the token, with periodic
eviction of stale sessions. A real (non-portfolio) deployment running more than one honeypot
replica, or wanting sessions to survive a restart, would externalize this — Redis or similar.

**Known simplification, stated plainly.** A session cookie hands the defender continuity that a real
rotating-attacker scenario would not give them: an attacker deliberately rotating IPs and user
agents to evade correlation is unlikely to also carry a cookie that re-links their requests. Real
sessionization under adversarial identifier rotation is probabilistic and genuinely hard
(behavioral fingerprinting, timing correlation, TLS fingerprints). This project uses a cookie as a
deliberate simplification so the rotation *features* can exist and be evaluated, not because cookie
continuity is realistic under evasion. Said here rather than left implied. (docs/03 notes a possible
later enhancement: intelligence-driven cookie-dropping, where the most capable villains fragment
themselves on purpose — a better model, out of scope for Phase 3.)

**120s is a Phase 3 recalibration point.** It was set against the spec's own reference run length
(Phase 3 checkpoint: "run all twelve for 120 seconds"), not against any villain's actual measured
inter-request gaps. Now that the cookie carries identity through rotation, the gap only matters for a
villain that genuinely goes quiet mid-run — so it's re-confirmed against real per-villain run
durations in Phase 3, especially Mister Freeze (longest session) and Poison Ivy (slow drip).

**Phase 8 raises it for the console stack, not for the default.** A human reading a technique
menu routinely exceeds 120s of cookie inactivity, at which point the honeypot would mint a *fresh*
`session_id` mid-run — the console's attempt events (carrying the session_id from warm-up) would
stop matching the request events the player's own next click generates, breaking the
session/attempt correlation the whole data model rests on. The fix is the existing
`SESSION_GAP_SECONDS` env var, set to `900` for a console session (`docs/08`'s Makefile wiring),
not a change to the honeypot's gap logic itself — a human reading a menu for fifteen minutes is
still one session; longer than that and starting a fresh one is the right call. `make attack`/
`make attack-all` keep the 120s default, since an autopilot run never idles.

---

## The observed / truth boundary

| Side | Contains | Tag | May reach the LLM? |
|---|---|---|---|
| **Observed** | `attack_events`, `botchat_turns`, everything derived from them | `triage_input` | Yes |
| **Ground truth** | `attack_runs`, `attack_attempts`, everything derived from them | `ground_truth` | **Never** |

This is what makes both evaluations meaningful. The model sees what the honeypot's sensor saw and
must reconstruct both *who* attacked and *what techniques they used*. Ground truth exists only to
score that reconstruction.

Practical consequences:

- `attempt_id` appears on request events for correlation, but is **stripped** from anything tagged
  `triage_input`. It is a join key for evaluation, not a feature.
- Stage progression, technique IDs, outcomes, rolls, retry and pivot counts are all ground truth.
  None of them may appear in a triage-input feature.
- The two feature models are separate by construction: `int_session_features_observed` and
  `int_session_features_truth`. Do not merge them for convenience.

### `assert_no_ground_truth_leakage` — a lineage test, not a column test

A column-name check is defeated by renaming. Test the dbt manifest instead:

> For every model tagged `triage_input`, walk its full upstream lineage. Fail if any ancestor is
> tagged `ground_truth`, or is one of `stg_attack_attempts`, `stg_attack_runs`,
> `int_stage_progression`, or `int_session_features_truth`.

Implement by reading `target/manifest.json` in a pytest test rather than as a dbt singular test, so
it runs in CI even when the warehouse is empty. Run it as a **named CI step** so a reader sees it in
the log. This is an uncommon thing to find in a portfolio repository and worth surfacing
deliberately.

---

## Fact sources

### `attack_events` (`event_kind = 'request'`) — OBSERVED

| Field | Notes |
|---|---|
| `source_ip` | Nullable by design. Rotates within a session for Penguin. |
| `user_agent` | Rotates for high-intelligence villains |
| `http_method`, `path`, `query_string` | |
| `path_tier` | Sensitivity 0–4 from the route table |
| `request_body` | Raw; may be malformed JSON |
| `body_bytes`, `status_returned`, `response_time_ms` | `response_time_ms` occasionally negative by design |
| `headers` | JSON string |
| `attempt_id` | Correlation key. **Stripped from `triage_input` models.** |

### `attack_attempts` (`event_kind = 'attempt'`) — GROUND TRUTH

`attempt_id`, `stage`, `technique_id`, `attack_id`, `attempt_seq`, `technique_attempt_seq`,
`decision` (`initial`/`retry`/`pivot`), `parameters`, `computed_probability`, `roll`, `outcome`,
`noise_generated`, `stage_entered_at`, `attempt_at`. Full definitions in `docs/07-attack-chain.md`.

### `botchat_turns` (`event_kind = 'chat_turn'`) — OBSERVED

`turn_number`, `speaker`, `objective`, `bot_text`, `user_text`, `extracted_intent_flags`, `refused`,
`latency_ms`, `input_tokens`, `output_tokens`.

`user_text` is **excluded from the committed sample partition without exception.**

### `counterstrike_events` (`event_kind = 'counterstrike'`)

`sequence`, `readout_line`, `attributed_villain_slug`, `attributed_confidence`, `attack_id`.
Attribution comes from the **triage model's prediction**, not ground truth, so a wrong prediction
produces a wrong accusation.

### `attack_runs` — GROUND TRUTH

`run_id`, `villain_slug`, `started_at`, `ended_at`, `duration_s`, `requests_sent`, `attempts_made`,
`max_stage_reached`, `run_outcome`, `pathologies_enabled`, `timing_compression_factor` (Phase 3 —
1.0 = faithful pacing, smaller = idle gaps compressed for speed; see docs/05 "Timing model"),
`session_source` (Phase 8 — `headless` | `console`; see docs/08's "Console session data
provenance"). Published as `event_kind = 'attack_run'`, keyed by `run_id`, which the run's request
and attempt events also carry so the observed session joins to its ground truth.

---

## `dim_villains` — confirmed roster

Twelve villains verified present in the akabab dataset (563 characters). Seed at
`transform/seeds/villains.csv`.

| slug | name | archetype | INT | STR | SPD | DUR | PWR | CMB |
|---|---|---|---|---|---|---|---|---|
| `370-joker` | Joker | chaotic | 100 | 10 | 12 | 60 | 43 | 70 |
| `558-riddler` | Riddler | cerebral | 100 | 10 | 12 | 14 | 10 | 14 |
| `60-bane` | Bane | brute | 88 | 38 | 23 | 56 | 51 | 95 |
| `165-catwoman` | Catwoman | stealth | 69 | 11 | 33 | 28 | 27 | 85 |
| `522-poison-ivy` | Poison Ivy | stealth | 81 | 14 | 21 | 40 | 100 | 40 |
| `576-scarecrow` | Scarecrow | cerebral | 81 | 10 | 12 | 14 | 48 | 50 |
| `514-penguin` | Penguin | cerebral | 75 | 10 | 12 | 28 | 30 | 45 |
| `309-harley-quinn` | Harley Quinn | chaotic | 88 | 12 | 33 | 65 | 55 | 80 |
| `457-mister-freeze` | Mister Freeze | methodical | 75 | 32 | 12 | 70 | 37 | 28 |
| `386-killer-croc` | Killer Croc | brute | 19 | 53 | 35 | 90 | 53 | 60 |
| `538-ras-al-ghul` | Ra's Al Ghul | methodical | 100 | 28 | 32 | 42 | 27 | 100 |
| `678-two-face` | Two-Face | cerebral | 88 | 10 | 12 | 14 | 9 | 28 |

Clayface and Mad Hatter are absent from the dataset under any name or alias. Man-Bat is present
(`427-man-bat`) but excluded by choice. Note both in the README.

All six powerstats are used. On four stats the closest pair (Riddler / Two-Face) is at distance 12.0
with three pairs under 20; on six stats it is 18.5 with one pair under 20, and median pair distance
rises from 55.2 to 69.4.

**Archetypes:** cerebral (Riddler, Two-Face, Scarecrow, Penguin), brute (Bane, Killer Croc), chaotic
(Joker, Harley Quinn), stealth (Catwoman, Poison Ivy), methodical (Ra's al Ghul, Mister Freeze).
The four closest stat pairs are all within-archetype, which is why the tiered metric in
`docs/04-llm-triage.md` is informative rather than a consolation prize.

## `dim_techniques`

Seed at `transform/seeds/techniques.csv`. Schema, catalog, and observability tiers in
`docs/07-attack-chain.md`. The `observability` column (`high` / `partial` / `low`) is what makes
detection coverage analysis possible and is central to how technique recall is reported.

## `dim_stages`

`stage`, `name`, `attack_tactic_id`, `attack_tactic_name`, `difficulty_multiplier`.

---

## dbt layers

Adapter: `dbt-duckdb`. Single target.

### staging

- **`stg_attack_events`** `[triage_input]` — types, dedupe on `event_id` keeping earliest
  `received_at`, safe JSON parse setting `body_is_valid_json`, `is_quarantined` +
  `quarantine_reason` for null `event_id`, null `received_at`, null `path`, or future `received_at`
- **`stg_botchat_turns`** `[triage_input]`
- **`stg_attack_attempts`** `[ground_truth]`, **`stg_attack_runs`** `[ground_truth]`
- **`stg_counterstrike_events`**, **`stg_villains`**, **`stg_techniques`**, **`stg_stages`**

Quarantined rows are never dropped. They flow to `fct_quarantined_events` with reason codes.

### intermediate

**`int_session_events`** `[triage_input]` — per-event windows over `session_id` ordered by
`received_at`: `prev_received_at`, `request_seq`, `tier_reached_so_far`.

**`int_session_features_observed`** `[triage_input]` — the triage model's entire world.

| Feature | Signal |
|---|---|
| `request_count`, `requests_per_min`, `duration_s` | speed, durability |
| `inter_request_stddev_ms` | automation / jitter |
| `distinct_paths`, `path_entropy` | intelligence |
| `max_path_tier`, `time_to_tier3_s`, `wasted_request_ratio` | combat |
| `error_ratio`, `requests_after_first_error` | durability |
| `distinct_user_agents`, `null_ip_ratio` | evasion |
| `distinct_source_ips` | Penguin signature |
| `mean_body_bytes`, `max_body_bytes` | strength |
| `body_bytes_trend` | Poison Ivy signature |
| `invalid_body_ratio` | power / signature |
| `exact_duplicate_path_pairs` | Two-Face signature |
| `riddle_param_count` | Riddler signature |
| `traversal_pattern_count`, `injection_pattern_count` | evidence for exploit techniques |
| `repeated_auth_failure_runs` | evidence for brute force |
| `path_enumeration_runs` | evidence for scanning and discovery |
| `sensitive_data_access_runs` | evidence for collection (Phase 5 — see below) |
| `chat_turns_completed`, `probe_engagement_ratio`, `intent_flags_triggered` | bot chat — **structurally zero in Track A** |
| `late_arrival_count` | pathology |

The **five** evidence features exist so technique reconstruction has something to reason over. They
are derived purely from request patterns, never from the attempt log. The authoritative
technique → evidence mapping lives in `docs/07-attack-chain.md` ("Detection signatures") and is
implemented directly in `assert_high_observability_techniques_leave_evidence`.

**`sensitive_data_access_runs` was added in Phase 5**, not for symmetry but because a gap was real:
of the seven `high` observability techniques, `data_local_system` had no covering evidence feature.
docs/07 had mapped it to `max_path_tier` and `distinct_paths`, neither of which is an evidence
feature, so the technique was effectively uncovered by the test that exists to catch exactly that.
Repeated reads of tier-3/4 endpoints is what a detector keys on for collection and exfiltration.

**The three chat features are structurally zero in Track A** and will stay that way until the bat bot
exists (Track B, docs/08) — nothing produces `chat_turn` events, so `stg_botchat_turns` is a typed
empty relation. They are carried anyway, deliberately: `int_session_features_observed` is the triage
model's entire input contract, and if these appeared in Phase 6 but not Phase 5 then every Track A
accuracy number would be measured against a different feature vector than every Track B number, with
nothing flagging it. **A Phase 6 result showing them uninformative is therefore expected, not a
finding.** They are excluded from `mart_threat_scores`' components, and
`assert_chat_features_excluded_from_threat_score` enforces that so Track B landing real values cannot
silently shift historical scores.

#### Feature-definition ambiguity pass (Phase 3)

Three feature definitions already drifted from their prose during Phases 2–3 (`wasted_request_ratio`
twice, the `pivot_ratio`/`retry_ratio` claims). A pass over the rest flagged the features whose prose
implies a property the computation cannot express alone. **Phase 5 must lift the resolved definition,
not the prose — an ambiguous feature becomes an ambiguous dbt model.** For each: `[def]` a definition
that must be pinned, `[doc]` a wording fix, `[pair]` only meaningful alongside another feature.

- **`wasted_request_ratio`** `[pair]` — resolved: fraction of requests not increasing
  `tier_reached_so_far`, counted only up to first reaching the session's peak tier. Measures
  efficiency *to its own peak*, so it only tells "efficient vs flailing" **paired with
  `max_path_tier`** (how high).

  **Ablation superseded in Phase 6 — the "weight lightly, Riddler-only" guidance below no longer
  applies, and Part B does not use it.** The Phase 3 ablation (`error_ratio` load-bearing for 11 of
  66 pairs, `wasted_request_ratio` for 2 of 66, both Riddler pairs) was measured on a corpus with
  deterministic technique selection at `time_scale=0.02`. Re-run on the Phase 6 corpus (randomized
  selection, `time_scale=0.2`) both features show **0 of 66 pairs load-bearing**. Selection
  randomization and the clock change landed together in that re-run, so the shift can't be
  attributed to one alone without a third corpus — noted as confounded, not as either feature having
  stopped mattering. Since neither ablation result singles either feature out anymore, both are
  weighted per their ordinary role in the feature set: `error_ratio` keeps its docs/02 threat-score
  weight (10, unchanged — it was never conditioned on the stale ablation), and
  `wasted_request_ratio` (not a threat-score component; a prompt/baseline-classifier signal) is no
  longer downweighted or scoped to Riddler-like cases.
  <br>*Original Phase 3 measurement, superseded above: load-bearing for the Riddler pairs; keep,
  weight lightly.*
- **`exact_duplicate_path_pairs`** `[def]` — the name says "pairs" but the working definition is the
  count of *paths that appear more than once* in the session (paths with count > 1), not the number
  of duplicate pairs (`n choose 2`). Phase 5 must implement the paths-with-duplicates count and the
  name should be read that way, or renamed.
- **`body_bytes_trend`** `[def]` — "trend" is computed as the Pearson correlation of `body_bytes`
  with request order (guarded to 0 for constant bodies), not a slope or last-minus-first. State it;
  the three would rank villains differently.
- **`time_to_tier3_s`** `[def]` — undefined for a session that never reaches tier 3 (most stallers).
  Needs an explicit convention (null, and excluded from aggregates — not 0, which reads as "instant").
- **`repeated_auth_failure_runs`, `path_enumeration_runs`, `sensitive_data_access_runs`** `[def]` —
  **RESOLVED in Phase 5.** "Runs" means maximal streaks of consecutive requests ordered by
  `received_at` within a session, and each feature is the **count of runs**, not their total length —
  counting matching requests instead would just re-count volume, which `request_count` already
  carries. Minimum lengths are dbt vars, and each was chosen from the observed distribution on the
  seeded corpus (144 sessions, 2,517 clean requests) rather than picked:

  | Feature | Predicate | Min length | Observed run-length distribution | Why that cut |
  |---|---|---|---|---|
  | `repeated_auth_failure_runs` | `status_returned = 401` | **3** | 1×88, 2×11, 3×1, 4×1, 5×6, 41×1, 42×7, 43×4 | Cleanly bimodal. 99 runs of length 1–2 are incidental single failures; everything ≥3 is deliberate, and the cluster at 41–43 is Killer Croc's brute-force hammering. |
  | `path_enumeration_runs` | path not seen before in this session | **3** | 1×301, 2×43, 3×43, 4×34, 5×23, 6×5, 7×4, 9×1 | Length 1 is every session's first request and any isolated new path — 301 of them, pure noise. `active_scan` emits 4 distinct paths in sequence, so 3 is the shortest run that reads as enumeration rather than ordinary browsing. |
  | `sensitive_data_access_runs` | `path_tier >= 3` | **2** | 1×100, 2×56, then a long tail to 56 | No bimodal split, so the cut comes from the technique's own detection signature: "**repeated** GETs against tier-3/4 endpoints". A single sensitive read is not repetition; 2 is the minimum that is. |

  A threshold with no stated basis is the next ambiguity flag, which is why the distributions are
  recorded here and not just the numbers. These three features are what Phase 6's technique
  reconstruction rests on.
- **`traversal_pattern_count`, `injection_pattern_count`** `[def]` — **RESOLVED in Phase 5**, in
  `transform/macros/attack_patterns.sql`, pinned to what the simulator actually emits
  (`_REQUEST_SPECS` in `services/simulator/traffic.py`): `?file=../../etc/passwd` for
  `exploit_public_app`, `?cmd=;cat%20/etc/shadow` for `exploit_remote_svc`. Two details decide
  whether these ever match: `query_string` is stored **raw**, so the injection payload keeps its
  `%20`; and the traversal marker lives in the **query**, not the path.
  `tests/test_dbt_attack_patterns.py` imports those payloads rather than copying them and asserts
  each matcher fires, so a changed payload fails at the seam instead of surfacing later as an
  unexplained zero.
  **The two matchers are deliberately orthogonal.** `/etc/passwd` and `/etc/shadow` are *targets*,
  not injection *syntax*; matching them as injection made `exploit_public_app`'s traversal payload
  register as both, which would hand Phase 6 injection evidence for a session that only performed
  traversal. Caught by the test, not by review.
- **`requests_per_min`** `[def]` — **RESOLVED**: duration floor of 0.001s **and** a 600/min cap, both
  in SQL. The floor alone is not enough, and the cap is not decoration: the harness applies it in
  Python *after* its SQL (`separability._REQ_PER_MIN_CAP`), so lifting only the SQL diverges on
  exactly the burst sessions the cap exists for. Note that at `time_scale = 0.02` the compressed idle
  gaps push **132 of 144 sessions to the cap**, leaving the feature saturated and near-uninformative —
  the same compressed-clock distortion docs/05 records for `inter_request_stddev_ms`. Faithful timing
  is needed for it to carry signal.

**`int_session_features_truth`** `[ground_truth]` — evaluation only.

`max_stage_reached`, `stages_cleared`, `total_attempts`, `total_successes`, `overall_success_rate`,
`distinct_techniques_used`, `technique_id_set`, `attack_id_set`, `tactic_set`, `retry_ratio`,
`pivot_ratio`, `mean_technique_min_intelligence`, `total_noise_generated`, `time_to_max_stage_s`,
`abandoned_after_failures`.

**`int_stage_progression`** `[ground_truth]` — one row per session-stage: `attempts_in_stage`,
`successes_in_stage`, `first_attempt_at`, `stage_cleared_at`, `dwell_time_s`, `pivots_in_stage`,
`retries_in_stage`, `stage_cleared`.

### marts

**Phase is noted on every entry.** This list previously gave none, which is what made the Phase 5/6
boundary ambiguous — several of these depend on triage output that does not exist until Phase 6.

Dimensions (Phase 5, untagged shared dimensions): `dim_villains`, `dim_techniques`, `dim_stages`.

Facts (Phase 5): `fct_attack_events` `[triage_input]`, `fct_attack_attempts` `[ground_truth]`,
`fct_stage_progression` `[ground_truth]`, `fct_botchat_turns` `[triage_input]`, `fct_attack_runs`
`[ground_truth]`, `fct_quarantined_events` (untagged — an operational data-quality fact, neither a
model input nor an answer key). `fct_intervention_orders` is **Phase 6** (triage output).
`fct_sessions` is folded into `int_session_features_observed` / `_truth` rather than built
separately — a third session-grain model would be a third place for the boundary to leak.

Analytic marts:

- **`mart_threat_scores`** `[triage_input]` — **Phase 6.** Composite score from observed features only
- **`mart_killchain_funnel`** `[ground_truth]` — **Phase 5.** Sessions entering and clearing each
  stage, conversion by villain and archetype
- **`mart_technique_efficacy`** `[ground_truth]` — **Phase 5.** Per technique per villain: attempts,
  successes, observed rate, mean computed probability, delta
- **`mart_detection_correlation`** `[ground_truth]` — **Phase 5.** Joins attempts to the requests they
  generated via `attempt_id`, reporting evidence volume per technique. This is where `observability`
  tiers get validated against actual log output, and it mirrors real detection engineering.
- **`mart_reconciliation`** `[ground_truth]` — **Phase 5.** The counts table from
  `docs/03-attack-simulation.md`

Evaluation marts — **all Phase 6**, since each consumes triage predictions:

- **`fct_triage_evaluations`** — villain identification, three levels
- **`fct_technique_evaluations`** — per session per technique: predicted, actual, true/false
  positive/negative, joined to `observability`
- **`mart_detection_coverage`** — technique recall grouped by observability tier. The headline
  result of the whole project. Recall is measured over **reachable** techniques (19 of 23) — see
  `docs/04-llm-triage.md`.

`assert_threat_score_bounds` is likewise Phase 6: it tests a model that does not exist yet.

### Threat score

From observed features only. Weighted, 0–100, each component its own column:

| Component | Weight |
|---|---|
| `max_path_tier` normalized | 25 |
| `path_enumeration_runs` + `distinct_paths` normalized | 15 |
| `injection_pattern_count` + `traversal_pattern_count` | 15 |
| `repeated_auth_failure_runs` | 10 |
| `error_ratio` | 10 |
| inverse `inter_request_stddev_ms` | 10 |
| evasion (`null_ip_ratio` + UA rotation) | 10 |
| `requests_per_min` (log-scaled, capped) | 5 |

Triage threshold ≥ 60, as a dbt variable.

**Calibration note:** Catwoman and Ra's al Ghul clear stages fast with very few requests. If they
fall below threshold, that is a real detector failure mode worth writing up, not a bug to hide.

---

## Tests

**Generic:** `unique` + `not_null` on every PK; `relationships` from
`fct_attack_attempts.technique_id` → `dim_techniques`, `fct_attack_runs.villain_slug` →
`dim_villains`; `accepted_values` on `event_kind`, `stage`, `decision`, `outcome`, `path_tier`,
`archetype`, `observability`, `quarantine_reason`.

**Custom:**

- `assert_no_ground_truth_leakage` — **the lineage test above. Pytest over the dbt manifest, named
  CI step.**
- `assert_no_attempt_id_in_triage_input.sql` — belt and braces on the correlation key
- `assert_technique_gating_respected.sql` — no attempt uses a technique whose `min_intelligence`
  exceeds the villain's. Catches simulator bugs directly.
- `assert_probability_calibration.sql` — for techniques with 30+ attempts, observed success rate
  falls within tolerance of mean computed probability. **Validates the simulation itself.**
- `assert_stage_monotonic.sql` — no session enters stage N+1 without clearing stage N
- `assert_twoface_duplicates_survive.sql` — Two-Face's deliberate duplicate requests have distinct
  `event_id`s and must survive deduplication, or the villain disappears. **Scoped in Phase 6** to
  sessions with at least one traffic-producing attempt — there's nothing to duplicate otherwise. The
  reason the scoping exists is more interesting than the test: before Phase 6, technique selection
  was deterministic (`techniques.csv` row order), and stage 1's row 0 was always a traffic-producing
  technique, so every Two-Face session touched the honeypot at least once *by accident of catalog
  order*, not by any real guarantee. Randomizing selection (the fix for 4 unreachable techniques,
  above) removed that accident: a session can now stall entirely on `net_info_gather` /
  `open_source_search` / `phishing` — 3 of stage 1's 5 techniques don't touch the honeypot at all —
  before ever making a request. Two-Face's low durability (14) means he gives up fast; this is what
  fast can look like. Legitimate corpus behavior, not a regression, but it means "every Two-Face
  session shows a duplicate" was never quite the right invariant — "every Two-Face session that
  touched the honeypot shows a duplicate" is. `assert_twoface_test_is_not_vacuous.sql` guards the
  scoping itself: at least half of Two-Face's sessions must qualify (a fraction, not a fixed count,
  so it scales with corpus size), or a future bug that made every Two-Face session traffic-free would
  leave the duplicate-survival test green while checking nothing.

  **This is the first of two confirmed instances of the same confusion**, both on
  `exact_duplicate_path_pairs`. The second: Phase 6's rule-based baseline used the feature as a hard
  discriminator for Two-Face (`>= 2`) and it fired on 143 of 381 real sessions - Killer Croc's
  retry-grinding produces a nearly identical mean (2.12 vs Two-Face's 2.21), because the feature
  can't tell "duplicated on purpose" from "duplicated by grinding." The discriminator was dropped
  in favor of nearest-centroid classification (`services/triage/baseline.py`) after three narrower
  single-feature fixes all failed to separate them. Two independent code paths, two different
  villain-facing symptoms (a flaky dbt test, an over-firing classifier rule), same root cause both
  times: this is a property of the Two-Face/Killer-Croc pair on this feature, not two unrelated bugs
  - see `docs/03-attack-simulation.md`'s Layer 2 section for the full comparison.
- `assert_high_observability_techniques_leave_evidence.sql` — every technique marked `high` produces
  a non-zero evidence feature in the sessions that used it. If it does not, either the tier is wrong
  or the detection signature is not being emitted.
- `assert_no_user_text_in_sample.sql` — committed sample partition contains no `user_text`
- `assert_twelve_villains_seeded.sql`, `assert_no_future_timestamps.sql`,
  `assert_no_negative_durations.sql`, `assert_threat_score_bounds.sql`,
  `assert_session_ordering_preserved.sql`

**Source freshness:** `warn_after: 1 hour`, `error_after: 6 hours`.

**Schema drift:** assert every distinct `schema_version` is in a known-versions list.

---

## Portability note

Standard SQL throughout. DuckDB-specific constructs confined to `transform/macros/`:
`parse_json_safe()`, `entropy()`, and the source location macro. State the migration path in the
README rather than performing it.
