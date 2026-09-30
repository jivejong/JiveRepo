# Session handoff — starting a new chat after Phase 3

**Date:** 2026-09-30. **Next phase:** Phase 4, transformation, scoring and signatures (doc 07).
**Working directory:** `Force_Balance_Pipeline/` inside the `jivejong/JiveRepo` monorepo, branch `main`.

This is a pointer document, not a re-summary. Docs 00-08, `docs/ENGINEERING-LOG.md` and the `docs/PHASE*-RESULTS.md`
files are the actual record — read those, not this. This file says *what order to read them in*, *exactly where
things stand right now*, and *what a fresh session won't get from the docs alone*. A second handoff
(`02-HANDOFF-claude-code.md`, from a review chat) was compared against this one fact-by-fact before this version
was written; where it disagreed with the repo, the repo won — see "Corrections" below.

## Read in this order

1. `docs/00-session-handoff.md` — the original design-session index. Still the right first stop.
2. `docs/07-implementation-plan.md` — the phase plan and checkpoints, including Phase 4's own step list.
3. `docs/03-data-model.md` — before proposing anything for Phase 4; it has several OPEN items Phase 4 must
   resolve (see below).
4. `docs/ENGINEERING-LOG.md` — the narrative: decisions and why, issues with cause/fix/how found, checkpoint
   numbers, commit SHAs, one section per phase.
5. `docs/PHASE3-RESULTS.md` — the evidence for the latest checkpoint in full, including the unplanned outage and
   two corrections found after the fact.

Real machine addresses (Pi, desktop, broker) are not in this file or any committed one — they're in the untracked
runbook (`~/.force_balance_pipeline/phase3-checkpoint-runbook.md`) and in chat. Ask the user for them rather than
guessing or reusing ones from an old chat without confirming they still apply.

## Where things stand (HEAD as of this handoff: `76160cc`)

- **Phase 3 checkpoint: MET and committed.** In order: `ddb6002` (checkpoint results + the unplanned outage),
  `a297e3f` (mode schedule on), `5e9cb8f` (clock-sync fix), `d64c4a5` (persistent-journal drop-in fix),
  `b8c3a08` (added `docs/ENGINEERING-LOG.md`), `76160cc` (merged verified facts from a draft engineering log into
  it). **Confirm `git log` still shows `76160cc` at HEAD before trusting anything else here** — if not, something
  landed after this handoff was written and the newer state wins.
- The mode schedule is on; the clock-sync fix and the persistent-journal fix are both deployed to the Pi.
  The journal fix is verified with `ls`/`--disk-usage` but **not yet tested across an actual reboot**.
- **Fault injection is deliberately still off** (`--fault-rate 0`) — intentional, not forgotten: the schedule
  needs a few clean days running alone first, so a scheduled outage and an injected fault are never ambiguous in
  the data. The built-and-tested files for turning it on are staged, untracked, at
  `~/.force_balance_pipeline/pending/fault-injection/` (`04-edge-simulators.md`, `force-probe.service`,
  `test_infra_phase3.py`). **Re-run their tests against current HEAD before applying** — the clock-sync fix
  landed after they were built and could interact with them. Confirm with the user that this directory still
  holds what's expected before trusting it.
- The first scheduled outage has not yet been observed on the Pi. When the user pastes a mode-log excerpt showing
  one, check it has a schedule reason (not `link_lost`/`no_initial_connect`), lasts 1-3h, and ends on its own.
- Tests as of this handoff: edge 498, ingest 261, all green.

## Corrections to the pasted `02-HANDOFF-claude-code.md`

Checked against the repo directly, per the standing rule below ("verify every repo fact"):
- **HEAD is stale in that file** (`5e9cb8f`) — three commits landed after it (`d64c4a5`, `b8c3a08`, `76160cc`).
- **The README status note is stale.** `Force_Balance_Pipeline/README.md` shows no uncommitted changes; whatever
  the user was doing to it earlier has already landed.
