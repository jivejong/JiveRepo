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

Append-only from Auto Loader; a live row is never updated by the ingestion notebook itself. One documented exception: a single,
one-time backfill MERGE (doc 05) that fills `_file_modified_ts` on existing live rows. No deduplication otherwise — bronze is a
faithful record of what arrived, duplicates included.

| Column | Type |
|---|---|
| `event_id`, `source_id`, `source_type`, `mode`, `scan_id`, `sector_id` | STRING |
| `schema_version` | INT |
| `event_time` | TIMESTAMP |
| `is_synthetic` | BOOLEAN |
| `synthetic_ingest_ts` | TIMESTAMP (null unless `is_synthetic`) |
| `payload` | VARIANT |
| `dt` | DATE (partition) |
| `hh` | INT |
| `_source_file` | STRING |
| `_ingest_ts` | TIMESTAMP |
| `_file_modified_ts` | TIMESTAMP (NULL on rows ingested before this column existed; silver never reads it for `is_synthetic` rows) |
| `_rescued_data` | STRING (Auto Loader `rescue` mode; null when nothing was rescued) |

**Decided, Phase 4:** `_ingest_ts` remains `current_timestamp()`, unaffected. A new column, `_file_modified_ts`, captures
`_metadata.file_modification_time` for every row ingested from this point on. `silver.probe_reading.ingest_lag_seconds` is
defined on `COALESCE(_file_modified_ts, _ingest_ts)` for live rows, and unchanged (`synthetic_ingest_ts`) for `is_synthetic`
rows. A one-time backfill MERGE, run once and recorded in `docs/ENGINEERING-LOG.md`, fills `_file_modified_ts` on every existing
**live** row: it joins a `read_files(..., format => 'text')` scan of the landing volume's `_metadata.file_path` (the same value
Auto Loader already stores as `_source_file`) directly against `force.bronze.events._source_file`. `is_synthetic` rows are left
NULL — their lag is never read from this column. Run only while the ingestion notebook isn't running (or, once Job 1
exists, while it is paused), so the MERGE's transaction never overlaps the Auto Loader writer's. This means `force.bronze.events`'s Delta history is no longer pure-append
from that point on: nothing today reads this table as a stream, but any future consumer that does would need to handle
non-append changes (`skipChangeCommits`/`ignoreChanges`, or read a snapshot).

---

## Silver

Typed, validated, deduplicated. Incremental with `merge` on `event_id`.

### `silver.probe_reading`

`source_type = 'probe'`. Three channels extracted to typed columns.

| Added column | Definition |
|---|---|
| `ingest_lag_seconds` | `unix_timestamp(CASE WHEN is_synthetic THEN synthetic_ingest_ts ELSE COALESCE(_file_modified_ts, _ingest_ts) END) - unix_timestamp(event_time)` |
| `is_replayed` | `ingest_lag_seconds > 1800` |
| `was_buffered` | `mode = 'DISCONNECTED'` — the reading's own mode when taken (doc 02), carried straight from bronze |
| `is_synthetic` | carried from bronze, so the dashboard can tell backfill from live |
| `is_partial` | any of the three channels null — true in `STEALTH` |
| `channels_present` | int, 1–3 |

`is_replayed` means "arrived more than 30 minutes after it was taken" — a lag threshold, not a direct signal that the row came
from the buffer. `was_buffered` is that direct signal: every `DISCONNECTED`-mode row was buffered and later drained, regardless
of how quickly the drain caught up to it (a row buffered for under 30 minutes has `was_buffered = true` but `is_replayed =
false`). Surface both on the dashboard — `was_buffered` is the proof the late-arriving path works; `is_replayed` is the
operational lag signal.

**The reverse combination is also real, found live, 2026-10-05.** `was_buffered` records the probe's own mode *at the moment
the reading was taken* (doc 02), not how the reading actually reached the broker. A link can die silently between one scan
and the next: the probe takes the next scan still believing it is `CONNECTED` (no keep-alive timeout has fired yet), so that
reading is captured and labelled `was_buffered = false` — but its actual publish never completes until the link recovers and
the buffered backlog drains, which can be hours later. The observed case: the 2026-10-05 06:45Z scan, 60 rows, all
`was_buffered = false` (captured `CONNECTED`) yet `is_replayed = true` (arrival lag ~23,320 s, well over the 1,800 s
threshold) — the opposite pairing from the paragraph above, and just as real. `was_buffered` is still a true record of the
probe's own mode at capture; it was never meant to answer "did this arrive late," which is exactly what `is_replayed` is for.

