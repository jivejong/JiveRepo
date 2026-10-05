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
2. `scripts/jedi_roster.py` — hand-maintained list of the 17 Jedi Order (Episodes I-III) SWAPI
   URLs (ids in doc 08).
3. `scripts/enrich_planets.py` and `scripts/enrich_jedi.py` with `gemini-3.1-flash-lite` at its
   default temperature of 1.0 (Google advises against lowering it for Gemini 3). Reruns do not
   reproduce the committed values; the reviewed CSVs are the reproducible artifact.
4. **Review the output.** Work the checklist in doc 08. Expect to regenerate at least once —
   models regress to the mean on numeric tables and you will likely see baselines clustered in
   the middle of each range.
5. Verify specialty distribution across the Jedi roster. At least three Jedi per specialty, or
   certain signatures will have no valid responder.
6. Commit CSVs. `ENRICHMENT_PROVENANCE.md` is generated at promote time (doc 08); commit it too.
7. `dbt seed`.

**Checkpoint:** `dim_sector` has 60 rows with non-null baselines and sigmas, and each channel
passes the doc 08 review gate: at most 10% of sigmas clamped, at most 35% of baselines in the middle
0.40-0.60 of the range, the lowest baseline in the bottom quarter and the highest in the top quarter
of the range, and every anchor passing (Mustafar, Dathomir and Geonosis high and Naboo and Alderaan
low on dark; Coruscant high on midi; Utapau high on kyber, the giant kyber crystal from the Clone
Wars Utapau arc; Ilum is not in SWAPI). `dim_jedi` has 17 rows covering all four specialties with
at least three each.

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
   code, written directly as NDJSON files under the same ingest-time `dt=`/`hh=` prefix as live
   files (doc 02). The prefix names the day and hour a file was uploaded, not the date of the
   readings; event-time organisation belongs in silver.
   The window ends at the last 15-minute UTC boundary before the earliest live probe event already
   in bronze, not before generation time, so synthetic and live readings for `probe-01` never
   overlap. That boundary is an explicit input, recorded in the manifest, and the generator refuses
   to write any synthetic `event_time` at or after the earliest live `event_time`.

Give the backfill texture. Flat data makes a boring dashboard and unrealistic baselines:

- Gradual drift on 3–4 planets
- 3–4 injected historical emergencies with distinct signatures, fully resolved, in addition to
  ambient dark spike episodes (doc 04)
- One planet with a slowly rising dark side trend that has not yet crossed threshold
- A few `DISCONNECTED` gaps with later `BURST` recovery, so `is_replayed` has history

**Checkpoint:** run the live probe 45 minutes (3 scans), run Auto Loader, and see 180 rows in
`force.bronze.events` with correct partitions and `payload` queryable as `VARIANT`. Run Auto
Loader again with no new files — zero new rows, proving exactly-once. Backfill loads ~518,400
rows (60 planets × 96 scans/day × 90 days) and lands in a small number of `dt` partitions (the
upload days).

**Deviation, 2026-09-26:** the live checkpoint ran 3 scans at a 60 s cadence instead of 45
minutes. The real 15-minute cadence (quarter-hour alignment, an idle bridge between scans,
hour-boundary paths) is exercised in Phase 3.

**Closed, 2026-09-27:** the real 15-minute cadence ran on the desktop simulator (Phase 3, Step 3, not the
Pi) — 3 scans, 180 rows, quarter-hour aligned, the bridge idling between scans, the landing path crossing
an hour boundary. See PHASE3-RESULTS.md, "Desktop cadence run (closes the Phase 2 deviation)".

---

## Phase 3 — Four modes and hardware · 8–12h

1. Implement `DISCONNECTED`, `BURST`, `STEALTH`.
2. SQLite buffer with confirmed-publish deletion, 100k cap, overflow event.
3. Fault injection at 2–3% during `CONNECTED`, with local logging of what was injected.
4. Control topic for triggering a named signature on demand.
5. Deploy to the Pi 3 (64-bit Raspberry Pi OS Lite), systemd with `Restart=always`.

**Staging decision.** In Phase 3 the broker (Mosquitto in Docker) and the bridge (workspace mode) run on the developer's
desktop, and the Pi publishes to the broker over the LAN with a password and per-user topic permissions. TLS and the e2-micro
move together to a later phase; neither is part of Phase 3. The mode schedule (DISCONNECTED and STEALTH periods, doc 04) is off
until the checkpoint below passes, then on.

