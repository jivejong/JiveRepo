# 03 — Data model

Catalog: `force`. Schemas: `raw`, `bronze`, `silver`, `gold`.

---

## Dimensions

Loaded via `dbt seed` from committed CSVs. Generated once by the AI enrichment layer — see
doc 08. **Never fetched or regenerated at runtime.**

### `dim_sector` — 60 rows

SWAPI planets enriched with Force parameters, system, region, and narrative context.

SWAPI passthroughs: `sector_id`, `sector_name`, `climate`, `terrain`, `population`,
`diameter_km`. `sector_name` is the planet's own name; `system_name` (below) is its star system.

Key columns beyond the SWAPI passthroughs:

| Column | Notes |
|---|---|
| `system_name`, `region` | Region is one of 8 canonical values. Drives dashboard grouping |
| `midi_baseline`, `midi_sigma` | Stable channel — sigma 2–5% of baseline |
| `kyber_baseline`, `kyber_sigma` | Variable — sigma 8–15% |
| `dark_baseline`, `dark_sigma`, `dark_spike_probability` | Quiet with rare spikes |
| `description`, `force_history` | Shown in the dashboard planet detail |
| `canon_confidence` | Review aid, retained for transparency |
| `is_unknown` | True for the one SWAPI planet with no data (planets/28, `sector_id` `uncharted`, name `unknown`). Its baselines are the medians of the other 59 sectors. Excluded from spread and anchor checks (doc 08) |

These baselines are the **static** parameters used by the generator. They are distinct from the
**rolling** baselines in `gold.sector_baseline`, which are computed from observed data. The
static values seed the simulation; the rolling values drive detection.

### `dim_jedi` — 17 rows, prequel era

SWAPI people filtered to the Jedi roster (Jedi Order members in Episodes I-III), enriched. No
invented rows.

| Column | Notes |
|---|---|
| `rank` | `padawan` → `grand_master` |
| `primary_specialty` | `combat` \| `diplomacy` \| `investigation` \| `stealth` — used by the constraint layer |
| `secondary_specialty` | nullable |
| `power_rating` | 1–10 |
| `lightsaber_form`, `notable_for` | Dashboard flavor |

### `dim_species`, `dim_starship`

From SWAPI, unchanged. Starships with null or `"unknown"` `hyperdrive_rating` are filtered out
at seed time, not at runtime.

---

## Bronze

### `bronze.events`

Append-only, one row per ingested event. No deduplication — bronze is a faithful record of what
arrived, duplicates included.

| Column | Type |
|---|---|
| `event_id`, `source_id`, `source_type`, `mode`, `scan_id`, `sector_id` | STRING |
| `schema_version` | INT |
| `event_time` | TIMESTAMP |
| `payload` | VARIANT |
| `dt` | DATE (partition) |
| `hh` | INT |
| `_source_file` | STRING |
| `_ingest_ts` | TIMESTAMP |

---

## Silver

Typed, validated, deduplicated. Incremental with `merge` on `event_id`.

### `silver.probe_reading`

`source_type = 'probe'`. Three channels extracted to typed columns.

| Added column | Definition |
|---|---|
| `ingest_lag_seconds` | `unix_timestamp(_ingest_ts) - unix_timestamp(event_time)` |
| `is_replayed` | `ingest_lag_seconds > 1800` |
| `is_partial` | any of the three channels null — true in `STEALTH` |
| `channels_present` | int, 1–3 |

`is_replayed` flags data recovered from a `DISCONNECTED` buffer. Surface it on the dashboard —
it is the visible proof the late-arriving path works.

### `silver.force_report`

`source_type = 'report'`. Preserves `description` verbatim alongside inferred values,
`relevance_score`, `inference_model`, and `inference_rationale`.

### `silver.rejects`

Validation failures with a reason. Fed by the 2–3% fault injection during `CONNECTED`.