**Lag precision.** `_file_modified_ts` has 1-second resolution, so `ingest_lag_seconds` carries roughly ±1 s of rounding
on its own, on top of whatever clock skew exists between the device that wrote `event_time` and the volume's own clock.
A live row can legitimately read a small *negative* lag — arrival appearing to precede the reading — without anything
being wrong. Tolerance: `ingest_lag_seconds >= -2`. The Phase 2 desktop-simulator rows (`--assume-clock-synced`, doc 04)
reach as low as -1,950 ms; the Pi's own rows, clock-gated before every scan (doc 04), have never gone negative. A lag
below -2 s would point at a real clock problem, not rounding.

Deduplicated insert-only on `event_id`: when the same event lands twice (the known 2026-09-27 23:45Z scan, 13 ids — see
`docs/ENGINEERING-LOG.md`), the copy with the earliest arrival wins and later copies are never merged over it, so a
later-arriving, higher-latency copy of an already-landed row can never flip `is_replayed`/`was_buffered` on a row already
scored.

### `silver.force_report`

`source_type = 'report'`. Preserves `description` verbatim alongside inferred values,
`relevance_score`, `inference_model`, and `inference_rationale`.

### `silver.rejects`

Validation failures with a reason. Fed by the 2–3% fault injection during `CONNECTED`.

| `reject_reason` | Trigger |
|---|---|
| `unknown_schema_version` | `schema_version` not `1` (no fault currently produces this — `schema_version` is static until a real payload change) |
| `impossible_timestamp` | `event_time` more than 10 minutes ahead of arrival (`COALESCE(_file_modified_ts, _ingest_ts)`, or `synthetic_ingest_ts` for backfill rows), or more than 90 days behind it |
| `unknown_sector` | `sector_id` not in `dim_sector` |
| `null_required_field` | A channel is null outside of `STEALTH`, or `payload` itself is NULL (a malformed event, doc 05) |
| `out_of_range` | Channel value outside its valid range |

Checked in this order — first match wins, the same evaluation style as signature classification (below). The four
fault-injected categories (`edge/probe/faults.py`) map one-to-one onto four of these five reasons under this order: each
injected reading carries exactly one fault and never collides with another check. `unknown_schema_version` has no
corresponding fault — it is exercised only by a future real schema bump, and the fault-reconciliation checkpoint does not (and
cannot) cover it. A non-null `_rescued_data` (an unexpected field Auto Loader could not place) is **not** a reject — Auto
Loader already got the row into bronze either way — but is asserted empty by a warn-severity dbt test. `sensor_temp_c` and
`battery_pct` are not range-checked in Phase 4: no fault touches them (`faults.py`'s `SCIENCE_CHANNELS` covers only the three
science channels), so nothing requires it for the fault-reconciliation checkpoint.

**STEALTH-aware routing.** `edge/probe/runtime.py:148` already gates fault injection to `mode == CONNECTED` only, so a
`STEALTH` row can never carry an injected fault today — but silver's validation logic does not rely on that staying
true. A `mode = 'STEALTH'` row skips `null_required_field` only for its two expected-null channels
(`midichlorian_ppm`, `kyber_resonance`, doc 02); every other check — `unknown_schema_version`, `impossible_timestamp`,
`unknown_sector`, and `out_of_range` on `dark_side_activity` (the one channel `STEALTH` actually reports) — still
applies in the order above. A `STEALTH` row that passes routes to `probe_reading` with `is_partial = true`; one that
fails `impossible_timestamp`, `unknown_sector` or `out_of_range` still lands here with the correct reason.

Deduplicated the same way as `silver.probe_reading`, insert-only on `event_id`, earliest arrival wins — so doc 04's "the count
of rejects must equal the count in this log" stays a true 1:1 comparison even if a faulted reading is somehow ingested more
than once.

Validation must be `STEALTH`-aware: nulls on midichlorian and kyber are expected in that mode and
must route to `probe_reading` with `is_partial = true`, not to rejects. This distinction is a
genuine piece of pipeline logic, not boilerplate.

