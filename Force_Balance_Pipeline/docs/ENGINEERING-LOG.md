# Engineering log

Docs 00-08 are the design: what was decided, once, and why. The `PHASE*-RESULTS.md` files are the checkpoint
evidence: numbers recorded from command output, nothing inferred. This file is the narrative connecting them —
what happened, in the order it happened, including the parts a design doc and a results table don't capture:
why a decision went one way, what broke, how it was found, and what it cost. Dates below are the developer's own
(US Eastern, matching commit timestamps); timestamps *inside* a phase (log lines, event times) are UTC with a
`Z` suffix, the project's own convention (doc 02). Anything below computed or inferred rather than read directly
off a log or a query result is labelled **derived**.

## Stack at a glance

| Area | Decision |
|---|---|
| Platform | Databricks Free Edition |
| Ingestion | Bridge writes NDJSON to a UC volume via the Files API; Auto Loader (`trigger(availableNow=True)`) |
| Transform | dbt Core + `dbt-databricks`, run as a native `dbt` job task pulled from Git |
| Serving | Postgres on a GCP e2-micro (not yet built); dashboard never queries the warehouse directly |
| Bridge | Mosquitto + Python; on the developer's desktop for Phase 3, moves to the e2-micro with TLS later |
| Enrichment | `gemini-3.1-flash-lite`, default temperature (1.0), build-time only, human-reviewed, frozen CSVs |
| Agent (Phase 6, not yet built) | Cloud Run job, same Gemini model, function calling |
| Probe hardware | Raspberry Pi 3, 64-bit Raspberry Pi OS Lite, Ethernet, no RTC |
| Probe software | `paho-mqtt` 2.1.x, stdlib `sqlite3`, systemd, no LLM on-device |

## Phase 0 — De-risk the platform (2026-09-23)

**Goal** (doc 07): prove a `dbt` job task sourced from Git materializes a table on Free Edition serverless
before building anything real on top of it.

**Decisions and why:**
- Q1 (dbt job task from Git): **yes**, confirmed. Q2 (Unity Catalog external location to GCS): **no** — the
  Create Credential dialog offers only AWS IAM Role and Cloudflare API Token, no Google Cloud option (host
  cloud is AWS) — so the bridge keeps writing through the Files API rather than direct-to-GCS. Q3
  (`streaming_table` materialization): **yes, with a stability caveat** — kept Auto Loader as its own notebook
  task rather than moving it into the dbt DAG yet, since a "yes" on Q3 alone wasn't reason enough to add that
  risk before Phase 4.

**Issues:**
- Two early Q1 runs failed with `INTERNAL_ERROR` (`No such file or directory` for the pinned commit's project
  folder). Cause: the dbt job task pulls from GitHub, not the local checkout, and local `main` was ahead of
  origin at the time. **Operational lesson, not a code bug**: push before running `q1` or `q3`.

**Checkpoint: MET.** Run `1074650816698552`, commit `5cdcfc4`, `dbt=1.10.13` / `databricks=1.9.8`, `PASS=9
TOTAL=9`, 89.7 s. Locally, a `dbt ls` toggle (`enable_streaming_check`, default off) confirmed the streaming
model is disabled by default (2 models) and appears only with the var (3 models); Windows PowerShell 5.1 strips
inner double quotes, so `--vars` needs YAML flow syntax (`--vars "{enable_streaming_check: true}"`), not JSON.

**Commits:** `93a6ed7`..`a3ba8f2` (repo scaffold through Phase 0 results), 2026-09-23.

## Phase 1 — AI enrichment and dimensions (2026-09-24 to 2026-09-25)

**Goal** (doc 07): `dim_sector` (60 rows) and `dim_jedi` (17 rows), each passing doc 08's review gate, committed
and frozen before anything downstream depends on them.

**Decisions and why:**
- Froze a SWAPI snapshot first (`data/swapi_snapshot/`): 60 planets, 82 people, 37 species, 36 starships,
  written only once all four resources validate.