- **The `ingest/bridge/` layout is wrong.** There is no `config.example.yaml` there — the real config-example
  files are `.env.bridge.example` and `.env.mqtt.example` at the repo root. `ingest/workspace_check.py` and
  `ingest/e2e_local.py` exist and weren't listed.
- **"Handoffs stay outside the repo" is aspirational, not actual.** `handoffs/phase-3/ENGINEERING-LOG.md` is
  git-tracked and committed, and this file is headed the same way. Keep every handoff address-free regardless of
  where the rule says it should live — that constraint is what actually matters, not the location.
- Confirmed correct and worth keeping: the deadlock/startup/clock-sync/drain-logging fixes (section 6 there), the
  composite-score formula (missing only the `SQRT(3/channels_present)` partial-reading scale, added below), the
  `ingest_lag_seconds`/`is_replayed` rule, `deploy.sh` escalating internally via `sudo` (confirmed in the script),
  and the observation that commits land after 18:00 on weekdays (true for every weekday commit checked; weekend
  commits don't follow it, which is consistent since the rule only claims weekdays).
- Could not verify from any doc: a 90-day expiry on the bridge's OAuth service-principal secret. Asserted by two
  independent sources now, but still not written down anywhere in this repo — treat as probably true, not
  confirmed, and don't cite it as fact without checking the actual credential.

## Design facts Phase 4 depends on

- **Envelope** (doc 02): `event_id`, `source_id`, `source_type`, `schema_version`, `event_time`, `mode`,
  `scan_id`, `sector_id`, `is_synthetic`, `synthetic_ingest_ts`, `payload`. Both synthetic fields are always
  present (Auto Loader's rescue mode sends anything missing from the inferred schema to `_rescued_data`).
- **`mode`** is the mode when the reading was *taken*, not when it was sent — a replayed row keeps
  `DISCONNECTED`. That's why replay files run ~3 bytes/line larger than normal ones.
- **Bronze columns:** envelope fields + `dt DATE` (partition), `hh INT`, `_source_file`, `_ingest_ts`,
  `_rescued_data`. `payload` is `VARIANT` via `try_parse_json(payload)` — it arrives as a STRING because
  `inferColumnTypes=false`.
- **`_ingest_ts` is the notebook's run time, not file arrival** (doc 03 OPEN item, explicitly flagged there as
  "decide before silver is built" for live rows specifically). Phase 3 proved arrival independently with
  `_metadata.file_modification_time` (checkpoint query p3-3b) without changing bronze's schema.
- **Silver lag:** `ingest_lag_seconds = unix_timestamp(synthetic_ingest_ts if is_synthetic else _ingest_ts) -
  unix_timestamp(event_time)`; `is_replayed = ingest_lag_seconds > 1800`.
- **STEALTH rows:** hourly at `:00`, all 60 planets; `midichlorian_ppm`/`kyber_resonance` are JSON null,
  `sensor_temp_c` is omitted, `dark_side_activity`/`battery_pct` present. Route to `probe_reading` with
  `is_partial = true`, never to rejects.
- **Housekeeping events:** `payload.kind` present (e.g. `buffer_overflow`) → route before the sector check;
  `sector_id = source_id`, `scan_id = null`.
- **Walk:** AR(1), `K = 0.15`, step SD `= sigma * sqrt(K*(2-K))`; stationary SD equals the seed's own sigma.
  Dark spikes are a per-day rate (`p/96` per scan) — this was a real bug earlier (read per-scan), see the log.
- **Thresholds:** anomaly 4.0 (provisional), emergency 5.75 (tuned 2026-09-26 for ≤1 false sustained
  incident/week). Injection targets are computed, not hard-coded, with margin `M = 1/(1-3e)`, `e ≈ 1.9%`.
- **Composite score** (doc 03): `SQRT(z_midi^2 + z_kyber^2 + 2*z_dark^2) * SQRT(3/channels_present)` — dark
  double-weighted, and the second factor scales a partial (`STEALTH`) reading so it isn't automatically
  lower-scoring than a full one. Mustafar/Dathomir clamp at `dark_baseline = 100` and can't reach 5.75.
- **Backfill:** 518,400 rows, `is_synthetic = true`, `source_id = probe-01`. 4 injected emergencies (tatooine
  sith_presence, dantooine nexus_awakening, kamino force_drain, coruscant civil_unrest), one slow riser
  (mon_cala, must stay below the anomaly threshold), 6 `DISCONNECTED` gaps with `BURST` recovery.
- **Known duplicate test case:** 13 `event_id`s from the Pi's 2026-09-27 23:45Z scan landed twice (a partial
  file before the deadlock fix, then the full drain after) — expected, not a bug to chase.