| `reject_reason` | Trigger |
|---|---|
| `null_required_field` | A channel is null outside of `STEALTH` |
| `out_of_range` | Channel value outside its valid range |
| `unknown_sector` | `sector_id` not in `dim_sector` |
| `unknown_schema_version` | |
| `impossible_timestamp` | `event_time` in the future or absurdly old |

Validation must be `STEALTH`-aware: nulls on midichlorian and kyber are expected in that mode and
must route to `probe_reading` with `is_partial = true`, not to rejects. This distinction is a
genuine piece of pipeline logic, not boilerplate.

### `silver.source_health`

Latest state per source: current mode, last seen, buffer depth if reported, scan completeness
(did the last `scan_id` contain all 60 planets?). Drives the fleet health panel.

---

## Gold

### `gold.sector_baseline`

Rolling 90-day statistics per planet per channel. Rebuilt daily.

| Column | Notes |
|---|---|
| `sector_id` | |
| `channel` | `midichlorian` \| `kyber` \| `dark_side` |
| `mean_90d`, `stddev_90d` | |
| `sample_count` | |
| `computed_at` | |

**`WHERE source_type = 'probe'` is mandatory here.** Report-inferred values must never enter the
baseline. Add a dbt test asserting `sample_count` matches the probe-only row count for the window.

Cold start is handled by the Phase 2 synthetic backfill — 90 days of history exist before the
first live scan.

### `gold.sector_reading`

Grain: one row per planet per scan. No windowing — at one scan per planet per 15 minutes, the
scan *is* the grain.

| Column | Notes |
|---|---|
| `sector_id`, `scan_id`, `event_time`, `source_type` | |
| `midichlorian_ppm`, `kyber_resonance`, `dark_side_activity` | |
| `z_midi`, `z_kyber`, `z_dark` | **Signed.** Direction matters |
| `imbalance_score` | Composite magnitude |
| `signature` | Classified pattern |
| `channels_present` | |

**Composite deviation score:**

```sql
z_midi  = (midichlorian_ppm   - b.mean_90d_midi)  / NULLIF(b.stddev_90d_midi, 0)
z_kyber = (kyber_resonance    - b.mean_90d_kyber) / NULLIF(b.stddev_90d_kyber, 0)
z_dark  = (dark_side_activity - b.mean_90d_dark)  / NULLIF(b.stddev_90d_dark, 0)

imbalance_score = SQRT(
    1.0 * POWER(COALESCE(z_midi,  0), 2)
  + 1.0 * POWER(COALESCE(z_kyber, 0), 2)
  + 2.0 * POWER(COALESCE(z_dark,  0), 2)
) * SQRT(3.0 / channels_present)
```

Weighted Euclidean distance from the planet's normal state, dark side double-weighted. The
`SQRT(3/channels_present)` term scales partial readings so a `STEALTH` reading with one channel
isn't automatically lower-scoring than a full one.

Keep the signed z-scores as columns. The composite gives magnitude; the signed triple gives
direction, which is what signature classification reads.

**Thresholds.** Anomaly above 3.0, emergency above 4.5. Tune these after the backfill exists and
you can see actual distributions — treat the initial values as placeholders, not settled.

### Signature classification

Deterministic SQL from the signed z-scores. **Not an LLM.** This is what makes the agent's
Jedi selection non-arbitrary and testable.

| `signature` | Pattern | Matched specialty |
|---|---|---|
| `sith_presence` | `z_dark > 2.5` and `z_kyber < -1.0` | `combat` |
| `dark_adept` | `z_dark > 2.0` and `z_midi > 1.5` | `combat` |
| `nexus_awakening` | `z_midi > 2.0` and `z_kyber > 2.0` and `ABS(z_dark) < 1.5` | `investigation` |
| `force_drain` | `z_midi < -2.0` and `z_kyber < -2.0` | `investigation` |
| `kyber_cache` | `z_kyber > 2.5` and `ABS(z_midi) < 1.5` and `ABS(z_dark) < 1.5` | `diplomacy` |
| `civil_unrest` | `z_dark > 1.5` and `population > 1e9` and `ABS(z_kyber) < 1.5` | `diplomacy` |
| `veiled_presence` | `ABS(z_midi) < 1.0` and `z_dark > 2.0` and `channels_present < 3` | `stealth` |
| `unclassified` | fallback | any |