- Switched the enrichment (and later, the agent's) inference provider from Groq to Gemini
  (`gemini-3.1-flash-lite`) partway through — stack table, agent config, `.env.example` and the egress test
  target all updated together.
- Enrichment runs at the model's **default temperature, 1.0**, not a lowered one — Google's own Gemini 3
  guidance advises against lowering it. Reproducibility comes from the frozen, human-reviewed CSVs, not from a
  low temperature; a rerun does not reproduce committed values by design.
- Jedi roster fixed at exactly the 17 Episode I-III members who appear in SWAPI (Anakin included, SWAPI's own
  "Ayla Secura" spelling kept) — no invented Jedi.
- Ilum, the doc 08 draft's original kyber anchor, isn't in SWAPI, so Utapau (the Clone Wars giant-kyber-crystal
  arc) replaced it. Anchors are decided ahead of time and never named inside an enrichment prompt, so the model
  can't just satisfy the anchor check by pattern-matching the prompt.
- SWAPI's `planets/28` ("unknown") becomes `sector_id` `"uncharted"` with an `is_unknown` flag and median
  baselines — and is explicitly excluded from ever being a valid fault-injection sector, so an injected
  "unknown sector" fault can never collide with a real one.
- Gemini's structured-output shape had to be found by probing: `generationConfig.responseMimeType` +
  `responseJsonSchema` is accepted; `responseFormat` is rejected with a named-field error; `thinkingConfig
  .thinkingLevel` is accepted. Thinking tokens count against the same `maxOutputTokens` budget as the response,
  so the caps are generous — 65,536 (planets, all 59 in one call) and 16,384 (Jedi).

**Issues:**
- `dim_sector`'s first review-gate pass **failed**. The model had regressed two anchors to the middle of their
  ranges: Geonosis (a Clone Wars battle site and a site of Imperial atrocity) sat at `dark_baseline=40`, tied
  with ordinary worlds; Utapau (the giant-kyber-crystal arc) sat at `kyber_baseline=25`. Fixed with **2 targeted
  human corrections** (Geonosis 40→55, Utapau 25→80, each with sigma recalculated to keep the same relative
  band) recorded in `data/enrichment_corrections.csv` and applied at promote time — not a full regeneration.
  The gate passed after. `dim_jedi` passed its gate on the first try, 0 corrections.
- A separate, minor issue in the same pipeline: dbt parses every `.md` under `seeds/` as a docs file and runs
  Jinja block extraction on it, so an unbalanced `{%`/`{#`/`{{` in a free-text correction reason would have
  broken every dbt command. The provenance generator neutralises those tokens before writing.

**Checkpoint: MET.** `dim_sector.csv`: 60 rows, review gate PASS after 2 corrections (prompt `planets-v1` /
`0ab79f1844ca`), promoted 2026-09-25T03:27:47Z. `dim_jedi.csv`: 17 rows, review gate PASS, 0 corrections
(prompt `jedi-v2` / `93521a8f269a`), at least 3 per specialty confirmed — combat 6, diplomacy 4,
investigation 4, stealth 3 — promoted 2026-09-25T12:54:32Z. (Obi-Wan Kenobi came back rank `master` rather
than `council_member`; kept as a flavor field, not a gate criterion.)

**Commits:** `3b4f41b`..`6bf0816`, 2026-09-24 to 2026-09-25.

## Phase 2 — Ingestion path and 90-day backfill (2026-09-25 to 2026-09-27)

**Goal** (doc 07): a live probe → bridge → Auto Loader path proven exactly-once, plus a 90-day synthetic
backfill with texture (drift, resolved historical emergencies, a rising trend, outage gaps).

**Decisions and why:**
- `forcesim`'s walk is a calibrated mean-reverting AR(1) per planet per channel, stationary SD equal to the
  enrichment sigma, stdlib-only (a hand-written Box-Muller over `random.random()`, so draws are stable across
  Python versions) — this is the *same* generator code the live probe and the backfill both use, so the
  backfill is not a separate, drifting implementation.
- Injection targets are **computed**, not hard-coded: the smallest whole-sigma point that classifies as its
  signature and clears the emergency threshold by a safety margin (`M = 1/(1 - 3e)`, `e` = 1.9% window-SD
  sampling error — **derived**, not itself a doc 03 number). Retuning doc 03's threshold recomputes every target
  automatically instead of needing hand edits.
- The backfill window ends at the last 15-minute boundary before the earliest *live* event already in bronze,
  not at generation time, enforced in code (`OverlapGuard`) rather than by convention, so synthetic and live
  `probe-01` data can never overlap even if the backfill is regenerated later.
- Thresholds set from the 90-day analysis (2026-09-26, by the developer): emergency **5.75** (≤1 false
  sustained incident/week galaxy-wide on noise alone), anomaly **4.0** (provisional, ~1% of scans, pending a
  Phase 6 definition of "active anomaly").

**Issues:**
- The documented AR(1) walk was **1.9x too noisy**: `x_t = PHI*x_{t-1} + normal(0, sigma)` has a stationary SD
  of `sigma/sqrt(1-PHI^2) ≈ 1.898*sigma` at `PHI=0.85`, not `sigma` — every z-score would have read 1.9x too
  small. Fixed by scaling the step noise itself (`step_sd = sigma * sqrt(K*(2-K))`, `K = 1-PHI ≈ 0.5268*sigma`)
  and starting each series from a stationary draw rather than 0, with a regression test pinning the old,
  wrong form as a failure.
- `dark_spike_probability` was read **per scan** instead of per day, which at 96 scans/day would have produced
  roughly 5,000 ambient spike episodes over 90 days instead of the intended handful — contradicting doc 04's
  "quiet with rare spikes." Fixed by dividing the configured per-day rate by scans/day before each roll.
- Signature classification near the threshold boundary only succeeded about 58% of the time against walk
  noise. Fixed by zeroing the step noise on exactly the channels that define a signature's region for the
  duration of an injected hold, so an injection reliably lands where it's supposed to.
- **Deviation (accepted, not a bug):** the live checkpoint ran 3 scans at a 60 s cadence instead of the
  documented 45 minutes at the real 15-minute cadence. Accepted 2026-09-26; **closed 2026-09-27** by a dedicated
  desktop cadence run (3 scans, 180 rows, real cadence, an hour boundary crossed) — filed in commit `95eba37`,
  which lands chronologically after Phase 3's first commits below, since the closing run happened alongside
  early Phase 3 work rather than before it started.
- The Auto Loader notebook's `payload` line failed on its first real run: `to_json(payload)` given a `STRING`
  input (`DATATYPE_MISMATCH`), because `inferColumnTypes=false` delivers `payload` as a raw JSON string, not a
  struct. Fixed by switching to `try_parse_json(payload)`, which also means a malformed payload becomes `NULL`
  in bronze instead of failing the whole stream.
- The full backfill's landing verification hit one dropped network read (`RemoteDisconnected`) with no retry
  logic in the verifier — likely a VPN toggle mid-run (owner-reported, not confirmed from the output). Fixed
  with a 3-attempt retry on a network error or HTTP 429/5xx (never on a denied read, which is a real refusal,
  not a transient failure); the rerun verified clean.

**Checkpoint: MET.** Live: 180 rows, 3 scans, 60 sectors, `payload` queryable as `VARIANT`, a no-op rerun adding
0 new rows (exactly-once). Backfill: 8,640 files, 518,400 rows, content hash
`8308472c95e55e6360ec8168f61f6ffb56bd8abb2b9d1dd9e67ecdd9505ffc62`; all 4 injected emergencies fired at their
designed thresholds; 9 noise-only sustained runs across the 90 days (0.70/week, under the false-alarm target).

**Commits:** `5e046f8`..`43246ff`, 2026-09-25 to 2026-09-26 (closing commit `95eba37`, 2026-09-27).

## Phase 3 — Four modes and hardware (2026-09-27 to 2026-09-29)

**Goal** (doc 07), plus a staging decision: force `DISCONNECTED` for 45 minutes on real Pi hardware and prove
the buffered scans land correctly on restore; broker and bridge run on the desktop for this phase, the Pi
publishes over the LAN, TLS and the e2-micro move to a later phase.

**Decisions and why:**
- The LAN broker requires a password and per-user ACLs even for desktop-only staging (`probe-01`,
  `force-bridge`, `operator`, each scoped to only the topics it needs).
- The 45-minute checkpoint cut is made **on the Pi** (an `nftables` rule dropping outbound TCP to the broker,
  removed by a command scheduled *before* the cut is applied), not by toggling the desktop firewall — disabling
  a Windows allow rule might not kill an already-established TCP session, so it wouldn't prove anything.
- The SQLite buffer is write-ahead (every reading written before it is published) with PUBACK-gated deletion, so
  a crash between a sweep and its publish loses nothing — durability, not just at-least-once delivery.
- A scan due before NTP sync is skipped and logged, never stamped with a guessed `event_time` — a gap by design
  is safer than a wrong timestamp that looks right.

**Issues** (several found live on real hardware, not in offline tests):
1. Docker Desktop's own firewall rules silently widened the broker's exposure past the intended Pi-only rule —
   Windows combines Allow rules rather than preferring the most specific one. Found by a third-device TCP probe
   (`Test-NetConnection` should have failed and didn't); fixed by disabling Docker Desktop Backend's two Private
   Allow rules, leaving the Pi-only rule as the effective one.
2. `--fault-rate 0` was missing from the *first* committed unit file's `ExecStart` — the service would have
   started fault injection at the full default rate (1.0) the instant it was enabled. Fixed before it ever ran.
3. `/opt` is root-owned; a plain `git clone` into a not-yet-created `/opt/force-probe` as an unprivileged user
   fails. Fixed by adding the same `mkdir`/`chown` `deploy.sh` already did to the manual-clone instructions.
   Separately, `deploy.sh` itself wasn't executable in Git (committed from Windows, mode `100644`); fixed as
   its own git mode change to `100755` (commit `ca5dae0`).
4. A lock-order-inversion deadlock between paho-mqtt's own internal mutex and the probe's own lock — introduced
   by an *earlier* fix this same phase (closing a PUBACK-loss race by holding the probe's lock across the paho
   call), found via `py-spy` on the Pi mid-scan. Reproduced reliably at volume against a **real** broker; the
   offline fake client
   never triggered it, because it has no internal lock of its own to invert against — a reminder that a fake
   without the real dependency's concurrency behavior can hide a real concurrency bug. Fixed by moving all
   PUBACK bookkeeping onto a lock-free queue and never calling into paho while holding the probe's own lock.
5. A false `link_lost` was logged at startup: the runtime couldn't distinguish "never connected yet" from "was
   connected, now isn't." Proven by the broker's own log (one continuous connection, no disconnect anywhere near
   the false entry) — an independent second source was what settled it, not the probe's own log alone. Fixed
   with an explicit "waiting for the first connect" state that a forced `DISCONNECTED` still overrides.
6. The *last* batch of any drain never logged its "acked" completion line — a one-tick race between the mode
   controller's own backlog-zero check (which can leave `BURST` the instant the backlog hits 0) and the drain's
   own settlement check running one tick later. Fixed by settling the drain unconditionally, every tick.
7. Once bug 5 was fixed, a boot with **no broker at all** (not just a slow one) would never have declared itself
   offline, since every "not connected" tick before the first connect was now suppressed. Closed with a real
   detection window: past the runtime's own `ack_timeout` (30 s), it's `DISCONNECTED` with reason
   `no_initial_connect`.
8. Bronze's `_ingest_ts` is notebook run time, not landing time — so the checkpoint's own "clustered at replay"
   claim would be unprovable if the notebook happened to run late (it did: `15:36:50Z`, well after the actual
   replay at `~15:02Z`). Added a second, notebook-independent proof (query `p3-3b`, plus a `LIST` fallback) from
   the landed files' own modification times, which is what actually carried the checkpoint.
9. `mode_transitions.jsonl`'s `startup` entry could be stamped *before* the boot it belonged to — a Pi has no
   RTC, and `fake-hwclock` hands the process a stale time before NTP corrects it. Found from an accidental real
   power loss: `uptime -s` (13:22:09Z) came *after* the logged `startup` time (13:19:43Z), which is impossible
   for the boot that produced it. Fixed (`5e9cb8f`, landed after the checkpoint) by deferring the mode log's own
   writes until the clock is confirmed synced — the same treatment doc 04 already gave readings.
10. Doc 05's original persistent-journal fix (`mkdir /var/log/journal`, relying on `Storage=auto`'s "persistent
    if the directory exists" rule) does not work on this Pi OS image, which ships its own `Storage=volatile`
    drop-in that overrides that rule outright. Found while chasing why bug 9's power-loss incident left no
    Pi-side journal to examine at all. Corrected 2026-09-30 with an overriding drop-in of the project's own.

**Checkpoint: MET, 2026-09-29.** Outage `14:19:05.866Z`–`15:01:57.431Z` (42m 52s); `bronze.events` held the 180
buffered rows spanning it, arrival clustered within 2 seconds of the replay — proven three independent ways
(the workspace SQL, the Pi's own journal, and the desktop bridge's own landing log); no gaps, no duplicates.
Separately, `STEALTH` rows arrived with both variable channels null and no temperature field.

Also that day: a real, **unplanned** outage (1h 15m 31s, 300 rows buffered and drained cleanly, before any
schedule was deployed). The break was on the Pi-to-desktop path specifically, not the desktop side — confirmed
from `docker inspect` (broker container never restarted), the Windows System event log (no adapter/DHCP/power
event), and the Mosquitto broker's own log, after an earlier reading of the bridge's own log as evidence of a
desktop-side drop was found to be wrong and corrected.

**Commits:** `25cfe92`, `1550bd4`, `142056d`, `95eba37`, `6cd8ded`, `ca5dae0`, `d7f0b96`, `b16f0b2`, `ddb6002`,
`a297e3f`, `5e9cb8f` — 11 commits, 2026-09-27 to 2026-09-29.

**Today (2026-09-29/30):** mode schedule turned on (`a297e3f`); the mode-log clock-sync fix landed (`5e9cb8f`)
and was deployed — new process startup `22:41:27Z`, faults ×0, mode schedule on; the old process it replaced had
run 36 scans, 2,160 published and acked, 0 overflows.

## Phase 4 — Transformation, scoring and signatures (2026-09-30, in progress)

**Goal** (doc 07): silver (`probe_reading`, `force_report` stub, `rejects`, `source_health`), the `extract_payload`
macro, gold (`sector_baseline`, `sector_reading`, `disturbance`), dbt tests, and the local demo stack. Started
2026-09-30 with the design questions raised before any model is written (docs 03, 04, 05, 07).

### Bronze arrival column

**Decision and why:** doc 03's "decide before silver is built" item (`_ingest_ts` measures notebook run time, not file
arrival) is resolved: a new bronze column, `_file_modified_ts`, captures `_metadata.file_modification_time` from Auto
Loader going forward, and `silver.probe_reading.ingest_lag_seconds` reads it (falling back to `_ingest_ts`) for live
rows. Existing live rows needed a one-time backfill, since the column didn't exist when they landed.

**Issue:** the first backfill attempt matched nothing — dry run `rows_to_update` 0, `files_matched` 0; the real
`MERGE`'s `num_affected_rows` 0. **Derived** cause: `read_files()`'s `_metadata.file_path` comes back as
`dbfs:/Volumes/force/raw/telemetry/dt=.../hh=.../<file>.ndjson`, while `bronze._source_file` (stamped by the streaming
notebook's own `_metadata.file_path`) has no `dbfs:` prefix — the same underlying Databricks metadata column, formatted
differently by the two ingestion paths (`cloudFiles` streaming vs. ad hoc `read_files()`). Fixed by stripping the
prefix from both sides of the join (`regexp_replace(path, '^dbfs:', '')`), a no-op on whichever side doesn't have it.

**Checkpoint (this piece):** corrected run — `still_null` read 9,493 right after the first attempt above (unchanged,
since it affected 0 rows), the figure the corrected dry run's `rows_to_update` needed to equal; that dry run's
`rows_to_update` and the corrected `MERGE`'s `num_affected_rows` were not captured. `still_null` after the corrected
`MERGE`: 0. Sanity: 12,073 live rows total (9,493 backfilled + 2,580
stamped directly by the updated notebook on its own next run, 9,493 + 2,580 = 12,073); max lag 9,512 s. Every
`_file_modified_ts` is a whole second (Databricks Runtime rounds `_metadata.file_modification_time`), confirmed on both
populations (9,493 of 9,493 backfilled rows, 2,580 of 2,580 notebook-stamped rows).

**Phase 2 clock finding, from the same sanity pass:** 97 rows show a negative `ingest_lag_seconds`, minimum -1,950 ms,
every one of them in `date(event_time) = 2026-09-26` (180 rows that day) — confirmed from `docs/PHASE2-RESULTS.md`
(L1, L6) as the Phase 2 checkpoint's own desktop-simulator rows; that checkpoint ran entirely on the laptop (doc 07
Phase 2: "Laptop only. No Raspberry Pi yet."). Every later day has zero negative lags, minimum 1,050-2,050 ms. Cause:
those rows used `--assume-clock-synced` (Windows has no `timedatectl`), which assumes the clock is synced rather than
verifying it (doc 04); the desktop's clock ran roughly 2 s ahead of the volume's own clock while the simulator was
live. Combined with `_file_modified_ts`'s 1-second rounding, doc 03 now documents a `-2` second tolerance on
`ingest_lag_seconds` ("Lag precision") rather than treating any negative value as a data problem. The Pi's own rows,
clock-gated before every scan, have never gone negative.

### Job 1 timing

**Decision:** Job 1 (`force_pipeline`, doc 05 "Job topology") is built and scheduled in Phase 4, but only after the
gold models exist (doc 07, Phase 4 step 9) — not at the phase's start. Ingestion stays a manual notebook run through
the silver/gold scaffolding and build-out.

**Free Edition fit, researched before committing to the 15-minute cadence (web lookup, no workspace access):**
5 concurrent job tasks per account; Job 1's 3 tasks run sequentially within one job, which doesn't count against
that limit concurrently, so it uses at most 1 of 5 slots. The platform enforces only a 10-second minimum interval
between scheduled runs generally, far below 15 minutes. No daily/monthly serverless compute quota number is
published for Free Edition anywhere found — only that exceeding it shuts compute down for the rest of the day (or
month). Sources: [Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations),
[Serverless compute limitations](https://docs.databricks.com/aws/en/compute/serverless/limitations),
[Run jobs on a schedule](https://docs.databricks.com/aws/en/jobs/scheduled),
[Resource limits](https://docs.databricks.com/aws/en/resources/limits).

**Fallback if the undisclosed quota is hit:** drop the schedule to every 30 minutes (halves the daily run count;
the checkpoint's `sith_presence`-within-two-scans claim becomes "within one hour" instead of thirty minutes), or
restrict Job 1 to daytime hours only. The 15-minute cadence is needed continuously only for the checkpoint's own
test window (doc 07, Phase 4 checkpoint, C3) — not for every day the pipeline runs afterward.

### Stage 1: staging and silver

Six models, real SQL for the first time this phase (`stg_bronze_events`, `silver_probe_event`,
`silver_rejects`, `silver_probe_reading`, `silver_source_health`, `silver_force_report` stub) — written by
Claude Code, not hand-written as doc 00 originally planned (see "Claude writes all Phase 4 models," above).
Notes here are for a reader learning dbt from this code, not just a record of what happened.

**Dedup design: insert-only, earliest arrival wins.** Doc 03 says a duplicate `event_id` keeps its earliest
arrival, never gets overwritten by a slower duplicate landing later. dbt's incremental `merge` strategy
updates a matching row by default, which is the opposite of what's needed here, so every append-only silver
model uses the same two-part pattern instead of that default:

1. `row_number() over (partition by event_id order by arrival_ts asc)`, keep `rn = 1` — handles two copies of
   the same event_id landing in the *same* dbt run (Auto Loader can ingest an original and its replay in one
   notebook pass).
2. `WHERE event_id NOT IN (SELECT event_id FROM {{ this }})` inside `{% if is_incremental() %}` — handles a
   duplicate landing in a *later* run. Because the SELECT itself already excludes anything already in the
   table, the generated `MERGE`'s `WHEN MATCHED` branch is never reached for these models' own rows; it's an
   ordinary merge given a source that never contains an already-matched key, not a special "insert-only"
   dbt feature (dbt has no built-in switch for this — the exclusion in the query is what does it).

**`silver_probe_reading` as the complement of `silver_rejects`**, not a second copy of the validation CASE:
it `ref()`s `silver_rejects` and does `LEFT JOIN ... WHERE r.event_id IS NULL` — a candidate row that
validation didn't reject, reached here. The two models can't drift out of sync the way two independent
copies of the same CASE expression could; changing `silver_rejects`' rules changes `silver_probe_reading`'s
population automatically, with no logic duplicated. The cost: `silver_rejects` must build before
`silver_probe_reading` can (dbt's own dependency graph enforces this from the `ref()`, no separate ordering
step needed).

**The unit-test VARIANT finding, and how it was actually resolved.** `extract_payload`'s
`try_variant_get(payload, ...)` calls returned NULL for every field when first unit-tested, even for
genuinely valid JSON numbers. Cause: dbt-databricks 1.9.8's default unit-test fixture format renders a
VARIANT-typed column as a plain `CAST(<json text> AS VARIANT)`, which does not parse the JSON — it wraps the
literal text as a STRING-typed variant scalar, so every path lookup finds nothing. Two ways to fix this were
tried, in order:

1. Make the macro re-parse defensively: `try_variant_get(try_parse_json(cast(payload as string)), ...)`.
   Worked, confirmed harmless on real (already-parsed) bronze data too (`dbt show`, identical output either
   way) — but it added a cast-and-reparse round trip to every real read, for a problem that was really in the
   test fixtures, not the macro.
2. **What shipped instead:** unit test fixtures for `payload` use `format: sql` (a dbt unit-test option that
   takes a raw `SELECT` instead of the default column-by-column dict) with our own `parse_json(...)` call,
   producing a genuine object VARIANT. `extract_payload` stays the simple, one-line form; the fixtures, not
   the production macro, work around a fixture-rendering gap in this dbt-databricks version.

The general lesson, not just this bug: when a test and the code under test disagree, check which one is
wrong before "fixing" the code — the first, working fix here was fixing the wrong thing.

**`on_schema_change`: a silent no-op, not an error.** Adding `_rescued_data` to `silver_probe_reading`'s
SELECT and running dbt again did not fail — it also did not add the column. dbt-databricks's default,
`ignore`, means a schema change to an already-built incremental table is silently dropped until a
`--full-refresh` rebuilds it from scratch. `dbt_project.yml` now sets `on_schema_change: fail` project-wide
(harmless on the view and table models, which rebuild from scratch every run regardless), so the next schema
change surfaces as a build failure that names the missing full-refresh, not a test that mysteriously never
sees the new column.

**MSYS path conversion** (doc 05, dbt setup section, has the durable fix): Git Bash on this machine rewrote
`DATABRICKS_HTTP_PATH` into a Windows path before `dbt.exe` ever saw it, hanging every command with no error.
`MSYS_NO_PATHCONV=1` fixed it for this session.

**Verified counts, staging + silver, against Databricks (`--target prod`, `--select staging silver`), two
consecutive `dbt build` runs, identical both times:** `stg_bronze_events` 530,473; `silver_probe_event` 0;
`silver_rejects` 0 (fault injection is still off, doc 04 — confirmed independently, straight against bronze,
that there are zero out-of-range or unknown-sector rows to reject yet); `silver_probe_reading` 530,460 (the
530,473 minus exactly the 13 known duplicate `event_id`s from the 2026-09-27 23:45Z scan, deduplicated away);
`silver_force_report` 0; `silver_source_health` 1. Five real-data checks against known fixtures — the
13-duplicate scan (60 rows, 13 not replayed / 47 replayed), the 2026-09-29 outage (300/300 buffered, 180/300
replayed), the Phase 3 checkpoint outage (60/180 replayed), the 2026-09-28 STEALTH scan (60/60 in
`probe_reading`, all `is_partial`, 0 in `silver_rejects`) — all matched exactly. The fifth, minimum
`ingest_lag_seconds` on 2026-09-26 (the Phase 2 desktop-clock day, doc 03 "Lag precision"), measured -1 s
against a documented tolerance of -2 s; **derived**, not a doc number: `unix_timestamp()` truncates each
timestamp to whole seconds before subtracting rather than rounding the true sub-second difference, which can
land a truly ~-1.95 s lag at -1 instead of -2. Still inside the documented `>= -2` tolerance, not a violation.

### Stage 2: gold

Three models, on top of Stage 1's silver: `gold_sector_baseline` (a plain `table`), `gold_sector_reading`
(`incremental`, `merge` on `event_id`), and `gold_disturbance` (`incremental`, `merge` on a deterministic
`disturbance_id`). Notes here are for a reader learning dbt from this code, not just a record of what happened.

`gold_sector_baseline` computes rolling 90-day mean/stddev per sector per channel, probe-only, anchored to
`computed_at` (a `current_timestamp()` captured once in a CTE so every row in one build shares the same
anchor — doc 03, "Window anchor"). It's a `table`, rebuilt whole each time, and built by hand this stage —
Job 1 excludes it, since it's meant to run daily, not every 15 minutes.

`gold_sector_reading` is the first model where `is_incremental()` branches the *entire* CTE chain, not just a
`WHERE` clause, because the first build has no `{{ this }}` to query yet. On later builds, the recompute
window is `LEAST(now - 48h, MIN(event_time) of silver rows arrived since gold's own last build)` — the normal
case just replays the trailing 48 hours, but a late-arriving replay pulls the window back far enough to
recompute that replay's own historical scan too. That "own last build" timestamp doesn't exist anywhere else
in the warehouse, so this model stamps a new bookkeeping column, `_gold_built_at`, the same way bronze stamps
`_ingest_ts`. Z-scores are `(reading - mean_90d) / NULLIF(stddev_90d, 0)` per channel against the baseline
pivoted long→wide; `imbalance_score` and `signature` come from macros (`imbalance_score.sql`,
`classify_signature.sql`), not inline SQL, so a separate Python-side parity test can check them against doc 03
independently.

`gold_disturbance` finds runs of consecutive above-threshold scans per sector with the classic "gaps and
islands" trick — two `ROW_NUMBER()`s, one over every row and one over only qualifying rows; where they stay in
lockstep, the difference is constant, and that's one run. `disturbance_id` comes from a new macro,
`deterministic_id`: 48 bits of the onset's `event_time` plus a hash of `(sector_id, onset_event_time)`, so
recomputing the same onset twice always produces the same id and merges as an update. (Originally spec'd at 80
hash bits; Databricks' `conv()` overflows past 64 bits internally, caught by a real build failure and fixed by
trimming to 64 — doc 03 has the full deviation note.) `agent_processed` is protected two ways:
`merge_exclude_columns='agent_processed'` on the incremental config, and a
`COALESCE(MAX(existing.agent_processed), false)` self-join in the SELECT — belt and suspenders, so a future
bug in one layer can't silently start overwriting an agent's work. (The self-join needs `MAX()`, not a bare
column, because Databricks requires every correlated scalar subquery to be aggregated — caught on the
*second* build, since the first build never takes the incremental branch at all; this is exactly why building
twice and comparing row counts is the checkpoint, not just building once.)

**Cost note, candidate for the Job 1 quota measurement:** `gold_disturbance` deliberately rescans *all* of
`gold_sector_reading` on every build, not just a recent window (doc 03's own "Known limitation" note has the
reasoning — a run can span an arbitrary number of scans, and doc 05's "simpler is safer" philosophy applies).
At the current data volume (530,460 `gold_sector_reading` rows and growing by 60 rows per 15-minute Job 1
cycle) this costs ~6 seconds per build; worth watching once Job 1 is running continuously, since this is the
one Stage 2 model whose cost grows with total history rather than with the 15-minute increment.

**Real-data checks, against Databricks (`--target prod --select gold`), two consecutive `dbt build` runs,
identical both times:** `gold_sector_baseline` 180 rows (60 sectors x 3 channels); `gold_sector_reading`
530,460 (matches `silver_probe_reading` exactly on a first build, 0 null scores or signatures);
`gold_disturbance` 49. All 40 build-time checks passed both runs (3 models, 21 data tests including
`assert_baseline_probe_only` and `assert_cooldown_respected`, 16 new unit tests).

Six real-data checks: baseline is probe-only (`assert_baseline_probe_only` passes; `sample_count = 8455`
uniformly across all 60 sectors, 97.9% of the 8,640-scan theoretical max — consistent with the manifest's
known gaps). The 4 injected backfill emergencies (tatooine `sith_presence`, dantooine `nexus_awakening`,
kamino `force_drain`, coruscant `civil_unrest`) all fire on their manifest days with `sustained_scans >= 2`
and scores matching the manifest's own recorded composites closely. mon_cala: last backfill day mean
`imbalance_score = 2.52` (< 4.0), 0 disturbances. The 2026-09-28 19:00Z `STEALTH` scan: 60 rows,
`channels_present = 1` on all of them, 0 null scores (found only after widening a literal timestamp-equality
filter to a 15-second window — the synthetic rows carry sub-second jitter, same pattern as the manifest's
`.950`-second timestamps). 49 total disturbances, 45 outside the 4 named emergencies, against doc 03's own
design-time prediction of 9 noise-only + 32 ambient-episode-driven = 41 — a reasonably close match, though the
45 can't be cleanly split into those two buckets from `gold_disturbance` alone (that label lives in
`edge/backfill_texture.json`'s per-scan episode data, not joined here).

**A genuine, doc-03-consistent-but-previously-undocumented finding:** Mustafar can exceed the 5.75 emergency
threshold even though its dark-side *spikes* are clamped at 100 (doc 04's "two planets cannot fire one") —
because a large enough *drop* in dark-side activity relative to its own (unusually high) baseline produces an
equally large `z_dark`, and the composite formula squares it regardless of sign. Five real scans do this; one
produced a genuine 2-scan `unclassified` disturbance (2026-09-22, `z_dark` around -4.16). Not a bug — the
doc's clamp guarantee was always specifically about the upward direction; doc 04 now says so explicitly.

### Stage 2 follow-up: the cooldown's `WITH RECURSIVE` assumption was never checked

`gold_disturbance`'s header comment originally asserted that Databricks SQL doesn't support `WITH RECURSIVE`,
as the reason its cooldown check uses `LAG()` (compare each run only to the immediately preceding one) instead
of a true "last accepted incident" walk. That assumption was never actually tested — it was corrected this
round.

**Finding, verified live against the project's own serverless SQL warehouse (DBSQL 2026.36), 2026-09-30:**
`WITH RECURSIVE` works, including the specific shape this fix needs — a sequential per-sector walk carrying a
running value (the last accepted incident's `detected_at`) forward row by row, not just simple counting
recursion. Source: two inline `dbt show` queries run directly against `prod`, not a doc lookup; the second
constructs the exact "carry a pointer forward per sector" pattern and confirms it executes correctly.

**The bug's actual direction is the opposite of what the original comment guessed.** A 3-run fixture (an
accepted run; a long suppressed run whose own `detected_at` lands well after the accepted run's; a third run
whose onset is clear of the *original* accepted incident's cooldown but still within the *suppressed* run's
own, later `detected_at` + cooldown) shows the current `LAG`-based check incorrectly **suppresses** that third
run, where a true last-accepted-incident walk correctly **accepts** it. The original comment guessed the
opposite (incorrect acceptance) without verifying — corrected in doc 03 and in the model's own header comment.

**Proposed rewrite (not applied this round):** replace the `LAG()`-based `with_cooldown`/`accepted` CTEs with
a recursive CTE that walks `runs` per sector in onset order, carrying a `last_accepted_detected_at` value
forward — updated only when a run is itself accepted, left unchanged (not reset to the intervening run's own
`detected_at`) when a run is suppressed. Sketch:

```sql
with recursive walked as (
    select *, detected_at as last_accepted_detected_at, true as accepted
    from runs where seq_in_sector = 1

    union all

    select r.*,
        case when r.onset_event_time < w.last_accepted_detected_at + interval 2 hours
             then w.last_accepted_detected_at else r.detected_at end,
        case when r.onset_event_time < w.last_accepted_detected_at + interval 2 hours
             then false else true end
    from walked w
    join runs r on r.sector_id = w.sector_id and r.seq_in_sector = w.seq_in_sector + 1
)
```

Needs a unit test for the three-rapid-runs case above (accepted, then a long suppressed run, then a run that
should be a new incident once clear of the *first* run's cooldown) before it ships — the existing
`unit_test_disturbance_cooldown_suppresses_a_second_onset_and_flags_the_first` fixture only covers the
two-run case and would not have caught this. Deferred, not applied, per this round's scope.

### Stage 3a: cooldown rewrite

Applied the sketch above almost as written. Two refinements over the sketch: a `runs_seq` CTE computes
`row_number() over (partition by sector_id order by onset_event_time)` on the already sustained_scans-filtered
candidate runs (small per sector — single digits over 90 days — so the recursive walk stays cheap and nowhere
near any recursion-depth limit), and the recursive term's suppression test is computed once in a nested
subquery rather than repeated across the `last_accepted_detected_at`/`accepted` expressions, to avoid two
copies of the same condition drifting apart. Both were checked directly against Databricks before writing the
real model — `WITH RECURSIVE` does allow a plain (non-recursive) CTE earlier in the same `WITH` list, and does
allow the recursive term's self-reference to sit inside a nested subquery, not just a bare `FROM`/`JOIN`.

Each row of the new `walked` CTE carries `basis_detected_at` — which accepted incident's `detected_at` a run
was actually tested against, NULL for a sector's first run (never suppressed). `cooldown_conflict` is now
`EXISTS (a later, suppressed run whose basis_detected_at equals this row's own detected_at)`, which stays
correct across a chain of several consecutive suppressed runs, since they all carry the same original
`basis_detected_at` forward unchanged — the single-pair version this replaced couldn't express that.

**Unit tests:** the three-rapid-runs fixture (`unit_test_disturbance_third_rapid_run_is_accepted`) — run 1
accepted, run 2 long and suppressed (8 consecutive scans, its own `detected_at` landing after run 1's cooldown
ends), run 3 clear of run 1's true cooldown but still inside what run 2's own `detected_at` would have implied
— passes: run 1 and run 3 both come through as separate incidents, run 1 flagged `cooldown_conflict`, run 3
not. The existing two-run cooldown fixture (doc 07 checkpoint C4's shape) and the single-run fixture both still
pass unchanged — 17/17 gold unit tests green.

**Build verification:** `dbt build --select gold_disturbance --full-refresh`, then twice more without it — all
three green (12/12 each run), including `assert_cooldown_respected`. Row count held at 49 across all three
builds. Diffed the full 49-row list against Stage 2's recorded list: **zero additions, zero removals, zero
changes** to any sector, `detected_at`, signature, or `sustained_scans` — byte-identical. Checked why the real
fix had no observable effect: the minimum gap between any two accepted incidents for the same sector anywhere
in the 90-day backfill is 47.5 hours (`kalee`), far beyond the 2-hour cooldown the old bug needed to matter.
The bug was real, is fixed, and is regression-tested — it simply never fired on this particular dataset.

### Stage 3a: Job 1

Defined Job 1 (`force_pipeline`) and Job 2 (`rebuild_baseline`) as a Databricks Asset Bundle — `databricks.yml` at
the repo root plus `resources/*.job.yml` — rather than a hand-applied Jobs API JSON file, because `databricks
bundle deploy` is idempotent (updates the same job in place) where `databricks jobs create` is not (a second call
makes a second job). Doc 05, "Deploying Job 1 and Job 2," has the full reasoning and the change procedure.
`publish_serving` (doc 05's third Job 1 task, writing to Postgres) is left out — it depends on the Postgres/local
demo stack, deferred to Phase 8.

**`environment_version` "5", resolved Stage 3b:** `databricks bundle validate` accepts either `"2"` or `"5"` as
syntactically valid, so Stage 3a couldn't settle which was actually correct for this workspace from validation
alone, and a flagged-not-resolved note was left pointing at the job's own run as weak, inconclusive evidence.
Real evidence already existed and should have been checked first: `docs/PHASE0-RESULTS.md:94-97` records that
Phase 0's job runs found serverless `environment_version` **defaults to `"5"`** (Python 3.12) — read directly
from `.phase0_state.json`'s saved run state (`q1`, run `846253185978169`, `"env_version": "5"`), not guessed, and
confirmed again by later Phase 0 runs reusing that same default. `"5"` is correct, independent of Stage 3a's own
run succeeding.

**First manual run, 2026-09-30, both jobs created PAUSED as instructed:** `force_pipeline` triggered once via
`databricks jobs run-now`. `ingest_bronze` (notebook) SUCCESS in 83.6s; `transform` (`dbt build --exclude
gold_sector_baseline`) SUCCESS in 187.9s; both tasks' `git_snapshot.used_commit` = `d923a7c8` — the last commit
on `main` at trigger time (this round's own changes, including this log entry, hadn't been pushed yet, so the
run correctly did not see them; git-based deployment working as designed, doc 05 "the repository is the source
of truth"). Row counts before → after: bronze 530,473 → 535,333, `silver_probe_reading` and `gold_sector_reading`
530,460 → 535,320 each — the identical +4,860 at every layer, meaning the real Pi had been publishing live scans
the whole time since the last ingestion (no rows lost or gained crossing any layer). `gold_disturbance` 49 → 51,
two new real incidents from that backlog. Ran `transform`'s own command locally a second time against the same
now-current data: zero row change at every layer across three consecutive runs, confirming doc 07's Phase 4
checkpoint property ("Affected historical rows are recomputed, not duplicated" extends naturally to "a rerun over
unchanged data changes nothing").

### Stage 3b: edge items, checkpoint SQL, loose ends

**Pause-state loose end.** `resources/*.job.yml` now says `pause_status: UNPAUSED`, matching the live state after
Jong unpaused both jobs by hand following the passing run on commit `19cdba2`. See doc 05, "Deploying Job 1 and
Job 2," for the warning this round added: `bundle deploy` reconciles the whole job definition on every deploy,
including `pause_status`, so a stale `PAUSED` left in the file would silently re-pause a job an operator had
already turned on — an earlier draft of that doc section claimed deploying never touches pause state, which was
never actually tested and is wrong whenever the file sets `pause_status` explicitly, as this project's does.

**The 2 new live disturbances (49 → 51, first seen in Stage 3a):**

| sector | detected_at | signature | sustained_scans | peak score | scan mode |
|---|---|---|---|---|---|
| `umbara` | 2026-09-30 06:45Z | `unclassified` | 4 | 6.757 | `CONNECTED` (all 4 scans, 06:00–06:45Z) |
| `mustafar` | 2026-09-30 19:45Z | `unclassified` | 3 | 6.342 | `CONNECTED` (all 3 scans, 19:15–19:45Z) |

Checked directly against `silver_probe_reading.mode` for every scan in both runs (not inferred from the schedule):
**neither overlaps a `DISCONNECTED` window or a `STEALTH` hour** — every one of the 7 underlying scans across both
incidents reads `CONNECTED`. Both are real, ordinary ambient anomalies on live data, same shape as the backfill's
own ambient episodes (doc 03's noise-driven, non-injected incidents) — unremarkable, and reported here only
because they're genuinely new since Stage 2's 49-row snapshot, not because anything about them needed explaining.

**Quota watch:** `ingest/job1_quota_watch.sql` — two read-only queries against `system.lakeflow.job_run_timeline`
and `job_task_run_timeline` (Unity Catalog system tables, not a Jobs API script, so it's a plain `.sql` file like
every other checkpoint in this project), reporting per day: run count and outcome breakdown, and total task
execution time. **Derived baseline** (Stage 3a's one manual run): `ingest_bronze` 83.6s + `transform` 187.9s =
271.5s, **~4.5 minutes per 15-minute cycle**. **Caveat found while testing the queries:** `system.lakeflow`'s own
`run_duration_seconds` still read 0 for a run already confirmed `SUCCEEDED` — these system tables lag real time
by some margin Databricks doesn't commit to a number for; don't read a day's numbers as final same-day. **Early
live signal, not yet the baseline:** the job's first 3 real scheduled-cadence runs today average ~5.96
minutes/run of total task time (1,072s / 3), noticeably above the single-manual-run baseline above — plausibly
cold-start overhead on an automatically-provisioned serverless environment versus an already-warm manually
triggered one, or more backlog per run; three data points, not conclusive, exactly what the measurement week is
for.

### Stage 3b deploy and proofs

Jong's own maintenance window, 2026-09-30/10-01 (times as pasted; the Pi's journal shows EDT, everything else UTC
— EDT is UTC-4, so a derived UTC value is labelled as such).

**ACL fix, confirmed deployed and working.** `force-mosquitto-secrets` holds both `acl` (775 bytes) and `passwd`,
owned `mosquitto:mosquitto`, mode `-rw-------` — the volume-copy treatment applied correctly to both files. The
broker restarted (01:16:36Z) with no ACL group-ownership warning in its log — the fix this round made.

**Broker persistence, proven end to end.** The same restart's log shows `Restored 60 base messages`, `Restored 1
clients`, `Restored 1 subscriptions`, `Restored 60 client messages` — Mosquitto's own startup log confirming it
reloaded the bridge's queued session and its 60 queued QoS 1 messages from disk, not a cold start. The bridge
then reconnected (`connected (session present: True, reason: Success, 2026-10-01T01:17:17.213018Z)` — this
round's own `make_on_connect` logging, working exactly as built) and received all 60: one landed file,
`dt=2026-10-01/hh=01`, 60 lines, 23,977 bytes, `dead_lettered=0 retries=0 failed_batches=0`. Nothing was lost
across the broker's own restart. **The "two persistence proofs still owed" runbook item (doc 05) is closed —
both proofs passed.**

**Persistent-journal fix, proven by a stronger event than planned.** The Pi was accidentally unplugged during the
same window — a real, uncontrolled power loss, not the planned graceful `sudo reboot`. The previous boot's last
entry was 21:00:33 EDT, right after the 21:00Z scan published and was acknowledged (60 rows) — a clean stopping
point, not a scan caught mid-flight. The recovery boot (still running the Pi's then-current commit, `5e9cb8f` —
`deploy.sh` hadn't been re-run yet) came up normally: `mode None -> CONNECTED` at startup, and its first scan
landed with a late lag (see "The off-boundary scan," below). Jong then ran `deploy.sh e6242f4` (this round's own
commit) and did the planned `sudo reboot` as a second, deliberate test: that boot also came up clean, MQTT
connected at 21:24:05 EDT, `mode None -> CONNECTED` at 21:25:13 EDT (~70 s of clock-sync wait, consistent with
doc 04's clock gate). **`journalctl --list-boots` afterward showed 3 boots — the one that ended in the unplug,
the recovery boot, and the deliberate test reboot — with the unplug boot's own `force-probe` log still fully
readable.** A real power cut is the harder case `Storage=persistent` exists for, and it passed that case as well
as the planned one. **The "Persistent-journal fix... not yet confirmed to survive an actual Pi reboot" open item
is closed.**

**The off-boundary scan.** The recovery boot's first scan, `scan_id 01M3TGEVH0SB04KV733M20PVY2`, appeared in the
Pi's own journal at 21:20:58 EDT (2026-10-01T01:20:58Z, derived) — not aligned to a `:00/:15/:30/:45` boundary,
which doc 04 says scans always are (04:37, "Scans align to :00, :15, :30 and :45 UTC"). Queried directly rather
than guessed at: `silver_probe_reading` has exactly 60 rows for this `scan_id`, `event_time = 2026-10-01
01:15:00Z` (the aligned boundary, not the late journal time), `mode = CONNECTED`, `ingest_lag_seconds` 358-360
across the 60 rows. The surrounding window (00:45Z through 01:30Z, four scans total) shows no gap: 00:45Z and
01:30Z both lag 3-5s (normal), the 01:00Z scan (the one taken just before the unplug) lags 1,037-1,039s (the
pipeline, not the probe, was disrupted around the restart — the probe's own log already confirms that scan
published and was acked normally), and the 01:15Z scan alone lags ~360s. **So: not skipped, not stamped with a
guessed time, and not off its true boundary either — "21:20:58 EDT" is when the scan was physically taken and
published, roughly 6 minutes after its 01:15:00Z target; `event_time` itself is correctly the boundary.** This
matches the scheduler's own code, not a bug: `runtime.py`'s `_retime` computes `next_scan` via
`clock.boundary_at_or_after(now, interval)` on the very first tick after a (re)start, which rounds UP to the
next aligned boundary from whatever the clock reads at that moment; the tick loop then simply waits for real
`now` to reach `next_scan + SCAN_DURATION` and for `ClockGate.check()` to report synced before firing, and when
it fires, `_scan(boundary, now)` stamps readings using `boundary` (the target), never the late `now`. Doc 04
describes the two outcomes a reader would expect — on time, or skipped if still unsynced (04:33-35) — but not
this third, real one: a boundary already computed before the process was fully up and synced still fires,
correctly stamped, once the gate opens, even if that is several minutes later. Worth adding to doc 04 as its own
case in a future round; not changed here, since this round's scope was "query and report, change no probe code."
**Caveat on the journal timestamp itself:** per the maintenance window's own framing, any journal line written
before NTP sync completed could carry a clock-skewed timestamp; `event_time` (written only after
`ClockGate.check()` passes) does not have that problem, which is exactly why it, not the journal line, is the
figure trusted above.

### Stage 3c: checkpoint fixes, the fault-log converter, Mustafar's clamp, bridge auto-start

**p4-1 fixed properly, not patched around.** Comparing `gold_sector_baseline` against a plain `event_time`-windowed
recount of `silver_probe_reading` only holds immediately after a fresh baseline build (Stage 3b found 49
apparent "mismatches" running it hours later, all explained by real staleness, not a bug). Neither `event_time`
nor `arrival_ts` can answer "was this row physically in the table as of the baseline's `computed_at`" -- arrival
in bronze and incorporation into the silver *table* are governed by two different, independently-lagging
processes. Databricks' own Delta time travel (`TIMESTAMP AS OF`) answers the question exactly, because it
reconstructs the table's real historical state rather than approximating it from any column. Re-verified live,
~11 hours after the build it time-travels to: 0 mismatches, where the plain-`event_time` version still showed 49.

**`scripts/faultlog_to_seed.py`**, the Phase 4 fault-log-to-seed converter doc 04 names
(`fault_injection_<period>.csv`, "Decided, Phase 4"). Writes only `event_id` and `expected_reject_reason` -- the
two columns `ingest/phase4_checkpoint.sql`'s p4-12 actually joins and compares on, not the full fault record
(`fault`, `channel`, `original`, `injected`, `logged_utc` stay in the pulled log, not the committed seed). Refuses
to overwrite an existing seed without `--force`, the same discipline the enrichment seeds use. Seven tests, one
fixture covering all four doc 04 fault types (`null_channel`, `out_of_range`, `unknown_sector`,
`future_event_time`), four mutations checked: wrong source field, no blank-line skip, no overwrite guard, and a
missing `newline=""` on the CSV write -- that last one is not theoretical: it reproduced a real CRLF corruption on
this very machine the moment the fix was reverted, confirming the standing Windows-CRLF discipline applies here
too. p4-12 itself rewritten from a placeholder-shaped query to a real `FULL OUTER JOIN` against
`<fault_seed_table>` on `event_id`, distinguishing "a logged fault never got rejected" (the real failure) from "a
reject wasn't a logged fault" (expected and fine -- `silver.rejects` also catches real bad data doc 04's fault
injector never touches).

**`ingest/tests/test_phase4_checkpoint.py`**, in `test_phase3_checkpoint.py`'s own style: labels `p4-0` through
`p4-12` present and in order, every statement read-only, no credentials or hosts, every placeholder documented
and no undocumented one used, plus a claim test per block checking its real content (the known 13/47 and 180/60
and 300/180 figures, all four backfill emergencies with their real days and signatures, the cooldown window is
literally 2 hours, the fault seed's join columns match what the converter actually writes). 18 tests, 3 mutations
checked: a renamed label, a `DELETE` statement in real SQL (not a comment -- the first attempt mutated inside a
comment and the read-only test correctly ignored it, confirming the test strips comments before checking, not
that it's blind to them), and an undocumented placeholder rename.

**Mustafar's clamp: a cleaner, more complete picture, and the hypothesis it disproves.** The 2026-09-30 19:45Z
live incident's own `z_dark` per scan (verbatim): 19:00Z −1.869 (below threshold), 19:15Z −4.097, 19:30Z −4.179,
19:45Z −4.199 (the three sustained, qualifying scans), 20:00Z −3.504 (below threshold again) -- `dark_side_activity`
73.6-74.1 across the sustained run, against a 90-day baseline mean of 94.14. Checked whether the dark-side ceiling
(100) might be artificially *compressing* the measured stddev the z-score depends on, which would make a downward
dip look more extreme than it should. **It does not -- if anything, mildly the opposite.** Over the full 90-day
window: Mustafar has 19.6% of all `dark_side_activity` readings sitting at exactly 100 (1,750 of 8,935); Dathomir
7.8% (697 of 8,935); two unclamped comparison planets (naboo, alderaan -- doc 07's own low-dark anchors), 0%.
Excluding the clamped-at-100 pile, Mustafar's stddev is 4.452 (mean 92.647); *including* it, 4.945 (mean 94.087)
-- about 11% larger, not smaller, because the pile sits further from the bulk of readings than the mean does, not
closer. Dathomir shows the same direction (6.134 excluding vs. 6.638 including, +8%). The practical effect on
Mustafar's actual incident: using the official (clamp-inclusive) baseline, z_dark for the 73.6 reading is −4.199,
matching the real figure above; recomputed against the clamp-*excluded* stats instead, the same absolute reading
would score roughly −4.27 -- a *slightly larger* magnitude, not smaller. **No Phase 6 open item added** -- the
task's own premise (the clamp compresses the stddev) doesn't hold; the measured effect runs the other way, and
is small (about 2% difference in the resulting z for this specific dip) either way. The ~20%-of-readings-at-the-
ceiling fact itself is still worth knowing for Phase 6 (doc 04's "two planets cannot fire one" framing undersells
how often Mustafar is sitting right at its physical limit), but it isn't the stddev-compression risk it might
have been. No model, macro, or threshold changed.

**Bridge auto-start.** `infra/desktop/register-bridge-task.ps1` -- a Windows Scheduled Task, at-logon trigger,
restart on failure (999 times, 1 minute apart). Refuses to register, or (with `-StartNow`) to start, a second
bridge: a real process-command-line scan (`Win32_Process`, filtered on `bridge.py`), not a lock file the bridge
has to cooperate with -- the risk it guards against is real, not theoretical, since the bridge's fixed MQTT
client id (`force-bridge`, doc 05) means two running copies would silently disconnect each other the moment the
newer one connects. `-MqttHost` has no default; the bridge's own MQTT credentials still come from `.env.mqtt`,
read by the bridge process itself, never passed on the task's command line. Not registered or run this round --
written, not deployed, per this round's own scope.

### Stage 3d: close Stage 3 — C3's real result, the quota outage, C4's redesign

**C3's real result, verbatim (naboo, `gold_sector_reading`, 2026-10-01 02:30Z-06:00Z):**

| event_time | score | z_dark | signature |
|---|---|---|---|
| 02:30Z | 1.627 | 0.946 | unclassified |
| 02:45Z | 2.609 | 1.778 | civil_unrest |
| 03:00Z | 3.981 | 2.777 | civil_unrest |
| 03:15Z | **6.729** | 4.442 | sith_presence |
| 03:30Z | 6.292 | 3.943 | sith_presence |
| 03:45Z | 6.311 | 3.943 | sith_presence |
| 04:00Z | 6.303 | 3.943 | sith_presence |
| 04:15Z | 6.315 | 3.943 | sith_presence |
| 04:30Z | 3.867 | 2.278 | unclassified |
| 04:45Z-05:45Z | falling further | — | unclassified |

Injected 02:59Z, onset boundary `T` = 03:00Z. **Onset** (first scan `>= 5.75`) was **03:15Z** (`T+15`, score 6.729) — inside
the default 2-scan `ramp`, not only once `hold` began; this disproves an earlier assumption (Stage 3c) that onset is always
`hold`'s own first scan. The 5 consecutive qualifying scans run 03:15Z-04:15Z; 04:30Z is the first scan back under
threshold. `gold_disturbance.sql`'s own `detected_at = max(event_time)` of the run, **not** the onset — `onset_event_time`
only survives internally (it's what `scan_id` in the final output is aliased from: `onset_scan_id as scan_id`), so the
exposed row's `scan_id` is the ONSET's scan, while its `detected_at` is the LAST qualifying scan's time — two different
scans' fields on one row, worth knowing when reading one.

**The real row:** `detected_at = 2026-10-01T04:15:01.650Z`, `sustained_scans = 5` — 75 minutes after `T`, not the ~30 a naive
"within two scans" reading would suggest. The gap splits cleanly into two causes, not one: ~15 minutes is the onset delay
itself (ramp doesn't clear threshold on its very first scan here), and the other ~60 minutes is **Job 1 simply not running**
— see the quota outage below. A healthy pipeline, rebuilding every cycle, would have shown `detected_at` ≈ 03:30Z,
`sustained_scans = 2`. Doc 07's C3 bullet and doc 05's C3/C4 runbook are both corrected to match (the runbook's own two
paragraphs previously disagreed with each other, `T+45` vs `T+30`); `phase4_checkpoint.sql`'s p4-9 carries the full result.

**The quota outage, as a first data point, not a conclusion.** `system.lakeflow.job_run_timeline`, 2026-10-01 02:50Z-05:00Z:

| job | run | result | window |
|---|---|---|---|
| rebuild_baseline (Job 2) | ONETIME? no, scheduled | SUCCEEDED | 03:00-03:02Z |
| force_pipeline (Job 1) | scheduled | SUCCEEDED | 03:03-03:07Z (4m) |
| force_pipeline | scheduled | **CANCELLED** | 03:18-03:46Z (28m before cancel) |
| force_pipeline | scheduled | **CANCELLED** | 03:33-03:55Z |
| force_pipeline | scheduled | **CANCELLED** | 03:48-03:54Z |
| force_pipeline | ONETIME (manual) | SUCCEEDED | 13:05-13:25Z (20m, the backlog catch-up) |

Task log for the cancelled runs: `"Request to create a cluster failed ... RESOURCE_EXHAUSTED: You've hit the limit for
serverless compute for free usage."` All scheduled Job 1 runs 00:03Z-03:03Z that day had already succeeded cleanly in
4-6 minutes each; the failures start exactly at 03:18Z. Jong's own note: that day "also had heavy development use (many
dbt builds and full refreshes)" — a confounded day, not a clean Job-1-alone measurement. **Recorded here as the first
quota-measurement-week data point (`ingest/job1_quota_watch.sql`), not as a diagnosis** — a single event, on a day with
known extra load, isn't enough to conclude Job 1's own 15-minute cadence is unsustainable on its own.

**Cadence recommendation: keep 15 minutes, provisionally.** Reasons: (1) only one outage, on a day independently described
as heavy-dev-load, so there's no clean evidence yet that 15 minutes alone exceeds the free-tier quota under ordinary
conditions; (2) the checkpoint criteria (doc 07's own C3 "within two scans") and the whole near-real-time design intent
both assume 15 minutes — halving to 30 would roughly double C3/C4's detection latency (two scans at 30-minute cadence is
~60 minutes of wall clock, not ~30) as a standing cost, not a one-time one; (3) the actual fix for *this* outage is
resilience (a timeout so a stuck run doesn't sit holding capacity, and removing Job 2's own contention with Job 1 — both
below), not necessarily a slower cadence. **Revisit after the measurement week**: if quota exhaustion recurs on a day
*without* unusual concurrent dev load, that is the trigger to drop to 30 minutes, not this one data point alone.

**Resilience changes, `resources/*.job.yml` (not yet deployed — `pause_status: UNPAUSED`, for Jong to review and
redeploy):**
- `timeout_seconds: 2400` (40 minutes) on every task in both jobs. The two cancelled runs above sat for ~28 minutes each
  before a human cancelled them by hand; Databricks never auto-cancels a run with no timeout configured. 2400s is a real
  margin over the worst backlog run actually observed (the 20-minute catch-up), not a tight fit to it.
- Job 2 moved from 03:00 UTC to **03:10 UTC**. The original choice was justified as "clear of Job 1's cadence so the two
  never overlap" — never actually checked, and wrong: 03:00 is only 3 minutes before Job 1's own `:03` run. The two runs
  that day were adjacent (Job 2 finished 03:02Z, Job 1 started 03:03Z), not literally overlapping that one time, but with
  no margin at all. 03:10 sits in the middle of the 15-minute gap between Job 1's `:03` and `:18` runs.

**`gold_disturbance`, a proposed cost reduction — not built.** The literal idea floated ("recompute only sectors with new
readings since the last build") **would save close to nothing**: the probe sweeps all 60 planets every single scan, so
under normal operation every sector has a fresh reading every 15-minute cycle — there is no "sectors with nothing new" set
to skip in the first place. The real cost driver is total row count (530,000+ and growing ~5,760 rows/day), not which
sectors have fresh data. A genuinely effective version bounds the recompute *window* per sector instead — e.g. the last 7
days, comfortably beyond the 2-hour cooldown and every documented episode's own ramp+hold+decay length — which would cut
the rows `gold_disturbance` rescans by roughly 90%+ (≈40,000 vs. 530,000+) and keep that cost constant as history grows,
rather than unbounded. **Not built, because it reintroduces exactly the correctness risk the current full-rescan design
was built to avoid** (`gold_disturbance.sql`'s own header: a bounded window "risks cutting a real run in half") — a window
needs to be proven wide enough for every real case before it's safe, which this round's scope didn't include doing.

**C4 redesigned** (doc 05, "Runbook: C3/C4"): the original two-injections-30-minutes-apart design left undefined what
`forcesim` does when a second injection lands mid-episode, since the first episode's own default `ramp+hold+decay` (9
scans, ~2h15m) was still active 30 minutes in. The new version uses `probe_ctl.py`'s own documented `--hold`/`--decay`
flags (`edge/probe_ctl.py`; doc 04:96, "may override them") to shorten episode 1 to 5 scans (`ramp` 2 default + `--hold 2`,
doc 04:114's own documented minimum + `--decay 1`), confirms a below-threshold scan before firing episode 2, and fires it
at `T+75` — comfortably inside episode 1's 2-hour cooldown, with a genuine gap between the two runs instead of one
continued episode.

**`cooldown_conflict`: a real doc 03 / implementation mismatch, not resolved this round.** Doc 03's own wording ("Late-
replayed onset inside an existing cooldown... A `BURST` drain can insert an onset scan older than an incident already
recorded... it sets `cooldown_conflict = true`") describes a REPLAYED, out-of-order onset specifically. The actual
implementation (and the Stage 3a unit test, `unit_test_disturbance_cooldown_suppresses_a_second_onset_and_flags_the_first`)
sets `cooldown_conflict = true` for *any* suppressed onset, replayed or not — including a live, in-order second injection
like C4's own. The underlying computation is identical either way (does a later onset fall inside an earlier incident's
cooldown?), so this may just be the doc describing its motivating example without meaning to exclude the general case —
but doc 03's literal words do name the replay scenario specifically, so this is a genuine ambiguity, not a clear-cut doc
violation. Per this round's own instruction (align with doc 03 unless ambiguous, in which case propose wording and stop):
**stopping here, not changing code or the test.** Proposed wording, either direction, for a future round to pick:
- **(a) Narrow doc 03 to match a narrower implementation** (would need an actual behavior change): "`cooldown_conflict`
  is set only when the suppressed onset arrived via a replay (`is_replayed = true` on its own constituent rows), not for
  an in-order live onset that happens to fall inside an existing cooldown."
- **(b) Broaden doc 03 to match the current, general implementation** (doc-only change, no behavior change; this round's
  preference, not a decision): "`cooldown_conflict` is set whenever ANY later onset for the same sector — replayed or
  live — would have fired inside an existing incident's cooldown. The replayed case is the one that needs it most (a
  late-arriving onset can't be told apart from a genuinely new incident without it), but the flag's meaning doesn't
  depend on how the onset arrived."

**`register-bridge-task.ps1` hardened.** `-ErrorAction Stop` added explicitly to every cmdlet that can fail (on top of the
existing `$ErrorActionPreference`), so a success message can only print after the thing it describes actually succeeded.
Checked whether registering an at-logon, run-as-current-user Scheduled Task needs elevation: it doesn't (no `-User`
targeting another account, no `RunLevel Highest`) — documented in doc 05 rather than guessed at. Found and fixed a real
safety bug while reviewing: the running-bridge guard's `Get-CimInstance` call had `-ErrorAction SilentlyContinue`, which
would have silently treated a *failed* process-list query the same as a *successful* query that found nothing running --
backwards for a safety check whose whole job is refusing to proceed when it can't be sure. Refactored the guard into an
injectable `Get-RunningBridgeProcesses -Processes <list>` plus a pure `Test-IsBridgeCommandLine`, with the script's main
body skipped when dot-sourced, so `infra/desktop/register-bridge-task.Tests.ps1` (Pester 3.4.0, pre-installed with Windows
PowerShell 5.1, no new dependency) can exercise the real matching logic against a faked process list. Two mutations
checked (match-on-executable-name-alone, and the same with the empty/null case) — the first broke 3 of 7 tests as
expected; the second (dropping the now-redundant `-and $CommandLine` null guard) broke none, since PowerShell's `-like`
already handles `$null` safely on its own — the guard is kept for clarity, not because a test can prove it's load-bearing.

## Open items

| Item | Status |
|---|---|
| Mosquitto ACL file group-ownership warning (doc 05, S2) | **Resolved, Stage 3b (code) and 2026-10-01 (proven live).** Volume-copied like the password file; the broker restart in the Stage 3b deploy window logged no ACL warning. |
| `veiled_presence` signature unproducible in `STEALTH` | No midi channel present → `z_midi` NULL → `ABS(z_midi)<1.0` can't be true in SQL. Decide in Phase 4 how a missing z-score is treated. |
| `bronze._ingest_ts` is notebook run time, not landing time | Doc 03 OPEN item. Proposed: `_metadata.file_modification_time` as a future bronze column. |
| Duplicate `MQTT disconnected` log line | **Resolved, Stage 3b.** `publisher.py`'s `_on_disconnect` now guards against the duplicate callback, reset on every connect; two tests, both mutation-checked. |
| Unplanned-outage bridge reconnect blip (`19:29:28Z`) | **Partially resolved, Stage 3b:** `bridge.py` now logs its own disconnects and connects with a reason code and a UTC timestamp, so a *future* blip is diagnosable from the bridge's own log. This specific historical blip's cause is still undetermined — the fix couldn't be applied retroactively to a log that already happened. |
| Phase 4 `is_replayed` cutoff (1,800 s) edge case | **Derived, corrected <date>.** The five buffered scans lag roughly 4,190/3,290/2,390/1,788/888 s at replay; the 1,800 s cutoff undercounts this outage by two scans, flagging 180 replayed rows out of the true 300 (previously recorded here as 240 — see the dated correction in PHASE3-RESULTS.md). Worth a silver test — `ingest/phase4_checkpoint.sql` p4-3. |
| Composite score scaling on partial (STEALTH) readings | `SQRT(3/channels_present)` gives a dark-only STEALTH reading about 1.5x the score variance of a full reading with the same dark deviation — it trips 5.75 at `z_dark ≈ 2.35` instead of `≈ 4.07`. `SQRT(4/present_weight)` would remove the gap. Kept as-is for Phase 4; `edge/analyze_thresholds.py` gets a STEALTH-series comparison before the Phase 6 threshold retune decides between them. |
| Fault injection still at `--fault-rate 0` | Deliberately deferred a few days after the mode schedule, so a scheduled outage and an injected fault are never running at once; becomes its own period in a later commit. |
| Persistent-journal fix (2026-09-30) | **Resolved, 2026-10-01.** Proven by a real, accidental power loss (not just the planned graceful reboot) during the Stage 3b maintenance window — `journalctl --list-boots` showed 3 boots, the unplug boot's own log still readable. See "Stage 3b deploy and proofs," above. |
| `gold_disturbance` cooldown uses `LAG()`, not a last-accepted-incident walk | **Resolved, 2026-09-30.** See "Stage 3a: cooldown rewrite," below. |
| 45 non-emergency `gold_disturbance` rows can't be split into ambient-episode-driven vs. pure-noise | The label lives in `edge/backfill_texture.json`'s per-scan episode data, not joined against `gold_disturbance`. Doc 03's own 9-noise / 32-ambient design-time split (0.7/week target) can't be independently re-derived from gold alone yet. |
| `publish_serving` task not built | Waits on the Postgres/local demo stack (Phase 8). Add to Job 1, `depends_on: transform`, once it exists (doc 05). |
| `cooldown_conflict` scope: replayed onsets only (doc 03's literal words) or any suppressed onset (current implementation) | Genuine ambiguity, not resolved — see "Stage 3d," above, for both proposed wordings. Needs a decision before changing either the doc or the code/test. |
| `gold_disturbance`'s full-history rescan, cost growing with total row count (530,000+ and climbing) | A bounded recompute window (e.g. 7 days) would cut this ~90%+ and keep it constant, but risks cutting a real run in half if the window isn't proven wide enough first. Proposed, not built — see "Stage 3d," above. |
| Job 1 cadence (15 vs. 30 minutes) | Kept at 15 minutes provisionally after one quota-exhaustion event on a day with confounding heavy dev load — not enough evidence alone to justify halving it. Revisit after the quota measurement week (`ingest/job1_quota_watch.sql`) if exhaustion recurs on an otherwise-ordinary day. |
| `resources/*.job.yml`'s resilience changes (task timeouts, Job 2's moved schedule, `pause_status: UNPAUSED`) | Written, not deployed — Jong reviews and runs `databricks bundle deploy`. |

## Lessons (beyond this project)

- **"Never happened yet" is not the same state as "happened, and is now false."** Collapsing them (bug 5 above)
  turns ordinary startup into a spurious event. If a signal can be legitimately absent, model that absence as
  its own state, not as a default value of the thing it's absent from.
- **Detection time is not incident time.** Every system that *notices* a failure lags the failure itself — a
  keepalive timeout, a broker's own detection window, a runtime's own polling interval. When timing matters,
  record (or at least suspect) that gap rather than treating the first log line as the true start.
- **A one-way idempotence guard needs a defined reset, or it silences a real second occurrence.** Suppressing a
  duplicate signal is easy; knowing when to start listening for a *new* one again is the part that's easy to
  skip (the duplicate-disconnect proposal, open above).
- **A vendor default that "usually" does the right thing can be silently overridden by something more specific
  the vendor also ships.** `Storage=auto`'s documented rule didn't apply on this Pi OS image because of another
  drop-in already in place; the fix isn't to trust the documented default; it's to check the effective config.
- **A fake without the real dependency's own concurrency behavior can hide a real concurrency bug.** The
  lock-order inversion never reproduced against the offline fake client — only a real broker under real load
  exposed it. An offline test suite proves logic; it doesn't prove thread-safety.
- **When a log looks wrong, an independent second source is the fastest way to find out whether the log or the
  event is what's actually wrong.** The broker's log, `uptime -s`, `docker inspect`, and the Windows event log
  each individually settled a question that the primary source alone couldn't answer honestly.
- **A byte-count difference can be arithmetic, not a data problem.** A field's fixed string value being a few
  characters longer (`"DISCONNECTED"` vs. `"CONNECTED"`) explains a file-size delta exactly; check the encoding
  before reaching for new instrumentation.