- **`is_replayed` boundary test case:** the unplanned 2026-09-29 outage's 19:00Z scan replayed ~1,785s after it
  was taken, just under the 1,800s cutoff — expect 240 replayed rows out of that outage's 300, not 300. Worth a
  silver test specifically for this.
- **Scheduled outages are now normal data**, not just test artifacts — the mode log's reason field is what tells
  a scheduled outage from a real one; don't assume every `DISCONNECTED` span is anomalous.

## Phase 4 decisions to raise first (plan mode, before writing code)

1. **Arrival-timestamp column in bronze** (doc 03 OPEN, "decide before silver is built"). A candidate exists:
   `_metadata.file_modification_time` as a new bronze column. A test currently keeps the notebook from selecting
   it; that test changes if this is approved.
2. **`veiled_presence` NULL handling** (doc 03 OPEN): `z_midi` is NULL for `STEALTH`, so `ABS(z_midi)<1.0` can
   never be true in SQL — the signature can't fire as written.
3. **`impossible_timestamp` future tolerance** — faults inject +24h; the silver check's tolerance needs to be
   smaller than that to still catch it.
4. **Housekeeping routing** ahead of the `unknown_sector` check (already decided in doc 02/03; just confirm the
   silver model actually implements the ordering).
5. **Incremental merge on `event_id`** and how a replayed row recomputes affected gold rows without duplicating
   history.
6. **The dual-target `extract_payload` macro** (Databricks + Postgres branches) — doc 07 calls this the
   fiddliest thing in the whole design; if it fights, ship the Databricks target alone and add Postgres in
   Phase 8.
7. **Fault-log reconciliation path**: how `fault_injection.jsonl` gets from the Pi into Databricks (pulled via
   `scp` today; not yet decided how it lands in a volume).
