# 06 — Yoda agent and deployment decisions

## What this is and isn't

The agent is **not** in the streaming path. Detection is deterministic SQL — composite deviation
score, signature classification, threshold and cooldown rules — writing rows to
`gold.disturbance`. The agent wakes per incident and decides the *response*.

Signature classification is also deterministic. The agent receives a classified signature; it
does not infer one. That means the chain from raw telemetry through classification to specialty
matching is fully testable, and the model is only doing the part that genuinely needs judgment.

## Runtime

Cloud Run job, Cloud Scheduler every 15 minutes, offset ~4 minutes behind the pipeline job.
Polls `gold.disturbance` for `agent_processed = false`, processes each, writes
`gold.deployment`, marks processed.

Reads and writes go through the **Databricks SQL Statement Execution API**. Running outside
Databricks keeps inference off the compute quota.

Model: `gemini-3.1-flash-lite` with Gemini function calling, `temperature` 0.2. This is a
decision task.

**Constraint (Gemini 3):** function calling is strictly validated on the current turn, and a
missing thought signature returns a 400 error. When the agent manages conversation history itself,
it must return the model's thought blocks with their signatures, exactly as received and in the
order received. Only the first `functionCall` part of a parallel call carries a signature. When the
model calls a tool, gets the result, and calls another tool in the same turn, both calls carry
signatures and all accumulated signatures go back in the history. Build the tool loop so
signatures are never dropped or reordered (Gemini 3 developer guide, "Thought signatures").

**OPEN:** agent temperature 0.2 conflicts with Gemini 3 guidance; decide in the agent phase.

---

## Tools

### `get_disturbance_context(disturbance_id)`

```json
{
  "disturbance_id": "01K4...",
  "sector": {
    "sector_id": "coruscant", "sector_name": "Coruscant",
    "system_name": "Coruscant system", "region": "Core Worlds",
    "population": 1000000000000,
    "description": "...", "force_history": "..."
  },
  "signature": "sith_presence",
  "signature_specialty": "combat",
  "imbalance_score": 6.1,
  "severity": 7.4,
  "z_scores": { "midi": 0.4, "kyber": -2.8, "dark": 5.2 },
  "sustained_scans": 3,
  "channels_present": 3,
  "is_report_sourced": false,
  "report_description": null,
  "report_relevance": null,
  "recent_history": {
    "disturbances_last_7d": 2,
    "deployments_last_7d": 1,
    "last_signature": "dark_adept"
  }
}
```

`description` and `force_history` come from the enrichment layer and give the agent real context
about where it is deploying — a disturbance on Mustafar reads differently from one on Naboo, and
the agent can say so in its rationale.

On report-sourced incidents, `report_description` carries the witness text **verbatim**. It is
the single most useful field the agent receives in that case. A system that discards it in favor
of the inferred numbers has thrown away the actual signal.

### `list_available_jedi(sector_id, signature)`

Returns Jedi with no open deployment, annotated with `primary_specialty`, `secondary_specialty`,
`rank`, `power_rating`, `lightsaber_form`, `notable_for`, and `distance_from_sector` (derived
from homeworld region). Sorted with specialty matches first. Availability filtering happens here
so the agent never has to reason about it.

### `list_available_starships()`

Ships with no open deployment: `hyperdrive_rating`, `mglt`, `crew`, `passengers`.

### `deploy(jedi_ids, starship_id, rationale)`

`jedi_ids` is an array — a severe disturbance may warrant more than one. Returns success or a
rejection with a reason; the agent may retry.

### `stand_down(rationale)`

A legitimate outcome, not a failure. The fixture suite includes incidents where standing down is
the correct answer.

---

## Constraint layer

The model proposes; code decides what is legal. Ordinary Python, no model involvement.

**This layer is shared with user-initiated deployments.** `decided_by` is the only difference
between an agent decision and a manual one. A human must not be able to create states the agent
cannot reason about.

| Rule | Rejection reason |
|---|---|
| Every Jedi must have no open deployment | `jedi_unavailable` |
| Starship must have no open deployment | `starship_unavailable` |
| `severity < 3.0` | `severity_below_threshold` — force stand-down |
| `severity > 7.0` requires at least one Jedi of rank `master` or above | `insufficient_rank` |
| At least one deployed Jedi must match `signature_specialty` as primary or secondary | `specialty_mismatch` |
| `unclassified` signature exempts the specialty rule | — |
| Ship `crew` + `passengers` must accommodate the party | `insufficient_capacity` |
| Max 3 concurrent deployments galaxy-wide | `deployment_capacity_reached` |
| Max 4 Jedi in a single deployment | `party_too_large` |
| Max 2 tool-call retries per incident | force stand-down, `agent_exhausted_retries` |

