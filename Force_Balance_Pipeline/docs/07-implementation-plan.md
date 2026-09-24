# 07 — Implementation plan

Each phase ends with a **checkpoint**: something observable that must be true before the next
phase begins. Do not build on an unverified layer.

---

## Phase 0 — De-risk the platform · 2–4h

1. Confirm the account is Free Edition. Create catalog, schemas, volumes per doc 05.
2. Two-model dbt project. Run locally against the SQL warehouse.
3. Configure it as a **`dbt` job task sourced from Git** and run it. Confirms the task type works
   on Free Edition.
4. Attempt a Unity Catalog external location pointing at GCS. Record the result.
5. Check whether `streaming_table` materialization exists in the pinned `dbt-databricks` version.

Outbound egress is already confirmed working — see doc 01.

**Checkpoint:** A dbt job task sourced from Git materializes a table in `force.phase0` on Free
Edition serverless, and the run records the commit SHA. Record answers to 4 and 5 in doc 01 under
"Open items."

If step 3 fails, fall back to a Python task shelling out to dbt. Note it and move on.

---

## Phase 1 — AI enrichment and dimensions · 6–10h

The generator depends on these parameters, so this comes first.

1. `scripts/fetch_swapi.py` — pull planets, people, species, starships from `swapi.info` **with an
   explicit User-Agent**. Commit raw JSON to `data/swapi_snapshot/`.
2. `scripts/jedi_roster.py` — hand-maintained list of prequel-era Force-user SWAPI URLs (~18).
3. `scripts/enrich_planets.py` and `scripts/enrich_jedi.py` at `temperature: 0`.
4. **Review the output.** Work the checklist in doc 08. Expect to regenerate at least once —
   models regress to the mean on numeric tables and you will likely see baselines clustered in
   the middle of each range.
5. Verify specialty distribution across the Jedi roster. At least three Jedi per specialty, or
   certain signatures will have no valid responder.
6. Commit CSVs. Write `ENRICHMENT_PROVENANCE.md`.
7. `dbt seed`.

**Checkpoint:** `dim_sector` has 60 rows with non-null baselines and sigmas spread across their
ranges. `dim_jedi` has ~18 rows covering all four specialties with at least three each. Mustafar's
`dark_baseline` is high; Coruscant's `midi_baseline` is high; Ilum's `kyber_baseline` is high.

**Do not proceed until enrichment is reviewed and committed.** Everything downstream derives from
these numbers, and regenerating later is a migration event.

---

## Phase 2 — Ingestion path and 90-day backfill · 10–14h

Laptop only. No Raspberry Pi yet.

1. Probe simulator, `CONNECTED` mode only. Mean-reverting random walk from the enrichment
   parameters, 60-planet sweep, one burst per cycle.
2. Mosquitto locally.
3. Collector bridge: MQTT subscribe → buffer → flush NDJSON to the UC volume via Files API.
4. Auto Loader notebook. Run manually.
5. **Backfill generator** — 90 days of synthetic history in one shot using the same generator
   code, written directly as NDJSON files into dated volume paths.

Give the backfill texture. Flat data makes a boring dashboard and unrealistic baselines:

- Gradual drift on 3–4 planets
- 3–4 historical emergencies with distinct signatures, fully resolved
- One planet with a slowly rising dark side trend that has not yet crossed threshold
- A few `DISCONNECTED` gaps with later `BURST` recovery, so `is_replayed` has history

**Checkpoint:** run the live probe 45 minutes (3 scans), run Auto Loader, and see 180 rows in
`force.bronze.events` with correct partitions and `payload` queryable as `VARIANT`. Run Auto
Loader again with no new files — zero new rows, proving exactly-once. Backfill loads ~518,400
rows (60 planets × 96 scans/day × 90 days) and the partition count is sane.

---

## Phase 3 — Four modes and hardware · 8–12h

1. Implement `DISCONNECTED`, `BURST`, `STEALTH`.
2. SQLite buffer with confirmed-publish deletion, 100k cap, overflow event.
3. Fault injection at 2–3% during `CONNECTED`, with local logging of what was injected.
4. Control topic for triggering a named signature on demand.
5. Deploy to the Pi 3 (64-bit Raspberry Pi OS Lite), systemd with `Restart=always`.

**Checkpoint:** force `DISCONNECTED` for 45 minutes, then restore. `BURST` drains the buffer, and
`bronze.events` contains the buffered scans with `event_time` spanning the outage and `_ingest_ts`
clustered at replay. No gaps, no duplicates. Separately, force `STEALTH` and confirm those rows
arrive with two null channels.

This checkpoint is the project's core claim. Do not proceed until it holds.

---

## Phase 4 — Transformation, scoring, signatures · 14–22h

1. Silver: `probe_reading`, `force_report` (stub until Phase 5), `rejects`, `source_health`.
   Incremental merge on `event_id`.
2. `extract_payload` macro with Databricks and Postgres branches.
3. **STEALTH-aware validation** — partial readings route to `probe_reading` with `is_partial`,
   not to rejects. This is the trickiest logic in the phase.
4. `gold.sector_baseline` — rolling 90-day, probe-only, rebuilt daily.
5. `gold.sector_reading` — signed z-scores, composite score, signature classification macro.
6. `gold.disturbance` — firing rules, sustained-scan requirement, 2-hour cooldown.
7. dbt tests from doc 03.
8. `docker-compose.yml` and `make demo` against the Postgres target.

**Checkpoint:**
- `dbt build` passes all tests on both `prod` and `local`.
- **The baseline test passes:** `gold.sector_baseline` contains zero report-sourced data.
- Trigger `sith_presence` via the control topic. Within two scans a `gold.disturbance` row
  appears with the correct `signature` and `sustained_scans >= 2`.