Validation also checks for housekeeping events first (doc 02: `payload.kind` is reserved). An event with a `payload.kind` goes to a
probe-events path before the sector check. It never reaches `probe_reading`, the baselines or the rejects, so a `buffer_overflow`
event is not an `unknown_sector` reject and does not count against the fault-injection reconciliation.

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

**Window anchor, decided Phase 4:** `computed_at` is `current_timestamp()`, captured once per build in a CTE so every row
in one build shares the same anchor, not the run's calendar date. The window is `[computed_at - 90 days, computed_at)`.
This model is built by hand this phase (excluded from Job 1's own transform task) and rebuilt daily once
scheduled, so the anchor drifts forward with wall-clock time rather than snapping to midnight.

### `gold.sector_reading`

Grain: one row per planet per scan. No windowing — at one scan per planet per 15 minutes, the
scan *is* the grain.

| Column | Notes |
|---|---|
| `event_id` | Not originally listed here — added Phase 4. The merge key in practice: 1:1 with (`sector_id`, `scan_id`) once silver has deduplicated |
| `sector_id`, `scan_id`, `event_time`, `source_type` | |
| `midichlorian_ppm`, `kyber_resonance`, `dark_side_activity` | |
| `z_midi`, `z_kyber`, `z_dark` | **Signed.** Direction matters |
| `imbalance_score` | Composite magnitude |
| `signature` | Classified pattern |
| `channels_present` | |
| `is_replayed` | Not originally listed here — added Phase 4. Carried straight through from `silver.probe_reading` (above), unchanged. Needed so `gold.disturbance` can tell whether a run's onset scan arrived via replay (`cooldown_conflict`, "definition B") -- this table is the only place that information can reach `gold.disturbance` from, since it never reads silver directly |
| `_gold_built_at` | Not originally listed here — added Phase 4. Bookkeeping only, not part of the doc's original contract: this build's `current_timestamp()`, stamped on every row, the same way bronze stamps `_ingest_ts`. The recompute-window formula below needs "which silver rows arrived since the last time this model ran," which nothing else in the warehouse tracks |

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

**Deferred to Phase 6:** the scaling factor above gives a dark-only `STEALTH` reading about 1.5x the score variance of a full
reading with the same dark deviation, so it trips the 5.75 emergency threshold at `z_dark ≈ 2.35` instead of `≈ 4.07`. An
alternative, `SQRT(4/present_weight)`, would remove that gap. `edge/analyze_thresholds.py` gets a STEALTH-series comparison of
both factors before Phase 6's threshold retuning decides between them; the formula above is unchanged for Phase 4.

Keep the signed z-scores as columns. The composite gives magnitude; the signed triple gives
direction, which is what signature classification reads.

**Thresholds.** Anomaly above 4.0, emergency above 5.75. Tuned on 2026-09-26 on the full local
backfill (60 planets x 8,640 scans; the analysis is in `PHASE2-RESULTS.md`). The emergency threshold
comes from a false-alarm target: at most about one false sustained incident (2 or more consecutive
scans) per week galaxy-wide, on noise alone. At 5.75 the noise-only sustained runs are 9 per 90 days
(about 0.7 a week) and 32 of the 52 ambient spike episodes (62%) still fire; it is the lowest
threshold that meets the target. The anomaly threshold is provisional until Phase 6 defines "active
anomaly" (doc 06); 4.0 puts about 1% of scans over it on noise. Retuning requires rerunning the
analysis (`edge/analyze_thresholds.py`). Changing these requires updating `edge/forcesim` constants
in the same commit; `test_doc_parity.py` enforces it.

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
| `veiled_presence` | `COALESCE(ABS(z_midi), 0) < 1.0` and `z_dark > 2.0` and `channels_present < 3` | `stealth` |
| `unclassified` | fallback | any |

Evaluate in the order listed; first match wins. Implement as a `CASE` expression in a dbt macro
so it is testable in isolation and shared between the Databricks and Postgres targets.

`unclassified` must remain reachable — an incident the system can't categorize is a real
outcome, and the agent should handle it.

**Decided, Phase 4:** a `STEALTH` reading has no midichlorian value, so plain `ABS(z_midi) < 1.0` was never true and the
signature could not fire. Fixed rule-locally, not globally: `COALESCE(ABS(z_midi), 0) < 1.0` treats an absent midi channel as
satisfying this one zero-bound condition, since it is the only condition in the table that bounds *toward* zero rather than
asserting a direction. A directional condition (`>`/`<`) on an absent channel is still NULL, still false — no other rule in
this table is affected. `veiled_presence` stays unproducible by the control-topic injector this phase
(`edge/forcesim/signatures.py`); making it injectable is deferred.

### `gold.disturbance`

| Column | Notes |
|---|---|
| `disturbance_id` | **Deterministic**, not a random ULID — see below |
| `sector_id` | |
| `detected_at` | `event_time` of the incident's most recent qualifying scan **at build time** -- not a fixed moment. Because this model rescans all history every build, an incident that is still ongoing (more qualifying scans keep landing) has `detected_at` advance on every rebuild, right along with `sustained_scans`. The 2-hour cooldown (below) is measured from `detected_at` -- a quiet period after the run's last qualifying scan so far, not from its onset |
| `scan_id` | the run's **onset** scan (`min(scan_id)`) -- by design, a different scan from the one `detected_at` reflects (above). A fixed `confirmed_at` (stamped once, at the run's second qualifying scan, never moved again) was considered Stage 4a and is **deferred to Phase 6**: it is a new column on an already-incremental model with `on_schema_change: fail`, and nothing in the current checkpoints requires it |
| `imbalance_score`, `z_midi`, `z_kyber`, `z_dark` | |
| `signature` | |
| `severity` | see below |
| `is_report_sourced` | BOOLEAN |
| `report_description` | Verbatim text, null for probe-sourced |
| `report_relevance` | null for probe-sourced |
| `sustained_scans` | consecutive scans above threshold — see "Consecutive," below, for what that means across sectors with different cadences |
| `agent_processed` | BOOLEAN, default false |
| `cooldown_conflict` | BOOLEAN, default false. Set on an **existing** incident's row only when a later onset for the same sector both (a) would have fired inside that incident's 2-hour cooldown AND (b) arrived via **replay** — an ordinary, in-order onset suppressed by an active cooldown is the cooldown working as designed, not a conflict. The late onset itself never gets its own row either way — see "Firing rules" below |

