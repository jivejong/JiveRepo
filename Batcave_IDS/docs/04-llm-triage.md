# 04 — LLM triage and evaluation

The LLM is a downstream consumer of the analytics layer, not the centerpiece. The pipeline is the
project; the triage is what the pipeline enables.

What makes it worth including is that it is **measured against ground truth the model never sees**,
on two tasks:

1. **Attribution** — which villain was this, from behavior alone
2. **Technique reconstruction** — which ATT&CK techniques were used, from HTTP evidence alone

The second is the more serious one. It is what detection engineering actually measures: given only
what the sensor logged, can an analyst rebuild what the attacker did?

---

## Trigger

The Dagster asset `intervention_orders` selects sessions from `mart_threat_scores` where
`threat_score >= 60` and `session_id` is not already in `fct_intervention_orders`. One call per
session, capped per materialization (default 10) via a Dagster config field.

---

## Input contract — observed only

Assembled **exclusively** from `int_session_features_observed` and raw request and chat data. See
"The observed / truth boundary" in `docs/02-data-model.md`.

**Included:**
- Full observed feature vector, including the signature features (`riddle_param_count`,
  `exact_duplicate_path_pairs`, `distinct_source_ips`, `body_bytes_trend`, `wasted_request_ratio`)
  and the evidence features (`traversal_pattern_count`, `injection_pattern_count`,
  `repeated_auth_failure_runs`, `path_enumeration_runs`)
- Threat score with component breakdown
- Path sequence in order, up to 40 requests (first 20, last 20 if longer)
- Distinct user agents, status code distribution
- Up to 5 request bodies, truncated to 200 characters
- Source IP, first three octets only
- Bot chat turn metadata and intent flags

