# Engineering log

A chronological record of how Force Balance Pipeline was built: the technology chosen, the decisions made and why, the problems found, and how each was resolved. It complements the design docs (`docs/00`–`08`), which describe what the system is, and the phase results files (`docs/PHASE*-RESULTS.md`), which hold the verification evidence. Where this log and a design doc disagree, the design doc is authoritative; this log records how it got that way.

Dates are US Eastern. Timestamps inside entries are UTC and marked `Z`. Machine addresses, workspace hosts and credentials are deliberately omitted.

---

## Stack at a glance

| Layer | Technology | Version / detail |
|---|---|---|
| Edge device | Raspberry Pi 3, Raspberry Pi OS Lite 64-bit | systemd service, Python 3 venv, SQLite (WAL, `synchronous=FULL`) |
| Edge simulation | Python stdlib (`forcesim`, `probe`) | Stdlib Box-Muller and ULID, no third-party randomness |
| Messaging | MQTT, Eclipse Mosquitto | `eclipse-mosquitto:2` (2.1.2) in Docker; `paho-mqtt==2.1.0`; QoS 1 |
| Collector bridge | Python | Databricks Files API, OAuth M2M service principal |
| Landing | Databricks Unity Catalog volume | `force.raw.telemetry`, NDJSON, `dt=`/`hh=` ingest-time prefixes |
| Ingestion | Databricks Auto Loader | Notebook, serverless environment version 5 (Python 3.12) |
| Warehouse | Databricks Free Edition (AWS), Delta Lake | Catalog `force`: `raw`, `bronze`, `silver`, `gold` |
| Transform | dbt Core 1.10.13, dbt-databricks 1.9.8 | dbt job task sourced from Git |
| Reference data | SWAPI (`swapi.info`) | Frozen snapshot: 60 planets, 82 people, 37 species, 36 starships |
| Enrichment LLM | Google `gemini-3.1-flash-lite` | Default temperature 1.0, thinking level `low`, structured JSON output |
| Local dev | Windows desktop, Docker Desktop, VS Code, Claude Code | Separate venvs for edge and dbt |

---

## 2026-09-23 — Phase 0: platform de-risking

**Goal:** answer three unverified platform questions before building on them.

- **Scaffold.** Top-level layout (`edge/`, `ingest/`, `agent/`, `intake/`, `dashboard/`, `infra/`, `data/`), Makefile stubs that exit non-zero so a stub can never pass as a check, and a trivial dbt project (seed → view → table) to exercise the toolchain.
- **Decision: the project lives inside an existing monorepo.** `Force_Balance_Pipeline/` is a top-level folder of `jivejong/JiveRepo`, so the dbt task's `project_directory` is `Force_Balance_Pipeline/warehouse/dbt`.
- **Decision: every outbound HTTP call sends an explicit User-Agent.** Python's default `urllib` agent was blocked by Cloudflare (error 1010) on SWAPI's CDN. One helper per module sets `Force_Balance_Pipeline/0.1 (+<repo URL>)`.
- **Q1 — does the dbt job task run from Git on Free Edition? Yes.** Run `981893542841724` succeeded: dbt 1.10.13, adapter 1.9.8, PASS=9, 89.7 s, commit SHA recorded by `git_snapshot.used_commit`.
  - *Issue:* two earlier runs failed with `dbt copy: _collect_pairs failed … No such file or directory`. *Cause:* the commit being run hadn't been pushed. *Lesson:* push before any Git-sourced run.
  - *Issue:* the serverless environment version wasn't documented where first checked. *Resolution:* `environment_version: "5"` confirmed from Databricks release notes; made the default.
  - *Decision:* pin `dbt-core==1.10.13` alongside `dbt-databricks==1.9.*`, since pip had resolved core freely.
- **Q2 — can Unity Catalog external locations point at GCS? No.** The workspace runs on AWS, and the storage-credential dialog offers only AWS IAM Role and Cloudflare API Token. *Consequence:* the bridge writes through the Files API into a managed volume.
- **Q3 — do `streaming_table` models build and refresh? Yes, with a caveat.** The first refresh attempt failed (Spark Connect session deleted before ready, 622 s); the automatic retry succeeded in 25.84 s with an incremental refresh. Auto Loader stays a notebook task for now.
- **Local gate:** a `dbt ls` toggle check confirmed the streaming model is disabled by default (2 models) and enabled only with the var (3 models). Windows PowerShell 5.1 strips inner double quotes, so `--vars` uses YAML flow syntax `"{enable_streaming_check: true}"`.

