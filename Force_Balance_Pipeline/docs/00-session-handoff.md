# 00 — Session handoff

Summary of the design session that produced docs 01–08. Read this first, then
`07-implementation-plan.md`. The other docs are the detailed spec; this file is the index of
what was decided and why.

---

## Project identity

- **Repo:** `Force_Balance_Pipeline` (Snake_Case — portfolio repos follow their primary
  language's convention). Internals stay lowercase: Python modules per PEP 8, dbt project name,
  Unity Catalog catalog `force`.
- **Replaces** the earlier Force Resonance Detection Network (FRDN) project. Key difference: no
  LLM on the Raspberry Pi 3 — 1 GB RAM, no accelerator, and it would couple sensor reliability
  to the heaviest workload. All inference runs in the cloud.
- **Purpose:** data engineering portfolio piece, visible to recruiter tooling (SeekOut etc.).
  Data engineering is the headline; the Pi is the interesting *source*, not the point. Not
  branded as IoT.

## Stack decisions

| Area | Decision |
|---|---|
| Platform | Databricks Free Edition |
| Ingestion | Bridge writes NDJSON to a UC volume via Files API; Auto Loader with `trigger(availableNow=True)` — same streaming semantics, one-line change to continuous |
| Transform | dbt Core + `dbt-databricks` (pinned ≥1.6), run as native `dbt` job task pulled from Git |
| Serving | Postgres on the GCP e2-micro; dashboard never queries the warehouse |
| Bridge | Mosquitto + Python on the e2-micro; also hosts the report inference endpoint |
| Agent | Cloud Run job, Gemini (`gemini-3.1-flash-lite`), function calling; reads/writes via SQL Statement Execution API |
| Dashboard | Node/React, hosted outside Databricks |
| Local demo | Docker Compose, Postgres standing in for the lakehouse, `make demo` |

## Verified in this session

- **Outbound egress works.** LinkedIn verification is active; `swapi.info` and `api.groq.com`
  both return 200 from serverless compute.
- **Every outbound HTTP client needs an explicit User-Agent.** Default `Python-urllib` gets
  Cloudflare `403` / `error code: 1010`, which looks like blocked egress but isn't.
- `pypi.org` is allowlisted — never use it alone to test egress.

## Still unverified — Phase 0

1. Does Free Edition support the `dbt` job task type? (Fallback: Python task shelling out to dbt.)
2. Are UC external locations to GCS permitted? (If yes, bridge can write to GCS directly.)
3. Is `streaming_table` materialization stable in the pinned adapter? (If yes, Auto Loader can
   move into the dbt DAG.)

## Probe (RPi 3)

- 64-bit Raspberry Pi OS Lite; the only dependency is `paho-mqtt` (ULIDs come from the seedable
  implementation in `forcesim`).
- Sweeps **all 60 planets every 15 minutes**, emitted as one burst per `scan_id` → one file per scan.
- Channels: **midichlorian density** (stable), **kyber resonance** (variable), **dark side
  activity** (quiet, rare spikes). Mean-reverting random walk seeded from enrichment parameters.
- Modes: **CONNECTED** (normal + 2–3% fault injection), **DISCONNECTED** (buffer to SQLite),
  **BURST** (post-reconnect drain), **STEALTH** (limited power/connectivity — hourly, dark
  side channel only).
- Control topic injects a named signature on demand for demos.

## Detection and classification — all deterministic SQL

- **Rolling 90-day baseline** per planet per channel, rebuilt daily, **probe-only**.
- Cold start solved by a **90-day synthetic backfill** with texture: drift, a few resolved
  historical emergencies, one slow-rising planet, some DISCONNECTED/BURST gaps.
- **Composite deviation score:** weighted Euclidean over signed z-scores, dark side ×2, scaled
  by `SQRT(3/channels_present)` for partial readings. Signed z-scores kept as columns.
- **Thresholds:** anomaly 4.0 (provisional), emergency 5.75, tuned on the backfill. Doc 03 has the basis.
- **Signatures** (doc 03): sith_presence, dark_adept, nexus_awakening, force_drain,
  kyber_cache, civil_unrest, veiled_presence, unclassified — each mapped to a Jedi specialty.
- Grain is one row per planet per scan; no windowing.

## AI enrichment layer (doc 08)

- LLM generates planet baselines/sigmas/spike probability, system, region, description, Force
  history; and Jedi rank/specialties/power/form.
- **Build-time and frozen:** `gemini-3.1-flash-lite` at default temperature 1.0, human-reviewed,
  committed as seed CSVs with a provenance file. Reruns do not reproduce committed values.
  Regeneration is a migration event — it invalidates the backfill and baselines.
- Model knowledge only — **no Wookieepedia scraping** (CC BY-SA, ToS).
- Jedi roster: **17 Jedi Order members in Episodes I-III who appear in SWAPI (Anakin included), no
  invented Jedi.** At least three per specialty.
- Expect at least one regeneration: models cluster numeric output mid-range.

## Web intake

- User enters a planet and free-text description; a model infers the three channels and a
  **relevance score**. Server-side only; planet baseline passed into the prompt for scale;
  outputs clamped in code.
- **Permissive:** relevance ≥ 0.7 plus score over threshold creates an emergency on its own,
  tagged `is_report_sourced`. A report during an existing incident's cooldown escalates it.
- **Inferred values never enter the baseline.** Enforced by filter and dbt test — the most
  important test in the project.
- Own fixture suite, including prompt injection.

## Yoda agent and deployments

- Invoked per incident only, never per event. Receives an already-classified signature.
- Tools: context, available Jedi, available ships, deploy (multi-Jedi), stand_down.
- **Constraint layer in code, shared with user-initiated deployments;** `decided_by` is the
  only difference. Includes specialty matching, rank floor for severe incidents, capacity limits.
- `guardrail_overrides` and `context_snapshot` recorded for audit; ETA computed in code.
- ~24 fixtures, assert on decision class and constraint compliance, 3 runs each.

## Rejected alternatives

| Rejected | Reason |
|---|---|
| BigQuery | Deferred to a separate future project covering streaming and Terraform |
| Snowflake | Trial expires; bad for a portfolio piece left running |
| Lakeflow declarative pipelines | dbt is a learning goal and keeps models portable |
| Continuous streaming | Burns Free Edition quota |
| Databricks Apps for the dashboard | Auto-stops after 24 hours |
| LLM on the Pi 3 | Memory, throughput, and reliability coupling |
| Light/dark ratio imbalance index | Replaced by composite score across three channels |
| One-minute windows | Meaningless at one reading per planet per 15 minutes |
| DEGRADED / HYPERSPACE modes | Superseded by the four current modes; fault injection covers quarantine |
| Scraping Wookieepedia | Licensing and ToS |
| IoT in the repo name | Mis-sets expectations; points at the wrong search |

**To do:** add "on-device inference" as a formal rejected-alternative decision in doc 01.

## Working guidance

- **Models in Claude Code:** Sonnet by default. Opus for Phase 4 (STEALTH-vs-fault validation,
  incremental recompute for replayed data, the dual-target `extract_payload` macro) and for the
  agent's constraint layer. Fable not needed. Get logs before escalating models on hardware issues.
- **Learning dbt:** write the first three silver models by hand, using Claude as reviewer, then
  accelerate.
- **Estimate:** 66–100 focused hours. Core data engineering claim complete at end of Phase 4
  (~40–52h). Checkpoint verification dominates, not authoring.
- **Commit at every checkpoint.** The history is part of the portfolio artifact.

## Next action

Phase 0 in doc 07: confirm the `dbt` job task works from Git on Free Edition before anything else.
