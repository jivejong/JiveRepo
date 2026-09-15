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

Technique recall grouped by observability tier:

```
tier      techniques  attempts  recalled  recall
high               7       412       —      —
partial            6       288       —      —
low               10       351       —      —
overall           23      1051       —      —
```

The expected shape: strong on `high`, mixed on `partial`, near-zero on `low`. Write that up as a
**detection coverage gap analysis**, because that is what it is. The conclusion a security team
would draw is that an HTTP sensor alone cannot see a third of the technique catalog, and closing
that gap requires endpoint or identity telemetry the honeypot does not have.

That is a substantive result. It is also a much better README section than a single accuracy number,
and it is the kind of thing that reads as domain understanding rather than a demo.

Note the design consequence worth calling out: Killer Croc's gating leaves him only
high-observability techniques, so he is loud and easy to reconstruct. Ra's al Ghul can reach the
quiet ones. **The most capable villain is the hardest to detect**, which falls out of the stat
gating rather than being arranged.

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

- Model: Groq, Llama. Pin the exact model string in config, not code.
- `GROQ_API_KEY` from environment, never committed
- `temperature: 0` for reproducibility
- Timeout 30s, one retry with backoff
- **Without `GROQ_API_KEY`, triage runs the baseline instead**, so the entire pipeline including
  both evaluations is demonstrable with zero credentials. This matters for the durability goal: a
  reader can clone and run everything with no accounts at all.