## 2026-09-23 to 2026-09-25 — Phase 1: AI enrichment and dimensions

**Goal:** generate the planet and Jedi parameters every later phase depends on, review them, and freeze them as seeds.

- **Decision: switch the LLM from Groq to Gemini (`gemini-3.1-flash-lite`).** Works better with the developer's VPN, and credits were available. Groq references were replaced across docs, except historical egress evidence.
- **Decision: default temperature 1.0, not 0.** Google's Gemini 3 guidance warns temperatures below 1.0 risk looping and degraded output. Reproducibility therefore comes from the reviewed, frozen CSVs, not from the model; reruns are not expected to match.
- **SWAPI snapshot.** `fetch_swapi.py` writes raw JSON only if all four resources validate, and refuses to overwrite without `--force`.
- **Decision: the Jedi roster is 17 Jedi Order members from Episodes I–III present in SWAPI**, Anakin included, Sith and non-Jedi Force-sensitives excluded, Jocasta Nu included. SWAPI's spelling "Ayla Secura" is kept.
- **Issue: Ilum (the obvious kyber anchor) isn't in SWAPI.** *Resolution:* Utapau becomes the kyber anchor (the giant kyber crystal from the Clone Wars Utapau arc).
- **Decision: review anchors are never named in prompts.** Three prompt examples naming Coruscant, Ilum and Mustafar were reworded, so the review tests the model's knowledge, not the prompt.
- **Issue: SWAPI planets/28 is literally "unknown"** (no data; homeworld of Yoda and Qui-Gon). *Resolution:* keep it as `sector_id = uncharted` with `is_unknown = true`, never send it to the model, and fill its numeric columns with the median of the other 59. A singular dbt test asserts exactly one such row.
- **Issue: structured output shape.** A probe script bisected a 400 `INVALID_ARGUMENT`: `responseMimeType` + `responseJsonSchema` is accepted, `responseFormat` is rejected, `thinkingLevel` accepted. Only the working shape remains in code.
- **Issue: thinking tokens count against `maxOutputTokens`.** Early calls hit `MAX_TOKENS`. *Resolution:* caps of 65,536 (planets, one call for all 59) and 16,384 (Jedi).
- **Issue: the first review gate rejected correct data.** It demanded an even spread (P10–P90 span, SD, occupied bins), but the design's own lore makes kyber and dark right-skewed. *Resolution:* the gate now tests range use (lowest ≤ 0.25, highest ≥ 0.75 of range), clustering (≤ 35% in the 0.40–0.60 band), sigma clamping (≤ 10%), and all seven anchors (blocking). `check_gate_parity.py` keeps the Python gate and the analysis SQL in sync.
- **Decision: seeds are written only by promoting a reviewed saved response** (`--from-response <file> --write`), never by a fresh call. Promotion refuses on a prompt-hash or input-data mismatch, an incomplete run, a failing gate (unless an override is recorded), or an existing seed (unless `--force`). Raw responses are saved outside the repo before parsing.
- **Decision: human corrections are recorded, not hand-edited.** `data/enrichment_corrections.csv` applied two: Geonosis dark → 55 and Utapau kyber → 80 (sigma rescaled to the same percentage). The provenance sidecar records original value, reason and the file's SHA-256.
- **Jedi prompt v2.** v1 didn't guarantee three Jedi per specialty; v2 requires it and lowers `canon_confidence` where canon is thin. Result: combat 6, diplomacy 4, investigation 4, stealth 3. Obi-Wan came back `master` rather than `council_member`; accepted as a flavour field.
- **Issue: dbt parses every `.md` under `seeds/` as a docs file,** so an unbalanced `{%` in a free-text correction reason would break every dbt command. *Resolution:* the provenance generator neutralises Jinja tokens.
- **dbt notes:** column types use `"{{ 'double' if target.type == 'databricks' else 'double precision' }}"`; integer `accepted_values` needs `quote: false`; the Phase 1 analyses were gated behind a var until the seeds existed.
- **Checkpoint met:** `dim_sector` 60 rows, `dim_jedi` 17 rows; 25/25 seed tests, the singular test and all three checks pass. Thinking level `low`; prompt hashes planets `0ab79f1844ca`, Jedi v2 `93521a8f269a`.

