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

- Model: Groq, `qwen/qwen3.8-27b`. Pinned via the `TRIAGE_MODEL` env var (default in
  `services/triage/llm.py`), not hardcoded elsewhere. Originally spec'd as Llama; by Phase 6,
  Groq had removed every plain Llama chat model from serving (confirmed via the account's live
  `/models` list - `llama-3.3-70b-versatile` 404s, and no `llama-*` chat model remains in the
  catalog at all).

  Two models were tried and rejected before this one, both against the real corpus, not in the
  abstract: `openai/gpt-oss-20b` (a reasoning model) returned `400 json_validate_failed` from
  Groq's own server-side JSON-mode validator on nearly every call - not a parsing bug on our
  side, Groq rejected the raw output before we ever saw it - and its reasoning-token overhead
  (~3,500-4,000 tokens/call) exhausted the account's entire 200,000-token/day budget partway
  through a single 36-session run. `qwen/qwen3.8-27b` is not a reasoning model, returns clean
  JSON under `response_format: json_object`, and a single verified test call correctly attributed
  a real Riddler session with grounded evidence (cited the actual `riddle=` query parameters and
  request sequence) at ~4,170 tokens. Documenting the full swap chain here rather than silently
  repointing the default.
- `GROQ_API_KEY` from environment, never committed
- `temperature: 0` for reproducibility
- Timeout 30s, one retry with backoff
- **Without `GROQ_API_KEY`, triage runs the baseline instead**, so the entire pipeline including
  both evaluations is demonstrable with zero credentials. This matters for the durability goal: a
  reader can clone and run everything with no accounts at all.

---

## Results (Phase 6 checkpoint, real run)