**Deviation from `generate_ulid.sql`:** `disturbance_id` is **deterministic**, not randomly generated — 48 bits of the onset
scan's `event_time` (epoch ms) followed by a hash of (`sector_id`, onset `event_time`), laid out in ULID's own base-32
encoding so it stays sortable and ULID-shaped. A random id would make two dbt runs over the same onset produce two
different ids, breaking the merge key the incremental `gold.disturbance` needs. `gold.deployment.deployment_id` is unaffected —
it isn't built until Phase 6, which decides its own id scheme then. `macros/generate_ulid.sql` is renamed
`deterministic_id.sql` in doc 05's layout, scoped to `disturbance_id` only.

**Deviation, established on Databricks (2026-09-30, a build failure, not guessed):** the hash portion is 64 bits (16 hex
characters of a sha2-256 digest), not the originally documented 80. `conv()` on Databricks operates on a 64-bit integer
internally and raises `ARITHMETIC_OVERFLOW` past that — confirmed live: a 16-hex-char (64-bit) input works, the same call
on a 20-hex-char (80-bit) input fails every time. The id is now 48 (timestamp) + 64 (hash) = 112 bits, not the full 128 a
real ULID carries, which also shortens its encoded length (the hash segment is 13 base-32 characters, not 16). 64 bits of
hash entropy is still far beyond any collision risk this project will ever produce (a few hundred incidents, total, ever)
— accepted as a permanent design point, not a placeholder to revisit.

**"Consecutive," decided Phase 4 — doc did not say:** consecutive *rows* for a sector in `gold.sector_reading`, ordered by
`event_time`, not consecutive 15-minute clock slots. This is what lets "2 consecutive scans" mean the same thing for a
`STEALTH` sector (hourly cadence — 2 hours of wall-clock time) as for a `CONNECTED` one (30 minutes), and what makes a
missing scan (no row at all) invisible to a run rather than a below-threshold reading that would break it. A genuine
below-threshold *reading*, by contrast, does break a run even if immediately followed by another above-threshold one —
only a true gap is transparent to this definition.