## 2026-09-25 to 2026-09-26 — Phase 2: ingestion path and 90-day backfill

**Goal:** simulated probe → broker → bridge → volume → bronze, plus 90 days of synthetic history to cold-start rolling baselines.

- **Decision: mark synthetic rows in the envelope.** Two fields, always emitted: `is_synthetic` and `synthetic_ingest_ts`. *Why always:* Auto Loader's `rescue` mode sends any field missing from the inferred schema to `_rescued_data`, so omitting them on live rows would break the backfill. Silver computes lag from `synthetic_ingest_ts` for synthetic rows.
- **Decision: `dt=`/`hh=` paths are ingest wall-clock time, not event time.** The whole backfill lands in one or two `dt` partitions. Event-time organisation belongs in silver.
- **Decision: one file per scan** (60 lines), for backfill and live alike: 8,640 backfill files.
- **Issue: the documented random walk was 1.9× too noisy.** `value = prev + N(0, σ) + 0.15(baseline − prev)` is AR(1) with stationary SD σ/√(1−0.85²) ≈ 1.898σ, which would make every z-score 1.9× too small. *Resolution:* step SD = σ·√(K(2−K)) ≈ 0.5268σ, each series starts from a stationary draw, and a regression test fails if the old form returns.
- **Issue: the seed's `dark_spike_probability` read per scan meant ~5,000 spike episodes in 90 days**, contradicting "quiet with rare spikes". The seed is frozen. *Resolution:* the simulator reads it as a per-day rate (p/96 per scan); documented in docs 04 and 08.
- **Issue: signature targets at the region edge classified correctly only ~58% of the time** under walk noise. *Resolution:* during an injected hold, step noise is zero on the channels that define the signature's region.
- **Issue: most signatures couldn't reach the emergency threshold at minimal targets.** *Resolution:* targets are computed, not listed: the smallest whole-σ point that classifies as the signature and exceeds its threshold × M, where M = 1/(1 − 3e) and e ≈ 1.9% is the sampling error of a 90-day SD.
- **Thresholds tuned (2026-09-26):** anomaly 4.0 (provisional), emergency 5.75, chosen for at most about one false sustained incident per week galaxy-wide. At 5.75, noise-only runs fire 0.7 per week and 32 of 52 ambient spike episodes fire. Mustafar and Dathomir clamp at dark = 100 and can't fire. A parity test ties computed targets to the documented thresholds.
- **Decision: the bridge authenticates as a dedicated service principal** (`force-bridge`, OAuth M2M) with `READ/WRITE VOLUME` on the landing volume only. Token exchanged at `/oidc/v1/token`, cached, refreshed. The personal access token is for local dbt only.
- **Decision: exactly-once uploads.** A batch's file ULID is chosen once; uploads use `overwrite=false`; HTTP 409 means an earlier attempt landed. Confirmed live.
- **Issue: `parse_json(to_json(payload))` in the notebook.** With `inferColumnTypes=false`, payload arrives as a STRING. *Resolution:* `try_parse_json(payload)`, plus explicit casts for `schema_version`, `dt`, `hh` and a final select in documented column order.
- **Decision: reproducible backfill.** A committed manifest records seed, window, texture-file and seed-CSV hashes, counts, and a content hash; ULIDs derive from event time plus seeded randomness, so regeneration is byte-identical. Backfill texture: 4 drifting planets, 4 injected emergencies, one slow riser that must stay below anomaly, 6 DISCONNECTED gaps with BURST recovery. The window ends before the earliest live event, enforced by an overlap guard.
- **Checkpoint met:** 180 live rows, byte-for-byte matching, exactly-once on rerun; backfill 518,400 rows, 8,640 scans × 60, 91 calendar days, one `dt` partition, content hash `8308472c…ffc62`.
- **Deviation recorded:** the live check ran at 60-second intervals rather than the real 15-minute cadence. Closed in Phase 3.

## 2026-09-26 to 2026-09-27 — Phase 3 planning and edge code

**Goal:** the four probe modes on real hardware, and the project's core claim: survive an outage with no gaps and no duplicates.

