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
1.0 = faithful pacing, smaller = idle gaps compressed for speed; see docs/05 "Timing model").
Published as `event_kind = 'attack_run'`, keyed by `run_id`, which the run's request and attempt
events also carry so the observed session joins to its ground truth.

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
| `chat_turns_completed`, `probe_engagement_ratio`, `intent_flags_triggered` | bot chat |
| `late_arrival_count` | pathology |

The four evidence features exist so technique reconstruction has something to reason over. They are
derived purely from request patterns, never from the attempt log.

#### Feature-definition ambiguity pass (Phase 3)

Three feature definitions already drifted from their prose during Phases 2–3 (`wasted_request_ratio`
twice, the `pivot_ratio`/`retry_ratio` claims). A pass over the rest flagged the features whose prose
implies a property the computation cannot express alone. **Phase 5 must lift the resolved definition,
not the prose — an ambiguous feature becomes an ambiguous dbt model.** For each: `[def]` a definition
that must be pinned, `[doc]` a wording fix, `[pair]` only meaningful alongside another feature.

- **`wasted_request_ratio`** `[pair]` — resolved: fraction of requests not increasing
  `tier_reached_so_far`, counted only up to first reaching the session's peak tier. Measures
  efficiency *to its own peak*, so it only tells "efficient vs flailing" **paired with
  `max_path_tier`** (how high). Ablation: load-bearing for the Riddler pairs; keep, weight lightly.
- **`exact_duplicate_path_pairs`** `[def]` — the name says "pairs" but the working definition is the
  count of *paths that appear more than once* in the session (paths with count > 1), not the number
  of duplicate pairs (`n choose 2`). Phase 5 must implement the paths-with-duplicates count and the
  name should be read that way, or renamed.
- **`body_bytes_trend`** `[def]` — "trend" is computed as the Pearson correlation of `body_bytes`
  with request order (guarded to 0 for constant bodies), not a slope or last-minus-first. State it;
  the three would rank villains differently.
- **`time_to_tier3_s`** `[def]` — undefined for a session that never reaches tier 3 (most stallers).
  Needs an explicit convention (null, and excluded from aggregates — not 0, which reads as "instant").
- **`repeated_auth_failure_runs`, `path_enumeration_runs`** `[def]` — "runs" implies streak counting
  (consecutive events), but the run boundary is unspecified: how many consecutive 401s / sequential
  paths constitute one run, and is the feature the count of runs or their total length. Phase 5 must
  fix the streak definition before these evidence features mean anything for brute-force / scanning.
- **`traversal_pattern_count`, `injection_pattern_count`** `[def]` — "pattern" needs a concrete
  matcher (which substrings/regexes over path, query, and body count). The simulator emits specific
  payloads (`services/simulator/traffic.py`); the detector must match those, and the two must be
  defined together so a technique that leaves evidence is actually detected
  (`assert_high_observability_techniques_leave_evidence`).
- **`requests_per_min`** `[def]` — explodes toward infinity as `duration_s` → 0 (a 3-request burst
  spanning milliseconds). Needs a duration floor or a rate cap (the harness caps at 600/min); state
  which, so the dbt model and the harness agree.

**`int_session_features_truth`** `[ground_truth]` — evaluation only.

`max_stage_reached`, `stages_cleared`, `total_attempts`, `total_successes`, `overall_success_rate`,
`distinct_techniques_used`, `technique_id_set`, `attack_id_set`, `tactic_set`, `retry_ratio`,
`pivot_ratio`, `mean_technique_min_intelligence`, `total_noise_generated`, `time_to_max_stage_s`,
`abandoned_after_failures`.

**`int_stage_progression`** `[ground_truth]` — one row per session-stage: `attempts_in_stage`,
`successes_in_stage`, `first_attempt_at`, `stage_cleared_at`, `dwell_time_s`, `pivots_in_stage`,
`retries_in_stage`, `stage_cleared`.

### marts

Dimensions: `dim_villains`, `dim_techniques`, `dim_stages`.

Facts: `fct_attack_events`, `fct_attack_attempts`, `fct_stage_progression`, `fct_botchat_turns`,
`fct_sessions`, `fct_attack_runs`, `fct_quarantined_events`, `fct_intervention_orders`.

Analytic marts:

- **`mart_threat_scores`** `[triage_input]` — composite score from observed features only
- **`mart_killchain_funnel`** — sessions entering and clearing each stage, conversion by villain and
  archetype
- **`mart_technique_efficacy`** — per technique per villain: attempts, successes, observed rate,
  mean computed probability, delta
- **`mart_detection_correlation`** — joins attempts to the requests they generated via `attempt_id`,
  reporting evidence volume per technique. This is where `observability` tiers get validated against
  actual log output, and it mirrors real detection engineering.
- **`mart_reconciliation`** — the counts table from `docs/03-attack-simulation.md`

Evaluation marts:

- **`fct_triage_evaluations`** — villain identification, three levels
- **`fct_technique_evaluations`** — per session per technique: predicted, actual, true/false
  positive/negative, joined to `observability`
- **`mart_detection_coverage`** — technique recall grouped by observability tier. The headline
  result of the whole project.

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
  `event_id`s and must survive deduplication, or the villain disappears
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