36 sessions, 3 per villain across all twelve, stratified rather than threshold-selected (the
threshold alone only ever reaches 7 of 12 villains — docs/02's Catwoman calibration finding). Both
sources scored against the same sessions through the same evaluation marts.

**Read this against the Configuration section above: the LLM half ran on Groq's free tier against
`qwen/qwen3.8-27b`, a substitute for the originally spec'd Llama model, which no longer exists on
Groq at all.** That bounds what this comparison establishes — it is not a clean test of "the best
available LLM against a regex," it's a test of one free-tier-accessible model against one, under
real rate limits. Treat the LLM numbers as a lower bound on what a better-resourced run would show,
not as the ceiling.

### Attribution

| source | n | exact | top_3 | archetype | parse_fail |
|---|---|---|---|---|---|
| baseline | 36 | 30.6% | 77.8% | 41.7% | 0.0% |
| llm | 36 | 16.7% | 36.1% | 36.1% | 27.8% |
| random | — | 8.3% | 25.0% | ~20% | — |

**The archetype column is not apples-to-apples, and reading it as one is the wrong conclusion.**
Baseline's `suspected_archetype` is a mechanical lookup on its own villain guess
(`villain_archetype_map()`, `services/triage/__main__.py:152`) — it is not an independent
prediction, it cannot be, by construction. Of baseline's 15 archetype matches, **11 are tautological**
(the same 11 sessions where it also got the exact villain right — archetype correctness follows for
free) and only **4 are genuine** wrong-villain-but-right-family hits. So baseline's real,
non-tautological archetype signal is closer to 11.1% (4/36) riding on top of its 30.6% exact rate,
not a standalone 41.7%. The LLM's `suspected_archetype` is architecturally independent — its own
field in the output contract, asked for separately from `suspected_villain` — and its 36.1% against
its own 16.7% exact rate is genuine family-level recognition: on roughly a fifth of all 36 sessions,
it named the wrong villain but the right archetype. That's a real capability the baseline has no
mechanism to produce at all, tautological or otherwise.

### Technique reconstruction

| source | precision | recall | f1 | hallucination_rate |
|---|---|---|---|---|
| baseline | 65.4% | 39.7% | 0.49 | 0.00 |
| llm | 57.0% | 20.5% | 0.30 | 0.00 |

Zero hallucinated technique IDs from the LLM across all 36 sessions. One hallucinated villain slug
(below, in the Killer Croc detail) — the model named the right villain with a malformed slug, not a
fabricated one.

### Coverage by tier

| tier | techniques | reachable | attempts | baseline recall | llm recall |
|---|---|---|---|---|---|
| high | 7 | 7 | 2,690 | 89.7% | 44.3% |
| partial | 7 | 7 | 1,108 | 0.0% | 0.0% |
| low_camouflaged | 1 | 1 | 241 | 0.0% | 11.1% |
| low_no_evidence | 8 | 8 | 1,761 | 0.0% | 0.0% |

**Baseline wins high-tier decisively** — pattern matching against signatures that are
pattern-matchable by definition is a hard bar, and the baseline clears it. **Partial tier: zero
movement, 0% for both sources.** The hope that LLM inference would beat keyword matching on
ambiguous evidence did not hold on this corpus — reporting that as measured, not as a near-miss.

### Killer Croc and Ra's al Ghul: the strongest result in this project

Both were flagged in earlier phases as structurally hard, for different reasons — Killer Croc
because gating gives him only loud, high-observability techniques and his real discriminator
(`attempts_per_stage_reached`) is ground-truth-only, unobservable to either evaluator; Ra's al Ghul
because his discriminator is `wasted_request_ratio` + `max_path_tier`, which the Phase 3 ablation
showed load-bearing for only 2 of 66 villain pairs (both Riddler) and not load-bearing for him at
all. The baseline scores **0/3 on both** — every real Killer Croc session gets called Poison Ivy,
every real Ra's al Ghul session gets called Joker. That was the testable prediction going in: if the
LLM also fails both, the information isn't in the observed data at all, a stronger finding than a
baseline limitation; if it gets either, that's a concrete argument for the LLM.

**Killer Croc: the LLM found real signal where the baseline found none.** Of his 3 true sessions, 1
hit a rate-limit failure with zero real generation (excluded below — infrastructure, not a model
answer). Of the **2 sessions where the model actually responded, both correctly identified Killer
Croc**:

- Exact match (`386-killer-croc`, confidence 0.85): *"The session exhibits a high volume of requests
  (149 rpm) concentrated almost exclusively on a single endpoint (/login) with a 93% error rate,
  matching Killer Croc's signature of high-volume, low-precision brute force against a narrow slice
  of the kill chain. The lack of evasion, single user agent, and persistence through 41 requests
  after the first error align with the brute archetype's durability and lack of stealth."*
- Same villain, malformed slug (`killer-croc` — missing the `386-` prefix, so it fails exact-match
  scoring and is flagged `hallucinated_villain`; this is the run's one hallucination): near-identical
  reasoning, same 0.85 confidence, same correct villain. A slug-formatting miss, not a content miss.

Two out of two genuine attempts got the right villain, against a baseline that got zero out of three.
That's a concrete case for the LLM adding value exactly where the ablation said the baseline's tools
couldn't reach.

**Ra's al Ghul: the LLM correctly found no signal, and said so.** Of his 3 true sessions, 1 was a
genuine parse failure (49.5 seconds of real generation, still unparseable after the repair retry —
not a rate-limit artifact). The other 2 both came back `suspected_villain: "unknown"`, with confidence
dropping to **0.45** — well below the model's typical 0.85–0.92 elsewhere in this run. The reasoning
for one names exactly what's missing:

> *"The session exhibits high-speed, high-entropy path traversal with evasion via 5 distinct user
> agents and a single source IP, reaching tier-4 data endpoints quickly. The presence of exploit
> patterns and invalid bodies suggests a chaotic or automated attack rather than a methodical villain
> like Ra's Al Ghul, but the lack of specific signatures (riddles, IP rotation, burst patterns) makes
> attribution uncertain."*

This is not the same outcome as failing to guess. The model considered Ra's al Ghul by name, checked
for his specific signatures, found them absent, and dropped its confidence accordingly rather than
picking a villain anyway. That calibrated-uncertainty behavior is exactly what distinguishes
inference from confabulation, and the baseline has no equivalent of it — nearest-centroid always
returns a nearest neighbor, confident or not, because "I don't know" isn't in its output space. Tied
to the earlier prediction: Ra's al Ghul's discriminator was already shown non-load-bearing by the
ablation before this run. **The model finding no signal where we had already measured there was none
is the confirmation of that finding, not a new one** — two independent methods (a statistical
ablation over 66 pairs, and an LLM reasoning over individual sessions) landing on the same answer.

### Parse failures, unedited

27.8% (10/36) raw parse-failure rate for the LLM. Not recomputed, not adjusted — reported as
measured. Of those 10:

- **4 are pure rate-limit infrastructure failures** — Groq's free-tier output-tokens-per-minute cap
  (1,000 OTPM on this model) was hit repeatedly through the run; these 4 show the exact ~2,100ms
  signature of one transport retry then giving up, meaning zero real model generation was ever
  attempted for these sessions.
- **6 are genuine failures** — the model did respond, after waiting through real rate-limit backoff
  (19–52 seconds of actual generation time), and still produced unparseable JSON even after the one
  repair retry built into `services/triage/llm.py`.

The environment measurably contributed to the raw number. The headline 27.8% stands as measured —
this is not "27.8%, but really 16.7% if the infrastructure had cooperated." What can honestly be said
is narrower: roughly a third of the failures are attributable to the free-tier environment rather
than the model, which bounds how much weight the raw parse-failure rate alone should carry when
judging the model itself.

### Evidence spot-check

Every reasoning and evidence field checked by hand cites specific, verifiable numbers from the actual
session — request rates, error ratios, exact `riddle=` query-parameter counts, specific status-code
sequences — not generic template language. The Riddler example above (806b3735) cites "20 'riddle='
query parameters... short duration and low volume fit his low Durability... 5 injection patterns."
Every one of those numbers is checkable against the session's own features and matches. Verdict: real
inference grounded in the session's own evidence, not plausible-sounding guessing.

### Overall framing

**The baseline wins on pattern-matchable evidence. The LLM wins where evidence is sparse or absent.
The partial tier is 0% for both.** That is a more useful finding than either source winning outright,
and it is an honest answer to "does the LLM add value here": yes, specifically on the cases
pattern-matching structurally cannot reach (Killer Croc, and the calibrated non-answer on Ra's al
Ghul) — and not otherwise. A reader who only wants the accuracy numbers will conclude the LLM lost;
the coverage-by-tier and Croc/Ra's al Ghul detail is where the actual result is.