- **Decision: stage on the LAN first.** Broker and bridge on the development desktop, the Pi publishing over the local network. TLS and the cloud broker come later, together.
- **Decisions on behaviour:**
  - Readings taken before the clock syncs are skipped and logged, never stamped.
  - STEALTH scans hourly at :00, all 60 planets, midichlorian and kyber null, temperature omitted.
  - Write-ahead buffer: one SQLite transaction per scan, full envelope JSON, WAL + `synchronous=FULL`.
  - Drain: batches of 500, wait for every PUBACK, delete, 10 s pause.
  - Buffer overflow emits a housekeeping event (`payload.kind = "buffer_overflow"`).
  - Fault injection (2.7% total: null, out-of-range, unknown sector, +24 h future) only while CONNECTED, logged locally, and off for the checkpoint.
  - `mode` on a replayed reading is the mode when it was taken.
- **Decision: the test outage is a Pi-side nftables drop,** removed automatically by a `systemd-run` timer, so a lost SSH session can't leave it in place.
- **Decision: code reaches the Pi only by sparse Git clone at a pinned commit** (`edge`, `warehouse/dbt/seeds`, `infra/pi`), never by copying files.
- **Issue: a PUBACK could arrive on paho's network thread before `publish()` recorded its message ID,** so that row would be sent twice. Fixed with a regression test. (This fix later caused the deadlock below.)
- **Issue: the first mutation-test run was invalid;** every mutant appeared "killed" because the harness itself was broken. *Resolution:* check that the baseline passes before trusting any mutant.
- **Offline rehearsal:** a 45-minute outage on a fake clock showed detection, buffering, drain and a mid-drain crash losing nothing.

## 2026-09-27 — Phase 3: broker, network security, and first hardware run

- **Issue: the broker crash-looped with `Unable to open pwfile`.** `mosquitto_passwd` (run as root) created a root-only file, and Mosquitto 2.1.2 runs as the `mosquitto` user. *Resolution:* copy the password file into a Docker volume with `chown mosquitto:mosquitto` and `chmod 0600`.
- **Open:** 2.1.2 warns that the repo-mounted ACL's group isn't `mosquitto` and says future versions will refuse it.
- **Broker verification:** anonymous and wrong-password connections refused; the ACL proven by delivery, not just acknowledgment (telemetry reaches the bridge, control reaches the probe, all wrong-direction publishes are not delivered).
- **Issue: the whole LAN could reach port 1883** despite a firewall rule scoped to the Pi. *Cause:* Docker Desktop installs "Docker Desktop Backend" Allow rules on the Private profile, and Windows combines Allow rules. *Resolution:* disable those rules (not Block, which would also block the Pi). Verified with a TCP test from another machine.
  - *Lesson:* the broker log can't identify clients, because Docker Desktop's port relay presents every connection as `172.17.0.1`. `Test-NetConnection` pings first, so its "hang" can precede a successful port test.
- **Desktop cadence run:** three scans at the real 15-minute cadence landed as three 60-line files, flushed by the complete-scan rule, crossing an hour boundary correctly. Closes the Phase 2 cadence deviation.
- **Issue: a desktop test's state directory wasn't gitignored,** though it had been reported as ignored. The repo's own hygiene test caught it.
- **Pi deployment issues, each fixed in the repo:**
  - The unit file lacked `--fault-rate 0`; the CLI default is full injection. Now explicit, with a lint test.
  - The manual clone into `/opt` failed on permissions. Doc 05 now creates and chowns the directory first.
  - `deploy.sh` wasn't executable in Git (committed from Windows). Fixed with `git update-index --chmod=+x`.
  - The headless probe logged nothing useful. Added journald lines for connect, disconnect, each scan, each drain batch and each mode transition.
- **Issue: deadlock (the most important bug of the phase).** The probe froze after two scans, mid-publish of the third (13 of 60 readings sent). A `py-spy` dump showed the main thread holding the probe lock inside `paho.publish()`, waiting for paho's internal lock, while paho's network thread held that lock inside the probe's `on_publish`, waiting for the probe lock. The broker dropped the client at 23:46:34Z for missed keepalives, matching the partial file. *Resolution:* never call paho while holding the probe lock; `on_publish` only queues the message ID; the main loop reconciles acknowledgments every ~0.5 s. A stress test against a real paho client and broker (30,000 QoS 1 publishes in unthrottled batches of 500) hangs on the old pattern and passes in 4.5 s on the fix. *Why earlier tests missed it:* the fake publisher had no network thread, and pausing between batches closed the race window.
  - Write-ahead buffering held: all 60 readings of the frozen scan were on disk and drained after a forced kill. The 13 already-landed readings became known duplicates for silver's dedup test.