Evaluate in the order listed; first match wins. Implement as a `CASE` expression in a dbt macro
so it is testable in isolation and shared between the Databricks and Postgres targets.

`unclassified` must remain reachable — an incident the system can't categorize is a real
outcome, and the agent should handle it.

### `gold.disturbance`

| Column | Notes |
|---|---|
| `disturbance_id` | ULID |
| `sector_id`, `detected_at`, `scan_id` | |
| `imbalance_score`, `z_midi`, `z_kyber`, `z_dark` | |
| `signature` | |
| `severity` | see below |
| `is_report_sourced` | BOOLEAN |
| `report_description` | Verbatim text, null for probe-sourced |
| `report_relevance` | null for probe-sourced |
| `sustained_scans` | consecutive scans above threshold |
| `agent_processed` | BOOLEAN, default false |

```sql
severity = imbalance_score * (1 + LOG10(GREATEST(population, 10)) / 10)
```

Population-weighted, so the same reading over Coruscant outranks one over a barren world.

**Firing rules — probe-sourced:**

- `imbalance_score > 4.5`
- Sustained across at least 2 consecutive scans (30 minutes)
- Cooldown: no new incident for the same `sector_id` within 2 hours

**Firing rules — report-sourced:**

- `relevance_score >= 0.7` **and** inferred `imbalance_score > 4.5`
- No sustained requirement — a single credible sighting is enough
- Same 2-hour cooldown per sector
- `is_report_sourced = true`

This is the permissive model. "Darth Vader is walking the streets" creates an emergency on its
own. But it is tagged, it never touches the baseline, and both the agent and the dashboard can
see what kind of evidence they are acting on.

A report arriving within the cooldown of an existing probe-sourced incident should **escalate**
it — raise severity, append the description — rather than being suppressed. Corroboration is the
most valuable case in the system and it should not be silently dropped.

### `gold.deployment`

| Column | Notes |
|---|---|
| `deployment_id` | ULID |
| `disturbance_id` | FK, unique |
| `decided_by` | `yoda_agent` \| `user` |
| `decision` | `deploy` \| `stand_down` |
| `jedi_ids` | array — deployments may include more than one Jedi |
| `starship_id` | FK |
| `eta_hours` | computed in code from hyperdrive rating |
| `rationale` | agent's or user's stated reasoning |
| `context_snapshot` | VARIANT — full input the decider saw |
| `signature_at_decision` | |
| `model`, `decision_latency_ms` | null for user decisions |
| `guardrail_overrides` | array of rejected proposals with reasons |
| `decided_at` | |

**User deployments run through the identical constraint layer.** `decided_by` is the only
difference. A human must not be able to create states the agent cannot reason about — such as
deploying a Jedi who is already assigned.

---

## dbt tests

Minimum set. Part of the deliverable.

- `unique` + `not_null` on `event_id` and every PK
- `relationships` from `silver.probe_reading.sector_id` → `dim_sector.sector_id`
- `accepted_values` on `mode`, `source_type`, `signature`, `decision`, `decided_by`,
  `reject_reason`, `region`
- **`gold.sector_baseline` contains no report-sourced data** — the most important test in the project
- `imbalance_score >= 0`
- Cooldown assertion: no two `gold.disturbance` rows for the same sector within 2 hours
- Every `gold.deployment` references a `gold.disturbance` with `agent_processed = true`
- Every `signature` value has at least one matching Jedi specialty available in `dim_jedi`
- Freshness: warn if no new `bronze.events` rows in 45 minutes (three missed scans)