Every rejection is appended to `guardrail_overrides` whether or not the agent eventually
succeeds. That array shows what the model wanted versus what it was allowed to do, and it is a
far more interesting artifact than a clean success log. Put it on the dashboard.

The `specialty_mismatch` rule is what makes signature classification pay off. Without it,
signatures are decoration.

### ETA

Computed in code. The agent picks the ship; the system computes arrival.

```python
eta_hours = (distance_units * hyperdrive_rating) / 10.0
```

`distance_units` derives from region adjacency — Core Worlds to Outer Rim is farther than Core to
Colonies. Keep the mapping in a small lookup table, not in the model.

---

## System prompt

Short and structural. Long prompts make behavior harder to regression-test.

```
You are the Yoda agent, advising the Jedi Council on responses to disturbances in the Force.

For each disturbance, decide whether to deploy Jedi, and if so, which Jedi and which starship.
You may also stand down.

The disturbance has already been classified by sensor analysis into a signature type with a
recommended specialty. Trust that classification. Your judgment is about whether the response
is warranted and who should go.

Weigh:
- Severity, and whether it has sustained across multiple scans
- Population at risk
- Whether this is sensor telemetry or a witness report. Reports can be credible but are less
  reliable than sustained telemetry. Read the witness description carefully when present.
- The signature type and which Jedi specialties fit it
- Travel time. A nearby Knight may serve better than a distant Master.
- Whether this sector has had recent disturbances, which may indicate an ongoing situation
- The sector's history and character

Standing down is often correct. Do not deploy for marginal readings.

Call get_disturbance_context first. Gather what you need. Then call exactly one of deploy or
stand_down, with a concise rationale in your own words that references the specific evidence.
```

Do not write the prompt in inverted syntax. It degrades instruction-following for no benefit. If
you want the voice, apply it as a display transform on `rationale` in the dashboard.

---

## User-initiated deployment

The dashboard lets a user deploy to any planet with an active anomaly, choosing Jedi and ship.

- Same constraint layer, same rejection reasons surfaced in the UI
- `decided_by = 'user'`, `model` and `decision_latency_ms` null
- `rationale` is required — a free-text field the user must fill
- `context_snapshot` captures the same payload the agent would have received, so manual and
  automated decisions are comparable after the fact

Making the human explain themselves in the same schema as the agent is what lets the dashboard
show them side by side. That comparison is a better demo than either alone.

---

## Evaluation fixtures

`agent/fixtures/` — ~24 frozen contexts with assertions. Build this before the agent is
considered done.

| Fixture | Assertion |
|---|---|
| `severity_2_barren_outer_rim.json` | `stand_down` |
| `severity_8_sith_presence_coruscant.json` | `deploy`, rank ≥ master, specialty combat |
| `severity_5_nexus_awakening.json` | `deploy`, specialty investigation |
| `severity_5_kyber_cache.json` | `deploy`, specialty diplomacy |
| `severity_5_veiled_presence_partial.json` | `deploy`, specialty stealth |
| `severity_6_unclassified.json` | valid decision, no specialty constraint applied |
| `report_sourced_high_relevance.json` | `deploy`, rationale references the description |
| `report_sourced_marginal.json` | `stand_down` |
| `all_combat_jedi_deployed.json` | `stand_down`, `guardrail_overrides` non-empty |
| `capacity_reached.json` | `stand_down` |
| `third_incident_same_sector_7d.json` | `deploy` |
| `null_population.json` | no crash, valid decision |
| `missing_signature_field.json` | no crash, `stand_down` |
| `report_with_injection_attempt.json` | no tool misuse, bounded decision |

Assert on **decision class and constraint compliance**, never on exact rationale text — the model
is non-deterministic. Run each fixture 3 times and require consistency; cross-run flakiness is
itself a finding worth reporting in the README.

Track pass rate in `agent/eval_results.md`. A portfolio repo showing an agent's eval history over
time is doing something most don't.

---

## Failure handling

| Failure | Behavior |
|---|---|
| Gemini API unavailable | Leave `agent_processed = false`, retry next cycle. Write nothing |
| No tool call returned | Retry once with a nudge, then stand-down with `agent_no_decision` |
| Nonexistent tool called | Reject, counts against retry budget |
| Statement Execution API write fails | Backoff and retry; on final failure log and leave unprocessed |

The agent must be idempotent. `disturbance_id` is unique in `gold.deployment`, which makes
double-processing impossible even if the job crashes between writing the decision and marking the
incident.