- **Issue: a false `link_lost` at every startup.** The probe recorded CONNECTED before its first connect, then a disconnect 200 ms later. The broker log showed one continuous connection. *Resolution:* nothing is recorded before the first real connect; with a backlog, the first connect goes straight to BURST. A second bug found alongside: the last drain batch's "acked" line was never logged, because the mode left BURST one tick early.

## 2026-09-28 — Phase 3: soak, STEALTH, and an accidental power-loss test

- **Overnight soak:** the fixed probe scanned every quarter hour overnight with no gaps (31 consecutive scans in the checked window), each 60 buffered and 60 published, buffer at 0, clean startup. The bridge reconciled every scan to one 60-line file.
- **Issue: a probe booting with no broker would never record an outage.** *Resolution:* if the first connect hasn't happened within `ack_timeout` (30 s, an existing constant), record DISCONNECTED with reason `no_initial_connect`; scans taken during the wait are drained on connect.
- **Issue: `_ingest_ts` is the notebook's run time, not arrival.** The notebook sets it with `current_timestamp()`, so every row a run ingests shares one timestamp. *Resolution for Phase 3:* checkpoint query p3-3b proves arrival from the landed files' `_metadata.file_modification_time`. Recorded as a Phase 4 OPEN item with a proposed bronze column.
- **STEALTH test:** operator control message at 18:54:20Z, a 60-planet STEALTH scan at 19:00:03Z, automatic revert at 19:04:21Z. The landed file was 1,509 bytes smaller than a normal scan, exactly the STEALTH payload shape.
- **Accidental power-loss test:** a cable was knocked around 09:22 EDT. The Pi rebooted, the service started unattended, reconnected, and missed zero scans.
  - *Issue:* the Pi's journal was in memory only, so the previous boot's logs were lost. *Resolution:* persistent journal added to the Pi setup in doc 05.
  - *Issue:* the mode log's startup entry was stamped about three minutes before the real boot, because the Pi has no real-time clock and `fake-hwclock` restores a stale time until NTP syncs. Readings were already guarded by the sync check; mode-log entries weren't.

## 2026-09-29 — Phase 3 checkpoint met

- **Boot-with-no-broker fix proven on hardware:** `no_initial_connect` recorded exactly 30 s after startup, recovery on its own when the rule was removed.
- **The cut:** DISCONNECTED at 14:19:05.866Z, BURST at 15:01:57.431Z with a backlog of 180, drained in one batch within the same second, back to CONNECTED. Normal scans resumed on the quarter hour.
- **Checkpoint queries:**
  - 180 rows, 3 scans, 60 sectors, all labelled DISCONNECTED, event times spanning the outage.
  - The three replay files landed within 2 s of each other, 2 s after BURST; oldest reading 1,919 s old, newest 119 s.
  - 6 of 6 quarter-hour slots present; 0 duplicate event IDs; 0 incomplete scans; 0 housekeeping events.
  - STEALTH: 60 of 60 rows with midichlorian and kyber null, dark present, temperature absent.
  - The p3-3 query based on `_ingest_ts` reported 180 replayed rows instead of 60 because the notebook ran 35 minutes after the replay, which illustrates the Phase 4 OPEN item.
- **Clock-sync fix built:** mode-log writes wait for NTP sync; the first synced entry is a snapshot recording how many transitions were held back and any pre-sync DISCONNECTED with its uptime (monotonic, never wall time), so a boot during an outage can't hide it.
- **Issue: two source files were rewritten with Windows line endings** by a scripted text-mode Python write, invisible in `git diff`. The hygiene test caught it. *Standing rule:* scripted rewrites use binary mode or `newline=""`.
- **An unplanned real outage (afternoon).** A local connectivity loss cut the Pi off from the broker before any schedule was deployed.
  - The broker timed the Pi out at 18:13:46Z (`exceeded timeout`); the probe detected it at 18:14:16.192Z (`Keep alive timeout`). The mode log records detection, so the real break was earlier, around 18:12Z.
  - Five scans (300 readings) were buffered; the probe reconnected at 19:29:47.517Z, drained all 300 in one batch within a second, and was CONNECTED at 19:29:48.087Z. About 1 h 15 m 31 s offline, nothing lost.
  - *Where it broke:* the broker never restarted (up since 2026-09-27, 0 restarts), kept saving its database every 31 s throughout, and never dropped the bridge; Windows logged no adapter, DHCP or power events. So the break was on the path between the Pi and the desktop, most likely the router or switch.
  - *Minor findings, deferred to Phase 4:* the probe logged `MQTT disconnected` twice for one disconnect (paho calls the callback more than once; the mode log correctly recorded one transition). The bridge closed and reopened its own connection at 19:29:28Z, 19 s before the Pi returned; its log doesn't say why.
  - *Useful edge case:* the scan taken at 19:00Z arrived about 1,785 s later, just under the 1,800 s `is_replayed` cutoff, so this outage should yield 240 replayed rows, not 300.