```sql
severity = imbalance_score * (1 + LOG10(GREATEST(population, 10)) / 10)
```

Population-weighted, so the same reading over Coruscant outranks one over a barren world.

**Firing rules — probe-sourced:**

- `imbalance_score > 5.75`
- Sustained across at least 2 consecutive scans — 30 minutes for a `CONNECTED` sector at its 15-minute cadence, 2 hours
  for a `STEALTH` sector at its hourly one (see "Consecutive," above)
- Cooldown: no new incident for the same `sector_id` within 2 hours

**Late-replayed onset inside an existing cooldown — "definition B", decided Stage 4a.** A `BURST` drain can insert an onset
scan older than an incident already recorded for that sector. If that onset would itself have fired within the existing
incident's 2-hour cooldown, the recompute does not create a second incident and does not rewrite the existing one's
detection fields: it sets `cooldown_conflict = true` on the **existing incident's row** and stops. **This is specifically
about a *replayed* onset** — one whose own scan arrived via replay (`is_replayed = true`, doc 03's own `silver.probe_reading`
definition, carried through `gold.sector_reading`), not merely "arrived after" in wall-clock build order. An **ordinary**
second onset for the same sector, arriving in order and falling inside an active cooldown, is the cooldown mechanism working
exactly as intended — no flag, nothing to review, not a suppressed duplicate. `cooldown_conflict = true` means something
more specific: a replayed onset was discovered late, *after* the incident whose cooldown it falls inside had already been
accepted and potentially already acted on — that is what needs manual review, not suppression by itself.

**Resolved, 2026-09-30.** The cooldown check previously compared each run only to the *immediately preceding* run for the
same sector (a `LAG()`, not a walk carrying the last **accepted** incident forward), on the mistaken belief that
Databricks SQL doesn't support `WITH RECURSIVE`. It does (confirmed live, DBSQL 2026.36) — the bug this caused was the
opposite of what an earlier draft of this note guessed: a run that itself gets suppressed can still be a *long* one, and
its own `detected_at` (the old `LAG` basis) could fall later than the true last-accepted incident's `detected_at` would
— making the following run's cooldown window too *long*, not too short, and incorrectly **suppressing** a run genuinely
clear of the original incident's cooldown. Verified with a 3-run fixture before fixing (accepted run, then a long
suppressed run, then a run that should be a clear, new incident) that the old `LAG`-based check suppressed the third run
where a true last-accepted-incident walk correctly accepts it.

**Fix:** `gold_disturbance` now walks runs per sector in onset order with a recursive CTE, carrying the last accepted
incident's `detected_at` forward instead of the immediately preceding run's own. Regression-tested with the same 3-run
shape (`unit_test_disturbance_third_rapid_run_is_accepted`). On the real 90-day backfill, the fix changed **zero** rows
— rebuilt with `--full-refresh`, then twice more without it, all three runs producing the identical 49-row,
byte-identical disturbance list as before the fix (same sectors, `detected_at`, signatures, `sustained_scans`). Checked
why: the minimum gap between any two accepted incidents for the same sector, anywhere in the real data, is 47.5 hours
(`kalee`) — far beyond the 2-hour cooldown the bug needs to matter, so the bug was real (and is now fixed, and
regression-tested) but never actually fired on this dataset.

**`detected_at` semantics -- decided Stage 4a.** The current behavior is intended, not an oversight: `detected_at` is the
`event_time` of the incident's latest qualifying scan at build time, so it advances on every rebuild while the incident
is still ongoing. `scan_id` is unaffected by this -- it is always the run's onset scan and does not move -- so the two
columns deliberately refer to different scans in the same row. The 2-hour cooldown above is measured from `detected_at`,
i.e. from the end of the qualifying run so far, not from its onset: a sector only leaves cooldown once its readings have
actually gone quiet for 2 hours, not 2 hours after the anomaly first began. A fixed `confirmed_at`, stamped once at the
run's second qualifying scan and never moved again, would pin a single moment instead -- that's deferred to Phase 6 (see
`gold.disturbance`'s column table, above) because it's a schema change, not because the current behavior is wrong.

**Firing rules — report-sourced:**

- `relevance_score >= 0.7` **and** inferred `imbalance_score > 5.75`
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
- `synthetic_ingest_ts` is null on every bronze row where `is_synthetic` is false, and not null
  where it is true