**Checkpoint:** force `DISCONNECTED` for 45 minutes, then restore. `BURST` drains the buffer, and
`bronze.events` contains the buffered scans with `event_time` spanning the outage and `_ingest_ts`
clustered at replay. No gaps, no duplicates. Separately, force `STEALTH` and confirm those rows
arrive with two null channels.

The cut is made on the Pi: a firewall rule drops its outbound TCP to the broker and a scheduled command removes it after 45
minutes, so a lost SSH session cannot leave it in place. `mode_transitions.jsonl` on the Pi must show the transition into
`DISCONNECTED` and the return through `BURST`; that file, not the clock, is the proof the cut happened. The checkpoint runs with
fault injection off (`--fault-rate 0`) so the outage rows are clean; fault injection gets its own period afterwards.

This checkpoint is the project's core claim. Do not proceed until it holds.

**Met, 2026-09-29.** See PHASE3-RESULTS.md, "Phase 3 checkpoint (2026-09-29): MET".

**Decided, 2026-09-28, not built yet:** an accidental Pi power loss showed a real gap — `mode_transitions.jsonl`'s `startup` entry
can be stamped before NTP sync corrects the clock (readings are guarded by `NTPSynchronized`; mode-transition writes weren't). Fix
chosen: defer those writes until the clock is confirmed synced once, then log one snapshot noting any transitions and a
`DISCONNECTED` (with reason and uptime) suppressed while unsynced. Lands after this checkpoint. See PHASE3-RESULTS.md, "Accidental
power-loss test".

---

## Phase 4 — Transformation, scoring, signatures · 14–22h

1. Silver: `probe_reading`, `force_report` (stub until Phase 5), `rejects`, `source_health`.
   Incremental merge on `event_id`. **Deviation:** Claude Code writes every Phase 4 model, including
   `stg_bronze_events`, `silver_probe_event` and `silver_source_health` — the three doc 00's working guidance
   originally set aside for Jong to write by hand. Jong learns from the code and `docs/ENGINEERING-LOG.md`
   instead. See doc 00, "Working guidance."
2. `extract_payload` macro with Databricks and Postgres branches.
3. **STEALTH-aware validation** — partial readings route to `probe_reading` with `is_partial`,
   not to rejects. This is the trickiest logic in the phase.
4. `gold.sector_baseline` — rolling 90-day, probe-only, rebuilt daily.
5. `gold.sector_reading` — signed z-scores, composite score, signature classification macro.
6. `gold.disturbance` — firing rules, sustained-scan requirement, 2-hour cooldown.
7. dbt tests from doc 03.
8. `docker-compose.yml` and `make demo` against the Postgres target.
9. **Deviation (this round):** build and schedule Job 1 (`force_pipeline`, doc 05 "Job topology"), once the gold
   models (steps 4-6) exist. Ingestion has been a manual notebook run for the rest of this phase (doc 05, "The
   one-time arrival-timestamp backfill"); the checkpoint below needs the ingest → transform → publish cycle
   actually running on its own schedule (hourly, Stage 4c; was 30 minutes (Stage 4b), then 15 — Free Edition
   quota, see `docs/ENGINEERING-LOG.md`), not a one-off `dbt build`.

**Checkpoint:**
- `dbt build` passes all tests on `prod`. **Deviation (Phase 4):** the `local` target and the `extract_payload` Postgres branch
  move to Phase 8 with the rest of the local stack (07:266-268's own fallback, taken) — `local` raises a compiler error rather
  than running untested SQL until then.
- **The baseline test passes:** `gold.sector_baseline` contains zero report-sourced data.
- Trigger `sith_presence` via the control topic. Within two scans a `gold.disturbance` row
  appears with the correct `signature` and `sustained_scans >= 2`. **Depends on Job 1's own scheduled rebuild
  actually running** (step 9, currently hourly — Stage 4c) — a manually-triggered `dbt build` does not
  exercise it.

  **Clarification, Phase 4 Stage 3d: "two scans" counts from the onset scan (the first scan actually at or above
  the emergency threshold), not from when the injection command is sent or a fixed ramp/hold boundary — not a
  deviation, since `sustained_scans >= 2` itself is unchanged; only the wall-clock expectation needed
  correcting.** `gold_disturbance.sql`'s own `detected_at` is `max(event_time)` of the run — the run's *last*
  qualifying scan as of whenever that build happened to run, not the onset — so how long after injection it
  appears depends on two independent things: where onset actually falls, and how promptly Job 1 rebuilds after
  it.

  **Where onset falls is not a fixed rule.** An earlier draft of this note assumed the `ramp` phase (2 scans by
  default) never clears the threshold and only `hold` does (doc 04:114's "injection holds at least 2 scans,
  because a disturbance needs 2 consecutive scans above the threshold" was read as implying this). A real C3 run
  (naboo, `sith_presence`, injected 2026-10-01 02:59Z, onset boundary `T` = 03:00Z) showed otherwise: `T`'s own
  scan (03:00Z) scored 3.981, under threshold, but `T+15` (03:15Z, still inside the default 2-scan `ramp`) already
  scored 6.729, over it — doc 04:73's own "ramp toward `baseline + (4 to 7) * sigma` over 2-4 scans" reaches full
  severity by ramp's last scan here, not only once `hold` begins. Onset is therefore found empirically, per
  injection and per sector's own baseline, not assumed from `ramp`/`hold` scan counts. Under prompt Job 1
  operation, expect the 2 consecutive qualifying scans (and so the `gold.disturbance` row) within 1-2 scans of
  onset — here, that would have been ~03:30Z, 30 minutes after `T`.

  **How promptly Job 1 rebuilds can dominate the latency.** The same real run's `detected_at` was actually
  `2026-10-01T04:15:01.650Z` — 75 minutes after `T`, not ~30 — because Job 1's 03:18Z and 03:33Z runs both failed
  (`RESOURCE_EXHAUSTED`, doc 05 "Stage 3d: quota") and no build ran again until a manual catch-up well after the
  injection's `decay` phase had already ended. That delayed build saw the full, already-completed 5-scan run
  (03:15Z-04:15Z) at once and reported its last scan and `sustained_scans = 5`, not the 2 that would have shown
  under a healthy pipeline. This is real, demonstrated latency from an infrastructure outage, not a property of
  the detection logic itself — see `docs/ENGINEERING-LOG.md`, "Stage 3d," for the full build-timeline evidence.
- Trigger two disturbances in one sector 30 minutes apart. One incident row — cooldown works. **Resolved, 2026-09-30:**
  `gold.disturbance`'s cooldown now walks runs per sector with a recursive CTE, comparing each one against the last
  *accepted* incident rather than just the immediately preceding one (a `LAG()`-based bug that could wrongly suppress a
  third rapid run following a long suppressed second — see doc 03, `gold.disturbance`, "Resolved, 2026-09-30"). Rebuilt
  with `--full-refresh` against the real 90-day backfill: no change to the existing 49-row disturbance list, since no
  sector's incidents were ever close enough together for the old bug to have actually fired.
- Fault injection reconciles: rejects table count matches the injection log, with matching
  `reject_reason` values.
- `STEALTH` readings score correctly with `channels_present = 1` and are not rejected.
- Replay a `DISCONNECTED` buffer. Affected historical rows are recomputed, not duplicated.

**Stage 4a status:** `cooldown_conflict` "definition B" done (doc 03, `gold.disturbance`). dev/prod schema isolation
fixed (`generate_schema_name.sql` now isolates only `target=dev`; see `docs/ENGINEERING-LOG.md`, "Stage 4a"). `detected_at`
semantics decided: current behavior (the run's latest qualifying scan, advancing on each rebuild) documented as
intended, not a defect; a fixed `confirmed_at` is deferred to Phase 6.

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
3. Gemini wiring with function calling.
4. ~24 fixtures from doc 06.
5. `make test-agent`, 3 runs per fixture.
6. Cloud Run job, scheduled hourly offset 4 (Stage 4c; follows Job 1's own cadence, was 30 minutes (Stage 4b),
   then 15 minutes).

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

Every phase ends with an entry in `docs/ENGINEERING-LOG.md` (goal, decisions and why, issues with
cause/fix/how found, checkpoint result, commit SHAs), committed after that phase's own results
commit — the results files are the evidence; this file is the narrative connecting them. A session
handoff is also produced or refreshed at each phase close, kept **outside the repository**
(`~/.force_balance_pipeline/handoffs/`, gitignored) — a pointer document with real machine
details, not a committed artifact.