- **Decision: turn on the mode schedule first; fault injection a few days later,** so a simulated outage and an injected fault are never confused while reviewing data.
- **Commits:** `ddb6002` (checkpoint results and the unplanned outage), `a297e3f` (mode schedule on, fault rate still 0), `5e9cb8f` (clock-sync fix).
- **Deployed `5e9cb8f` to the Pi.** The old process shut down cleanly after 36 scans (2,160 published, 2,160 acknowledged, 0 overflows, including the outage drain). The new one started at 22:41:27Z with `faults x0; mode schedule on`, connected, and wrote a single `startup` entry after the clock-sync and first-connect checks, with nothing false after it.
- **Issue: the persistent-journal step didn't work.** Creating `/var/log/journal` had no effect because Raspberry Pi OS ships a journald drop-in setting `Storage=volatile` to reduce SD-card wear. *Resolution:* a higher-numbered drop-in with `Storage=persistent` and `SystemMaxUse=100M`, then `journalctl --flush`.
- **Decision: a phase-close routine.** Every phase ends with an engineering-log entry committed after the phase's results, plus refreshed handoff documents (kept outside the repo) for the next chat and Claude Code session.
- **Tests:** edge 498, ingest 261.
- **Phase 3 closed.** The scheduled-outage feature is on but hasn't yet been observed on hardware; it will show up in the Phase 4 data.

---

## Open items

| Item | Target |
|---|---|
| Capture file arrival time in bronze (`_metadata.file_modification_time`) and base live `ingest_lag_seconds` on it | Phase 4, before silver |
| `veiled_presence` never fires: STEALTH has no midichlorian value, so `z_midi` is NULL | Phase 4 |
| Future-timestamp tolerance for `impossible_timestamp` (faults inject +24 h) | Phase 4 |
| Fault-injection period and reconciliation of `fault_injection.jsonl` against rejects | Phase 4 |
| Confirm the first scheduled outage on hardware (reason, 1–3 h duration, self-ending) | Early Phase 4 |
| Probe logs `MQTT disconnected` twice per disconnect; guard must reset on every connect | Phase 4 |
| Bridge closed its own connection once, cause undetermined | Watch; investigate if it recurs |
| Silver test: a replayed scan just under the 1,800 s cutoff (the 2026-09-29 19:00Z scan) | Phase 4 |
| Mosquitto ACL file group ownership (future versions will refuse it) | Any time |
| Docker Desktop can re-create LAN-wide Allow rules after updates | Recheck before each checkpoint |
| Service principal secret expiry (90 days) | Rotate before expiry |
| Broker persistence volume in `docker-compose.yml` | Phase 4 |
| Postgres column types in `_seeds.yml` | Local stack |
| Doc 06 severity bands and "active anomaly" definition | Phase 6 |
| TLS and a cloud-hosted broker | Later phase |

## Lessons that apply beyond this project

- **Test concurrency against the real library,** not a fake. The deadlock only existed where a real network thread and its internal lock were present.
- **Keep at least one independent source of truth for every claim.** The Pi's journal, its mode log, the broker log, the bridge log and the workspace SQL agreed or disagreed in useful ways several times; the broker log alone disproved a false outage.
- **Write-ahead to disk before sending.** It turned a deadlock, a forced kill and a power loss into non-events for the data.
- **A processing timestamp is not an arrival timestamp.** Measure lateness from when data landed.
- **Check that a test can fail before believing it passes,** both for mutation tests and for "confirmed" claims such as a file being gitignored.
- **Failure detection lags the failure.** Keepalive-based detection records when a loss was noticed, not when it began; say which one a timestamp means.
- **Rule out each endpoint before blaming the network.** Uptime, restart counts and the other client's connection history located today's outage in minutes.