8. **The user writes the first three silver models by hand** (doc 00's own working guidance) — scaffold and
   review, don't generate them outright.

Present each as: what the docs say (with a line reference), what's silent, the options, a recommendation — then
stop and let the user decide, per the standing rules below.

## Phase 3 fixes — know these before touching probe/runtime code, so they don't get reintroduced

- **Deadlock:** never call into `paho` while holding the probe's own lock, and never block inside a paho
  callback. `on_publish` only puts the mid on a `queue.Queue`; the runtime's `tick()` reconciles it every cycle.
  This was introduced *by* an earlier fix in the same phase (closing a PUBACK-loss race by holding the lock
  across the paho call) — a reminder that a concurrency fix can itself introduce a concurrency bug.
- **Startup:** no transition is recorded before the first real connect (not the same as a link loss);
  `no_initial_connect` fires only after `ack_timeout` (30s) with still no connect; the first real connect goes
  straight to `BURST` if there's a backlog, never through a false `DISCONNECTED`.
- **Clock sync:** `ModeController`'s writes are deferred until the runtime's own NTP-sync check first reports
  true; the first synced write is one snapshot carrying a suppressed-transition count and, if one of them was a
  `DISCONNECTED`, its reason and uptime (monotonic, never wall clock).
- **Drain logging:** the drain's last batch must be settled every tick (`_settle_drain_batch()`), not gated on
  `mode == BURST`, or its "acked" line never prints.
- **Unit file:** `--fault-rate 0` must be explicit in `ExecStart` (the CLI default is full injection).
  `--assume-clock-synced` is a desktop/offline dev flag and must never appear in the Pi's own unit file.

## Working rules established in chat (mostly not written in any doc)

- **Git:** the user runs all *write* commands (add/commit/push/checkout/reset/stash). Read-only git
  (`status`/`diff`/`log`/`ls-files`/`show`) is fine to run freely. **Verify every repo fact in a pasted handoff
  document** (paths, SHAs, test counts, file names, what's actually committed) before trusting it — this file
  itself needed several corrections above.
- **Docs are truth.** If a doc is silent or ambiguous, stop and ask rather than deciding. Explicit numbers in a
  doc (cadences, counts, thresholds, durations) don't change without the user's OK — record a deviation note
  instead of silently editing the number. Show doc diffs before applying them.
- **No workspace/Docker/Pi/SSH/firewall action without a go-ahead.** The user runs anything needing a password;
  a session can't answer a `getpass` prompt. Specific diagnostic commands have been pre-authorized in past
  sessions on a case-by-case basis — ask if unsure which bucket a new action falls into.
- **No secrets or real addresses in anything committed**, `handoffs/` included now that it's confirmed tracked.
  Real desktop/Pi addresses live in chat and in the untracked runbook only; committed docs use `<DESKTOP_IP>`,
  `<PI_IP>` placeholders.
- **Commit messages:** subject line leads with the most important change, no emoji, no AI-attribution trailer
  (explicit project override), list every file touched from `git status` scoped to `Force_Balance_Pipeline/`
  only (the user has unrelated edits elsewhere in the monorepo), state plainly whether secrets/machine-specific
  values are present, frozen data gets its own commit separate from code. No narration about the assistant
  itself in the message body.
- **Every new behavior gets a real test; every fix is mutation-checked** (mutate the logic, confirm the new
  test fails, restore, reconfirm green) — confirm the *baseline* passes before trusting any mutant result, and
  report which mutants (if any) survive.
- **Windows gotcha:** any throwaway Python script that reads and rewrites a whole file must use binary mode
  (`"rb"`/`"wb"`) or `newline=""` — plain text-mode `open(path, "w")` silently converts the whole file to CRLF
  on this machine. Bit this project twice already; `test_repo_hygiene.py` catches it, but the Edit tool avoiding
  the whole problem is better than relying on that catch.
- **Flag new, unrelated bugs explicitly** rather than folding a fix silently into unrelated work.
- **Results files are built from pasted command output only.** Label arithmetic and verdicts as derived, never
  presented as if they were pasted.
- **Stop at the requested stopping point and report** — don't roll forward into the next step uninvited.
- Every outbound HTTP call sends the project User-Agent; the bridge must never read `DATABRICKS_TOKEN`.
- **Phase-close routine:** when a phase's checkpoint is met and committed, add its dated entry to
  `docs/ENGINEERING-LOG.md` (own commit), and produce/refresh a handoff like this one.

## Open items (see `docs/ENGINEERING-LOG.md` for the full table — not duplicated here)

Likely to come up first: the duplicate `MQTT disconnected` log line (guard proposed, must reset on every
connect, two specific tests required, deferred to Phase 4); the unplanned outage's bridge reconnect blip (cause
undetermined); the `is_replayed` 1,800s cutoff edge case above; `bronze._ingest_ts` vs. arrival time (doc 03,
"decide before silver is built" — this one is close to a Phase 4 blocker, not just a nice-to-have); the Mosquitto
ACL group-ownership warning; Docker Desktop's Allow rules reappearing after an update.

## Likely next action

Either (a) apply the staged fault-injection commit once the mode schedule has run clean for a few days, or
(b) start Phase 4 per doc 07, beginning with the "decisions to raise first" list above. Ask the user which,
rather than assuming.
