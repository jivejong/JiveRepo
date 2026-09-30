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
TOTAL=9`, 89.7 s.

**Commits:** `93a6ed7`..`a3ba8f2` (repo scaffold through Phase 0 results), 2026-09-23.

## Phase 1 — AI enrichment and dimensions (2026-09-24 to 2026-09-25)

**Goal** (doc 07): `dim_sector` (60 rows) and `dim_jedi` (17 rows), each passing doc 08's review gate, committed
and frozen before anything downstream depends on them.

**Decisions and why:**
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

**Issues:**
- `dim_sector`'s first review-gate pass **failed**. The model had regressed two anchors to the middle of their
  ranges: Geonosis (a Clone Wars battle site and a site of Imperial atrocity) sat at `dark_baseline=40`, tied
  with ordinary worlds; Utapau (the giant-kyber-crystal arc) sat at `kyber_baseline=25`. Fixed with **2 targeted
  human corrections** (Geonosis 40→55, Utapau 25→80, each with sigma recalculated to keep the same relative
  band) recorded in `data/enrichment_corrections.csv` and applied at promote time — not a full regeneration.
  The gate passed after. `dim_jedi` passed its gate on the first try, 0 corrections.

**Checkpoint: MET.** `dim_sector.csv`: 60 rows, review gate PASS after 2 corrections, promoted
2026-09-25T03:27:47Z. `dim_jedi.csv`: 17 rows, review gate PASS, 0 corrections, at least 3 per specialty
confirmed, promoted 2026-09-25T12:54:32Z.

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
4. A lock-order-inversion deadlock between paho-mqtt's own internal mutex and the probe's own lock, found via
   `py-spy` on the Pi mid-scan. Reproduced reliably at volume against a **real** broker; the offline fake client
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

## Open items

| Item | Status |
|---|---|
| Mosquitto ACL file group-ownership warning (doc 05, S2) | Not applied. Proposed: volume-copy like the password file, or pin the image version. |
| `veiled_presence` signature unproducible in `STEALTH` | No midi channel present → `z_midi` NULL → `ABS(z_midi)<1.0` can't be true in SQL. Decide in Phase 4 how a missing z-score is treated. |
| `bronze._ingest_ts` is notebook run time, not landing time | Doc 03 OPEN item. Proposed: `_metadata.file_modification_time` as a future bronze column. |
| Duplicate `MQTT disconnected` log line | Diagnosed (paho can call `on_disconnect` from more than one internal path around a keepalive timeout); fix proposed (a guard that resets on every connect), deferred to Phase 4. Two tests required, not built. |
| Unplanned-outage bridge reconnect blip (`19:29:28Z`) | Cause undetermined from the bridge's own log; it logs nothing on its own disconnect. |
| Phase 4 `is_replayed` cutoff (1,800 s) edge case | **Derived.** The unplanned outage's newest buffered scan lags ~1,785 s — just under a 1,800 s cutoff — so that cutoff would undercount replayed rows for this outage by one scan (240 vs. the true 300). Worth a silver test. |
| Fault injection still at `--fault-rate 0` | Deliberately deferred a few days after the mode schedule, so a scheduled outage and an injected fault are never running at once; becomes its own period in a later commit. |
| Persistent-journal fix (2026-09-30) | Not yet confirmed to survive an actual Pi reboot — the next real test of it. |

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