- Trigger two disturbances in one sector 30 minutes apart. One incident row — cooldown works.
- Fault injection reconciles: rejects table count matches the injection log, with matching
  `reject_reason` values.
- `STEALTH` readings score correctly with `channels_present = 1` and are not rejected.
- Replay a `DISCONNECTED` buffer. Affected historical rows are recomputed, not duplicated.

---

## Phase 5 — Web intake and inference · 10–14h

1. `POST /api/report` endpoint in the bridge. Inference call with the planet's baseline passed in.
2. **Clamp all returned values to valid ranges in code**, regardless of model output.
3. Browser page: planet picker, description box, confirmation showing inferred values.
4. IndexedDB buffering on network failure.
5. `silver.force_report` model.
6. Report-sourced firing rules in `gold.disturbance`, including the escalation case where a report
   arrives during an existing probe-sourced incident's cooldown.
7. `intake/fixtures/` suite from doc 04, including the prompt-injection case.

**Checkpoint:** "It rained today" scores below 0.15 and creates no incident. "Saw Darth Vader in
the market" scores above 0.85 and creates an incident with `is_report_sourced = true` and the
description preserved verbatim. The injection-attempt fixture produces bounded values. Baselines
remain probe-only — re-run that test.

---

## Phase 6 — Yoda agent · 10–14h

1. Five tools against the SQL Statement Execution API.
2. **Constraint layer first** — ordinary code, easier to test in isolation than through a model.
3. Groq wiring with tool calling.
4. ~24 fixtures from doc 06.
5. `make test-agent`, 3 runs per fixture.
6. Cloud Run job, scheduled 15 minutes offset 4.

**Checkpoint:** fixture suite passes on decision class and constraint compliance across 3 runs
each. A real disturbance from Phase 4 produces a `gold.deployment` row with populated
`context_snapshot`, non-null `eta_hours`, a matched specialty, and a rationale referencing the
actual sector and signature.

---

## Phase 7 — Dashboard · 12–18h

1. `publish_serving` — gold aggregates to Postgres.
2. React dashboard reading Postgres.
3. Manual deployment UI running through the **same constraint layer**, with rejection reasons
   surfaced in the interface.

Panels, in priority order:

| Panel | Why |
|---|---|
| Galaxy view grouped by region, planets colored by `imbalance_score` | The headline visual |
| Planet detail: three-channel chart with baseline bands, description, force history | Where enrichment pays off |
| Signature breakdown of active and historical incidents | Makes classification visible |
| Source health — mode, last scan, buffer depth, scan completeness | The edge story |
| **Ingest lag histogram** | Proves late-arriving handling. Do not cut this |
| Rejection rate over time | Spikes when faults inject |
| Incident feed with `is_report_sourced` badge | Shows the telemetry/sighting distinction |
| Deployment log: ETA countdown, `guardrail_overrides`, `decided_by` | Agent vs. human, side by side |

**Checkpoint:** trigger a signature end to end with the dashboard open. Watch the planet shift,
the incident appear with its signature, and the deployment log populate. Then deploy manually to a
different planet and confirm the constraint layer rejects an already-deployed Jedi. Record both as
a GIF.

---

## Phase 8 — Repo polish · 4–6h

1. `.gitattributes`. Verify the language bar shows Python and SQL after pushing.
2. Repo description, one dense line.
3. Topics: `data-engineering`, `etl`, `streaming`, `databricks`, `delta-lake`, `dbt`,
   `unity-catalog`, `auto-loader`, `medallion-architecture`, `iot`, `edge-computing`,
   `raspberry-pi`, `mqtt`, `llm-agent`, `pyspark`.
4. README: architecture diagram, dashboard GIF, quickstart.
5. `dbt docs generate` to GitHub Pages.
6. GitHub Actions: `dbt build --target local`, `make test-agent`, `make test-intake` on every PR.
7. Clean-clone test of `make demo`.

**Checkpoint:** clone into a fresh directory, run `make demo`, reach a working dashboard without
editing a file.

---

## Ordering

- **Phases 0–4 are strictly sequential.** Phase 1 especially — the enrichment parameters are
  upstream of everything.
- Phases 5 and 6 can run in parallel with each other; they share only gold tables, frozen after 4.
- Phase 7 depends on both if you want the full dashboard, but the read-only panels only need 4.
- Do not start Phase 8 early.

## The showable milestone

The core data engineering claim is complete at the end of **Phase 4** — roughly 40–52 hours in.
Late-arriving data, quarantine, partial readings, medallion architecture, rolling baselines,
deterministic classification, dbt tests. All demonstrable.

Phases 5–7 are differentiators, not foundations. If the project stalls, Phase 4 plus a minimal
dashboard is a complete and defensible portfolio piece. The agent is the memorable part; it is not
the part that proves you can do data engineering.

## Estimate

**66–100 focused hours.** At 6h/week, about three to four months. At 12–15h/week, six to eight
weeks.

Claude Code compresses authoring, not verification — and every phase gate above is a verification
step. Budget for checkpoint debugging to dominate.

Two specific risks: Phase 3 hardware has the highest variance in the plan, and Phase 4's
dual-target `extract_payload` macro is the fiddliest thing in the design. If the macro fights you,
ship the Databricks target and add Postgres in Phase 8.

One deliberate slowdown: you want to learn dbt. Write the first three silver models yourself
before letting Claude Code accelerate. Generating them all saves maybe eight hours and forfeits
the objective.

## Commit discipline

Commit at every checkpoint minimum. Incremental commits across weeks read as sustained
engineering; three large commits read as a dump. Given the recruiting purpose, the commit history
is itself an artifact.