**Feature weighting is not conditioned on the Phase 3 ablation result (Phase 6 correction, see
docs/02's `wasted_request_ratio` entry).** That result — `error_ratio` load-bearing for 11 of 66
villain pairs, `wasted_request_ratio` for 2 of 66 (Riddler-only) — came from a corpus with
deterministic technique selection at `time_scale=0.02`. Re-run on the Phase 6 corpus (randomized
selection, `time_scale=0.2`), both show **0 of 66 pairs load-bearing**. **Ablation is corpus-
dependent, and this specific result is confounded**: selection randomization and the clock change
landed in the same re-run, so the shift can't be attributed to one cause without a third corpus.
Neither prior nor current result singles a feature out cleanly enough to justify downweighting it in
the prompt or the baseline classifier — both features are weighted at face value, per docs/02.

**Excluded — enforced by lineage test:**
- Anything from `attack_attempts`, `attack_runs`, `int_stage_progression`, or
  `int_session_features_truth`
- `attempt_id`, technique IDs, outcomes, rolls, stage progression, retry and pivot counts
- `villain_slug`, `name`, `archetype`, `run_id`

`assert_no_ground_truth_leakage` walks the dbt manifest and fails if any model tagged `triage_input`
has a `ground_truth` ancestor. A column-name check is defeated by renaming; a lineage check is not.
Run it as a **named CI step**. If it ever fails, every historical number in the repository becomes
meaningless.

---

## Prompt design

Versioned files in `services/triage/prompts/`. Record `prompt_version` on every evaluation row.

The system prompt supplies the briefing a real analyst would have:

1. Analyst role and Batcave framing
2. The roster of twelve villains with all six powerstats
3. What each powerstat implies about traffic shape (Layer 1, `docs/03-attack-simulation.md`)
4. The twelve signature profiles, written as threat-intelligence notes
5. The five archetypes and their members
6. **The technique catalog** — `attack_id`, name, stage, and `detection_signature` for every
   technique, so the model knows what evidence each would leave
7. Strict JSON, no prose, no fences

Giving the model the catalog and signatures is what keeps both tasks fair. Without them, Riddler and
Two-Face are indistinguishable and technique reconstruction is guesswork. **Do not give it the
`observability` column** — inferring which techniques are detectable is part of the task.

**Write the signature notes to match what the data actually contains, not the lore (Phase 3
finding).** Two signatures separate their villains through a *different* mechanism than their prose
describes, and the prompt must describe the observable, not the story:

- **Harley Quinn** — the lore is "erratic bursts separated by long pauses," but in the data she
  separates from Joker on *pace variance*, not a distinct bimodal burst shape (her burstiness is
  capped at the same total timing variance as Joker's high-intelligence jitter; docs/03). The prompt
  should describe elevated, irregular request spacing and her high volume, not tell the model to look
  for a burst/pause structure that isn't distinctly there. If Phase 6 shows the model confusing
  Joker and Harley specifically, the first fix is the truer bimodal burst shape docs/03 flags as a
  refinement — then update this note.
- **Killer Croc** — his distinguishing trace is a very high `attempts_per_stage_reached` (he
  concentrates a large volume into two stages), not the "highest raw request count" the lore
  emphasizes (Mister Freeze can rival him on raw count). Describe the concentration.

The general rule: the signature notes are threat intelligence *about the observed telemetry*, so
they must track the harness's measured discriminators (`make separability`) as those get tuned,
rather than restating docs/03's character descriptions where the two have diverged.

---

## Output contract

```json
{
  "threat_level": "low | moderate | high | critical",
  "suspected_villain": "<slug or 'unknown'>",
  "alternate_suspects": ["<slug>", "<slug>"],
  "suspected_archetype": "cerebral | brute | chaotic | stealth | methodical",
  "confidence": 0.0,
  "identified_techniques": [
    {
      "attack_id": "T1110",
      "confidence": 0.0,
      "evidence": "<what in the log supports this>"
    }
  ],
  "identified_tactics": ["TA0007", "TA0001"],
  "reconstructed_stage_reached": 3,
  "reasoning": "<2-3 sentences citing specific observed behavior>",
  "in_person_intervention_required": true,
  "recommended_countermeasures": ["<string>", "..."],
  "attack_pattern_summary": "<one line>"
}
```

**The `evidence` field per technique is the important one.** It forces the model to point at
something in the log rather than pattern-matching the villain to a plausible technique list, and it
lets you read whether a correct answer came from inference or luck. Spot-check a sample of these by
hand during Phase 6 and note what you find.

Parse defensively: strip fences, retry once with a repair instruction, then write `parse_failed = true`
rather than raising. Validate every slug against `dim_villains` and every `attack_id` against
`dim_techniques`; unrecognized values get flagged rather than stored as truth.

---

## Intervention order

| Field | Source |
|---|---|
| `order_id`, `session_id`, `issued_at` | generated / input |
| `threat_level`, `suspected_villain`, `alternate_suspects`, `suspected_archetype`, `confidence` | LLM |
| `identified_techniques` (JSON with evidence), `identified_tactics`, `reconstructed_stage_reached` | LLM |
| `reasoning`, `countermeasures`, `in_person_intervention_required` | LLM |
| `threat_score` | analytics layer, not the LLM |
| `prompt_version`, `model_name`, `latency_ms`, `input_tokens`, `output_tokens` | runtime |
| `parse_failed`, `hallucinated_villain`, `hallucinated_technique_count` | validation |

---

## Evaluation

### Task 1 — Attribution

`fct_triage_evaluations` joins orders to `fct_attack_runs` via `session_id → run_id`.

| Level | Definition | Random baseline |
|---|---|---|
| Exact | `suspected_villain = villain_slug` | 8.3% |
| Top-3 | truth in `suspected_villain` + `alternate_suspects` | 25.0% |
| Archetype | `suspected_archetype` matches the true archetype | ~20% |

Plus per-villain accuracy, a 12 × 12 confusion matrix, and calibration (mean confidence on correct
versus incorrect — if equal, confidence is uninformative, and say so).

The four closest stat pairs are all within-archetype, so expect exact accuracy well below archetype
accuracy. **That gap is the finding**, and it is supported by numbers you can show.

### Task 2 — Technique reconstruction

`fct_technique_evaluations`, one row per session per technique in the catalog: predicted yes/no,
actual yes/no, outcome class, joined to `observability`.

| Metric | Definition |
|---|---|
| Exact technique | Precision, recall, F1 over the `attack_id` set per session |
| Tactic-level recall | Right kind of activity, wrong specific technique — a partial credit that matters |
| **Observability-adjusted recall** | Recall restricted to `high` and `partial` tiers. The fair ceiling. |
| Stage reconstruction | `reconstructed_stage_reached` vs actual, exact and within-one |
| Hallucination rate | Techniques named that were never attempted |
| Evidence quality | Manual spot-check: does the cited evidence actually appear in the session? |

### `mart_detection_coverage` — the headline result

Technique recall grouped by observability tier. `low` is split further — see below —
because it is not one detection posture.

**Resolved in Phase 6 Part A**: deterministic technique selection (`services/simulator/session.py`
picking `candidates[0]` / `untried[0]` in strict `techniques.csv` row order) left 4 of 23 techniques
unreachable, understating the denominator. Fixed by shuffling candidates per stage entry with the
session's own RNG before selection (deterministic per seed, uniform over many). Confirmed on the
re-run corpus: **all 23 catalog techniques are now reachable** — see `mart_detection_coverage`'s
real output below, where `reachable` equals `techniques` in every tier.

**Recall is still measured over the `reachable` column, not `techniques`, as a matter of principle**
even though the two now coincide on this corpus — a technique with zero real attempts still can't be
scored, and a future corpus (fewer runs, a different seed) could reopen the gap.

The expected shape: strong on `high`, mixed on `partial`, near-zero on `low`. Write that up as a
**detection coverage gap analysis**, because that is what it is. The conclusion a security team
would draw is that an HTTP sensor alone cannot see a third of the technique catalog, and closing
that gap requires endpoint or identity telemetry the honeypot does not have.

That is a substantive result. It is also a much better README section than a single accuracy number,
and it is the kind of thing that reads as domain understanding rather than a demo.

**`low` is two different detection postures, not one** (docs/07, Phase 2): eight techniques where
the honeypot never sees an HTTP request at all (`net_info_gather`, `identity_gather`,
`open_source_search`, `phishing`, `screen_capture`, `audio_capture`, `video_capture`,
`input_capture`) — recall there is a hard ceiling, not a model failure, since no evidence exists to
recover from. One (`valid_accounts`) is on-platform but camouflaged — a real login request lands in
the logs, it just looks like legitimate use — so recall is theoretically possible from session-level
context even though it's impossible from the event alone. Reporting these as one bucket would hide
that distinction; reporting them separately is what makes the eventual real number legible rather
than mysterious.

Note the design consequence worth calling out: Killer Croc's gating leaves him `port_sweep`
(`partial`) and `brute_force` (`high`) — no low-observability options at all — so he is loud and
easy to reconstruct, and he stalls there (docs/07: no stage-3 technique's `min_intelligence` is low
enough for him). Ra's al Ghul clears every intelligence gate and reaches nearly the full catalog,
including the quiet techniques — with one exception, `privesc_exploit`, which gates on power rather
than intelligence. **The most capable villain is the hardest to detect** — with one gap that's still
cheap to build — which falls out of the stat gating rather than being arranged.

---

## Baseline

The rule-based baseline must attempt **both** tasks:

- **Attribution:** nearest neighbour in normalized stat space between the observed session profile
  and each villain's expected profile, with the signature features as hard discriminators
- **Technique reconstruction:** keyword and threshold matching of `detection_signature` patterns
  against the evidence features

Run it on every session alongside the LLM. Report both side by side.

If the LLM does not beat a regex at technique reconstruction, say so in the README. That is a
genuinely interesting finding and a reviewer will respect it more than a claim that it works. It is
also the single most likely outcome for the `high` observability tier, where the signatures are by
definition pattern-matchable.

---

## Configuration

**Current: Google Gemini, `gemini-3.1-flash-lite`.** Pinned via the `TRIAGE_MODEL` env var (default
in `services/triage/llm.py`), `GEMINI_API_KEY` from environment, never committed. This is the fourth
model this project has run triage on, and stays on the same provider as the third — full chain: Llama
on Groq (removed from serving) → `openai/gpt-oss-20b` on Groq (reasoning-token budget blowout) →
`qwen/qwen3.8-27b` on Groq (worked, Phase 6's numbers) → `gemini-3.5-flash-lite` on Gemini (worked,
Track A's shipped numbers) → **`gemini-3.1-flash-lite` (current)**. Unlike every prior swap, this one
was not forced by an operational failure (no 404, no rate limit, no VPN incompatibility) — it was a
deliberate choice, re-verified the same way every forced swap was rather than assumed safe because
nothing broke.

**Re-verifying `thinking_config` found a real behavioral difference between the two Gemini versions.**
The Groq→Gemini swap discovered that `gemini-3.5-flash-lite` **rejects**
`thinking_config=ThinkingConfig(thinking_budget=0)` with an opaque `400 INVALID_ARGUMENT` (isolated by
testing each config parameter individually against the live API). Re-running that same isolated test
against `gemini-3.1-flash-lite` found the opposite: `thinking_budget=0` is **accepted** and produces
zero thinking tokens — the same zero-thinking-tokens result omitting the field entirely already gives
on this model, so the two are equivalent and the field still isn't set, for the same reason as before
(nothing to gain from setting it). `thinking_budget=-1` (AUTOMATIC) and `thinking_level="low"` both
still induce real thinking tokens on a trivial prompt (306 and 120, versus 3.5's 132 and 67) — the
one thing that changed is that the *rejection* of an explicit zero didn't carry over. Full account,
including the raw API responses: docs/09.

The original Groq→Gemini swap required two real-corpus corrections, preserved here unchanged since
they still describe how `gemini-3.5-flash-lite` was chosen in the first place:
- `gemini-2.5-flash-lite` — the more mature, longer-available tier, the reasonable first choice —
  returned a live `404`: *"This model models/gemini-2.5-flash-lite is no longer available to new
  users."* Google's own error recommended `gemini-3.5-flash-lite`, which is what was pinned then.
- `thinking_config=ThinkingConfig(thinking_budget=0)`, added to explicitly disable reasoning tokens
  (this project already lost `gpt-oss-20b` to exactly that failure mode), turned out to be **rejected**
  by `gemini-3.5-flash-lite` with an opaque `400 INVALID_ARGUMENT` — isolated by testing each config
  parameter individually against the live API, since the error carried no field-level detail.
  Omitting `thinking_config` entirely produces zero thinking tokens by default on this model, which is
  the behavior that was wanted, so the field just isn't set.

**Schema-constrained structured output** (`response_schema=TriageOutput`, a Pydantic model mirroring
the Output contract above field-for-field) replaces Groq/qwen's plain `response_format: json_object`.
`suspected_villain`, `alternate_suspects`, and `identified_techniques[].attack_id` stay unconstrained
strings in the schema deliberately — constraining them to known values would make hallucination
structurally impossible and defeat `validate()`'s whole purpose, which is to measure hallucination,
not prevent it. `threat_level` and `suspected_archetype` are constrained to their fixed small enums,
where there's no measurement value in leaving them open. The defensive parse + repair retry stay in
place regardless of the schema guarantee — see Results below for why that caution paid off.

Also unchanged from the Groq era: `temperature: 0`, 30s timeout with one retry and backoff, and
**without `GEMINI_API_KEY`, triage runs the baseline instead** — the zero-credential path is
provider-agnostic by construction and was re-verified after the swap.

---

## Results

36 sessions, 3 per villain across all twelve, stratified rather than threshold-selected (the
threshold alone only ever reaches 7 of 12 villains — docs/02's Catwoman calibration finding). All
four LLM chapters below were scored against the **same 36 sessions** through the same evaluation
marts, so the numbers are directly comparable across models, not just against the baseline.

**Read the qwen numbers below against the constraint that produced them: that chapter ran on Groq's
free tier against a substituted model, under real rate limits.** Treat that chapter's numbers as a
lower bound on what a better-resourced run of *that model* would show, not as its ceiling. The Gemini
chapters that follow are not similarly rate-limited — a genuinely different measurement, not a rerun
under the same constraint.

**The `gemini-3.1-flash-lite` chapter carries one disclosed confound the first three don't: the
reference corpus grew between measurements.** `classify_villain`'s nearest-centroid baseline computes
its per-villain centroids fresh, over every labeled session currently in the warehouse
(`build_reference_profiles`, `services/triage/baseline.py`) — by design, not a bug. Track B's Phase 8
verification work landed 3 new labeled sessions (2 console, 1 headless) between the `gemini-3.5-
flash-lite` measurement and this one, which shifts those centroids slightly even though the same
exact 36 session_ids were re-scored. The evidence this is corpus drift, not a scoring change:
`classify_techniques` (the technique-reconstruction baseline) takes only a session's own features, no
corpus-wide reference, and its numbers below are **byte-identical** to the `gemini-3.5-flash-lite`
chapter's baseline row; `classify_villain` is the one that moved (30.6% → 36.1% exact, same 36
sessions). Full account: docs/09.

### Attribution

| source | n | exact | top_3 | archetype | parse_fail |
|---|---|---|---|---|---|
| baseline | 36 | 30.6% | 77.8% | 41.7% | 0.0% |
| qwen (Groq) | 36 | 16.7% | 36.1% | 36.1% | 27.8% |
| gemini-3.5-flash-lite | 36 | 13.9% | 41.7% | 30.6% | **0.0%** |
| baseline (re-scored, grown corpus — see confound note above) | 36 | 36.1% | 77.8% | 44.4% | 0.0% |
| gemini-3.1-flash-lite | 36 | 27.8% | 50.0% | 44.4% | **0.0%** |
| random | — | 8.3% | 25.0% | ~20% | — |

**The archetype column is not apples-to-apples with baseline, for either LLM chapter, and reading it
as one is the wrong conclusion.** Baseline's `suspected_archetype` is a mechanical lookup on its own
villain guess (`villain_archetype_map()`, `services/triage/__main__.py:152`) — it is not an
independent prediction, it cannot be, by construction. Of baseline's 15 archetype matches, **11 are
tautological** (the same 11 sessions where it also got the exact villain right) and only **4 are
genuine** wrong-villain-but-right-family hits — baseline's real, non-tautological archetype signal is
closer to 11.1% (4/36), not a standalone 41.7%. Both LLMs' `suspected_archetype` is architecturally
independent — its own field in the output contract, asked for separately from `suspected_villain` —
and both show the same shape of genuine family-level recognition: qwen's 36.1% against its own 16.7%
exact rate, Gemini's 30.6% against its own 13.9% exact rate. Both named the wrong villain but the
right archetype on roughly a fifth of all 36 sessions. That's a real capability the baseline has no
mechanism to produce at all, tautological or otherwise, and it holds under both models.

### Technique reconstruction

| source | precision | recall | f1 | hallucination_rate |
|---|---|---|---|---|
| baseline | 65.4% | 39.7% | 0.49 | 0.00 |
| qwen (Groq) | 57.0% | 20.5% | 0.30 | 0.00 |
| gemini-3.5-flash-lite | 71.4% | 29.7% | 0.42 | 0.00 |
| baseline (re-scored, grown corpus) | 65.4% | 39.7% | 0.49 | 0.00 |
| gemini-3.1-flash-lite | 60.9% | 25.6% | 0.36 | 0.00 |

Gemini 3.5 improved on qwen across all three technique-reconstruction metrics, and edged out baseline
on precision specifically (71.4% vs 65.4%) while still trailing on recall. Zero hallucinated technique
IDs under either model, both runs.

**Gemini 3.1 does not repeat that result — it trails 3.5 on every technique-reconstruction metric**
(precision 60.9% vs 71.4%, recall 25.6% vs 29.7%, f1 0.36 vs 0.42), and now trails baseline on
precision too, not just recall. Read together with the Attribution table above, this is the headline
finding of the swap: **`gemini-3.1-flash-lite` is a mixed result against `gemini-3.5-flash-lite`, not
a strict upgrade or downgrade** — measurably better at *who did this* (exact villain attribution
nearly doubles, 13.9% → 27.8%; top_3 rises 41.7% → 50.0%; archetype rises 30.6% → 44.4%) and
measurably worse at *what did they do* (every technique-reconstruction metric down, and detection
coverage on the high-observability tier down from 66.0% to 55.7% — see Coverage by tier below). Zero
hallucinated technique IDs holds for both Gemini versions.

**Villain-slug hallucination — a recurring property of the validation layer, not any one model, and
it got more frequent, not less, on the newer model.** All three LLM chapters produced a
malformed-but-substantively-correct villain slug: qwen returned `killer-croc` (missing the `386-`
prefix) on a real Killer Croc session; `gemini-3.5-flash-lite` returned `scarecrow` (missing the
`576-` prefix, twice) on real Scarecrow sessions; **`gemini-3.1-flash-lite` returned an unprefixed
slug 6 times in 36** (`bane` ×3, `scarecrow` ×2, `ras-al-ghul` ×1) — three times 3.5's rate. Same
failure shape, same root cause (a plain string field with no format constraint, deliberately — see
Configuration above), across three unrelated models, with a clear escalation on the model this project
now defaults to. Worth treating as a property of the schema, not a model-specific quirk, if a future
pass wants to close it — a regex/enum check on the slug format at the validation layer would catch
all three without touching any model. Given the rate increase, this is worth prioritizing sooner
rather than "if a future pass wants to."

### Coverage by tier

| tier | techniques | reachable | attempts | baseline recall | qwen recall | gemini-3.5 recall | gemini-3.1 recall |
|---|---|---|---|---|---|---|---|
| high | 7 | 7 | 2,690 | 89.7% | 44.3% | 66.0% | **55.7%** |
| partial | 7 | 7 | 1,108 | 0.0% | 0.0% | 0.0% | 3.5% |
| low_camouflaged | 1 | 1 | 241 | 0.0% | 11.1% | 5.6% | 0.0% |
| low_no_evidence | 8 | 8 | 1,761 | 0.0% | 0.0% | 0.0% | 0.0% |

**Baseline still wins high-tier decisively** — pattern matching against signatures that are
pattern-matchable by definition is a hard bar, and the baseline clears it regardless of which LLM it's
compared against. Gemini 3.5 closed real ground on high-tier versus qwen (66.0% vs 44.3%), consistent
with schema-constrained output producing more usable technique lists; **Gemini 3.1 gives some of that
ground back** (55.7%), consistent with the technique-reconstruction table above — the attribution
gain and the technique-coverage loss are the same trade showing up in two different marts, not two
unrelated findings. **Partial tier: three of four measurements show zero movement**, with gemini-3.1
the first source of any four to register a non-zero partial-tier recall at all (3.5%, one session) —
too small a sample (1 of ~1,108 attempts) to call a trend reversed, but worth flagging as the first
crack in what had been a clean negative result across two full model generations.

### Killer Croc and Ra's al Ghul: qwen's strongest result did not replicate under either Gemini version

Both villains were flagged in earlier phases as structurally hard, for different reasons — Killer
Croc because gating gives him only loud, high-observability techniques and his real discriminator
(`attempts_per_stage_reached`) is ground-truth-only, unobservable to either evaluator; Ra's al Ghul
because his discriminator is `wasted_request_ratio` + `max_path_tier`, which the Phase 3 ablation
showed load-bearing for only 2 of 66 villain pairs (both Riddler) and not load-bearing for him at all.
The baseline scores **0/3 on both, under every chapter** — every real Killer Croc session gets called
Poison Ivy (qwen chapter) or Bane (Gemini chapter), every real Ra's al Ghul session gets called Joker
or Harley Quinn/Bane. That was the testable prediction going in: if an LLM also fails both, the
information isn't in the observed data at all; if it gets either, that's a concrete argument for the
LLM.

**Under qwen, this was the project's strongest LLM result**, preserved here as measured: of Killer
Croc's 3 true sessions, the 2 that got a real response (1 hit a rate-limit failure) both correctly
identified him — *"The session exhibits a high volume of requests (149 rpm) concentrated almost
exclusively on a single endpoint (/login) with a 93% error rate, matching Killer Croc's signature of
high-volume, low-precision brute force..."* (0.85 confidence, plus a malformed-slug near-miss with
near-identical reasoning). Two out of two genuine attempts got the right villain, against a baseline
that got zero out of three.

**Under Gemini, it does not replicate.** All 3 real Killer Croc sessions were called Bane — a
different wrong answer than qwen/baseline's Poison Ivy, but still wrong, all three times, all with
real generations (no rate-limit exclusions this chapter). One example: *"The session consists of a
heavy sequence of login endpoint attempts generating numerous 401 errors with large payloads and
persistence despite errors, matching the signature of a brute-force attack"* (0.90 confidence) — a
real, grounded description of Killer Croc's actual behavior, misattributed to the wrong brute-archetype
villain. Ra's al Ghul: also 0/3 under Gemini 3.5 (2 called Harley Quinn, 1 called Bane), none as
`"unknown"`.

**Killer Croc: 0/3 again under Gemini 3.1** — all three still called Bane, the identical wrong answer
3.5 gave, with similarly grounded brute-force reasoning (quoted above in the Coverage section's
lead-in data). Three models, three chapters, one consistent wrong answer for every real Killer Croc
session. **Ra's al Ghul: 1/3 under Gemini 3.1** — the first genuine hit on this villain across all
three LLM chapters (*"high-speed, methodical traversal of sensitive endpoints with significant
user-agent rotation, characteristic of Ra's Al Ghul's efficient, goal-oriented reconnaissance"*, 0.85
confidence), against 2 misses (both called Harley Quinn). One hit in nine attempts across three model
generations is not evidence the signal is reliably recoverable, but it means "genuinely unrecoverable"
should soften to "very hard to recover" — the honest update given a third data point that doesn't
match the first two.

**The overall conclusion holds regardless: the information isn't reliably recoverable from the
observed features for either villain, especially Killer Croc**, and that conclusion gets *stronger*,
not weaker, each time a differently-behaved model mostly fails to find it too, even though the specific
"LLM beats baseline here" finding from the qwen chapter does not stand as a general claim about LLM
triage. It was true of one model, not of "the LLM" as a category, and reporting that qualification is
the point of running more than one model at all.

**The calibrated-uncertainty claim from the qwen chapter does not hold for either Gemini version,
stated plainly rather than left standing.** qwen answered `"unknown"` on 8 of 36 sessions, including
2 of Ra's al Ghul's 3, with confidence dropping to 0.45 on those — a real, measured instance of a model
recognizing absent signal and saying so rather than guessing. **Neither Gemini version ever answers
`"unknown"`**: 3.5 was 0 of 36 at a flat 0.84 mean confidence *including on wrong answers* (0.90
confidence on the Killer Croc misattribution quoted above); 3.1 is also 0 of 36, mean confidence 0.82,
range a narrow 0.75–0.85 (versus 3.5's flatter-still 0.84 point estimate) — including 0.85 confidence
on all three of the identical wrong Killer Croc answers. This looks like a stable behavioral property
of the Gemini family at `temperature=0` with this prompt, not a one-model quirk or sampling noise:
qwen showed calibrated uncertainty as a real capability across two model generations of Gemini now,
neither exhibits it at all in this sample. A reader should take "the model can express calibrated
uncertainty" as a qwen-era finding, not a property of
LLM triage in general, and not a property of the pipeline's current default.

### Two-Face / Killer Croc: a related but distinct third data point, not a third confirmation

docs/02 and docs/03 document two prior instances of Two-Face and Killer Croc being confused on
`exact_duplicate_path_pairs` — the dbt test's scoping, and the baseline classifier's dropped hard
discriminator, both traced to the same root cause (the feature can't distinguish "duplicated on
purpose" from "duplicated by grinding"). Gemini's confusion matrix shows one of Two-Face's 3 true
sessions misattributed to Killer Croc. **Read this as a related but mechanistically distinct data
point, not a third confirmation of the same pattern.** The reasoning for that misattribution cites
auth-failure counts and payload characteristics — *"brute-force characteristics with multiple auth
failures against /login followed by repeated requests to /admin... point strongly toward a brute
archetype such as Killer Croc or Bane"* — never mentions `exact_duplicate_path_pairs` or duplicate
paths at all. The two prior instances were specifically about that one feature's values overlapping;
this one is the model conflating Two-Face's behavior with a brute-force pattern via a different signal
path entirely. Worth recording as a fourth data point that these two villains are confusable in
general, not as the same bug showing up a third time.

### Parse failures: 27.8% → 0.0%, and what it cost

**qwen (Groq), unedited:** 27.8% (10/36) raw parse-failure rate. Of those 10, 4 were pure rate-limit
infrastructure failures (Groq's free-tier output-tokens-per-minute cap, zero real generation attempted)
and 6 were genuine failures — the model responded and still produced unparseable JSON even after the
repair retry. Roughly a third of that chapter's failures were attributable to the free-tier environment
rather than the model itself.

**Gemini, unedited: 0.0% (0/36).** Every one of 36 real API calls returned schema-conformant JSON on
the first attempt — the repair-retry path was never exercised this chapter. This is the mechanism-
explained reliability improvement the schema-constrained `response_schema` was expected to produce:
Gemini enforces the output shape server-side rather than only being asked nicely for `json_object`
formatting, and on this sample it worked completely.

**This is a trade, not a clean win, and both halves are real.** The reliability gain is unambiguous
and mechanism-explained: schema-constrained generation eliminated a failure mode that cost the prior
chapter more than a quarter of its sessions. The cost is the calibrated-uncertainty finding directly
above — the model that never fails to produce valid JSON is also the model that never says "I don't
know," including when it should. Whether that trade is worth it depends entirely on what the
downstream use case weighs more: a triage pipeline that always returns a structured, actionable
answer, or one that sometimes correctly declines to answer at all. This project doesn't take a
position on which is better — it measures both and reports the trade plainly.

### Evidence spot-check

**qwen:** every reasoning and evidence field checked by hand cited specific, verifiable numbers from
the actual session — request rates, error ratios, exact `riddle=` query-parameter counts, specific
status-code sequences — not generic template language.

**Gemini, same check, same verdict:** hand-checked reasoning across correct and incorrect answers
alike cites real, specific session details rather than generic filler. A correct Penguin
identification: *"The session exhibits 8 distinct source IPs across a single session, a signature
characteristic of Penguin's multi-machine coordinated proxying, alongside high request rates and tier
4 endpoint access"* — checkable and accurate. The wrong Killer-Croc-as-Bane answer quoted above is
equally well-grounded in real features; it's wrong about the villain, not fabricated about the
evidence. Verdict, both chapters: real inference grounded in the session's own evidence, whether the
final answer is right or wrong.

### Overall framing

**Baseline still wins on pattern-matchable evidence, under both LLM chapters. Neither LLM has beaten
it on the partial tier, across two models now.** Beyond that, the two chapters tell different stories
and neither should stand in for "what LLM triage does" in general:

- **qwen** showed real value specifically on the cases pattern-matching structurally cannot reach —
  Killer Croc, and a calibrated non-answer on Ra's al Ghul — at the cost of a 27.8% parse-failure rate,
  partly environmental.
- **Gemini** is dramatically more reliable (0.0% parse failures, a real mechanism-explained
  improvement) and improves technique reconstruction and high-tier coverage over qwen, but did not
  reproduce the Killer Croc / Ra's al Ghul finding and shows no calibrated-uncertainty behavior at all
  in this sample.

A reader who wants one number will miss the actual result either way. The honest summary is a trade,
not a ranking: more reliable structured output, less legible uncertainty. Which one a given deployment
should want depends on what happens downstream of a wrong-but-confident answer versus a right-but-
occasionally-silent one — a question this project surfaces rather than answers, since it's a real
operational tradeoff, not a bug in either model.

### Why `gemini-3.1-flash-lite` is the default, stated explicitly

**`gemini-3.1-flash-lite` is the default model as of this phase, and the reason is cost — not
accuracy.** Say that plainly rather than leaving it for a reader to infer from two tables sitting side
by side: this project chose the model that performs worse on part of its own evaluation, on purpose,
because the two result tables above are not the whole decision. A deployment (or a portfolio project)
weighs price against measured accuracy, and here the cheaper model was judged worth its accuracy cost.

**The accepted cost, restated plainly rather than left implicit in the tables above:** switching from
`gemini-3.5-flash-lite` to `gemini-3.1-flash-lite` improves villain attribution (exact 13.9% → 27.8%,
archetype 30.6% → 44.4%) but regresses technique-reconstruction precision (71.4% → 60.9%), regresses
high-tier detection-coverage recall (66.0% → 55.7%), and increases the malformed-slug hallucination
rate (2 of 36 → 6 of 36). That is the full price of the swap, not a subset of it — attribution
improving does not offset the technique-reconstruction and coverage regressions; they are three
separate, independently real findings, and all three were known before the model shipped as the
default, not discovered afterward by a reader comparing columns.

This is a deliberate, disclosed choice, made with the trade already measured — not an oversight to be
found later. If a future phase's priorities shift toward technique-reconstruction accuracy over cost,
this section is where to start: the `gemini-3.5-flash-lite` numbers above are the ceiling this project
already knows is available at a higher price.
