# 04 — Edge simulator and web intake

The sensors are simulated. The **device behavior** is not, and that is where the engineering
value sits.

---

## Intergalactic probe (Raspberry Pi 3)

### Hardware

The Pi 3 has a Cortex-A53 and is 64-bit capable. Install **64-bit Raspberry Pi OS Lite** — wheel
availability on `aarch64` is far better than on 32-bit `armv7l`, which otherwise forces source
builds.

The real constraint is 1 GB RAM shared with the GPU. Dependencies:

```
paho-mqtt
```

Stdlib `sqlite3`. No cloud SDK on the device — the bridge handles upload. ULIDs come from
`forcesim`'s own stdlib implementation, which is seedable: a reproducible backfill needs seeded
randomness, and `python-ulid` draws from OS randomness. Run as a systemd service with
`Restart=always`.

### Scan cycle

Every 15 minutes the probe sweeps all 60 planets and emits the full sweep as a single burst
sharing one `scan_id`. This produces one clean file per scan rather than a trickle of
four-event files.

**Clock.** The Pi 3 has no real-time clock, so `event_time` is trusted only once NTP has synchronised
(`timedatectl` reports `NTPSynchronized=yes`). A scan due before that is skipped and counted in the probe's log
(`clock_unsynced`): it is not stamped with a guessed time and not buffered, so it appears as a gap. The probe keeps the last
stamped `event_time` in its state directory and refuses a scan whose boundary is not after it, so a clock that steps backwards
cannot re-stamp history. Scans align to :00, :15, :30 and :45 UTC (to :00 in `STEALTH`).

### Reading generation

Parameters come from `dim_sector` — `midi_baseline`/`midi_sigma`, `kyber_baseline`/`kyber_sigma`,
`dark_baseline`/`dark_sigma`/`dark_spike_probability` — loaded from the seed CSV at startup.

Per planet, per scan:

```python
# Mean-reverting AR(1) walk, calibrated so the stationary SD equals the enrichment sigma.
# State persists across scans per planet per channel. Each series starts from a stationary draw,
# normal(baseline, sigma), not from the baseline itself.
K = 0.15                                    # reversion rate; phi = 1 - K = 0.85
STEP_SD = sigma * math.sqrt(K * (2 - K))    # = sigma * sqrt(1 - phi**2) = 0.5268 * sigma
value = prev + normal(0, STEP_SD) + K * (baseline - prev)
```

Mean reversion keeps values anchored without them being independent draws, which is what makes
the series look like a physical process rather than noise. The step noise is scaled so the
long-run standard deviation of each series equals the planet's `*_sigma` from `dim_sector`. With
step noise equal to sigma itself the long-run SD would be 1.9 x sigma, and every z-score in the
warehouse would be 1.9 x too small. Readings are clamped to the valid ranges in doc 02; the walk
state is not clamped, so a clamped reading does not feed back into the next step.

Dark side additionally rolls for a spike episode each scan with probability
`dark_spike_probability / 96`. The seed value is read as a **per-day** episode rate (96 scans a
day). The seed was generated under a per-scan framing; read that way it would produce about 5,000
episodes galaxy-wide over the 90-day backfill, which contradicts a quiet dark channel with rare
spikes. The seed is frozen, so the interpretation lives here (doc 08 notes it). A planet does not
roll while it is already in a spike episode, or while an injected event (a backfill emergency or a
control-topic injection) is active on it. On a hit, ramp toward `baseline + (4 to 7) * sigma` over
2–4 scans, hold 1–2 scans, then decay over 3–5 scans. That ramp-hold-decay shape is what produces
`sustained_scans >= 2` and fires a real emergency.

Two planets cannot fire one. Mustafar and Dathomir have dark baselines of 95 and 90 (sigma 6 and 7),
so a spike to `baseline + (4 to 7) * sigma` would reach 118 to 139, above the dark ceiling of 100
(doc 02). Their readings clamp at 100 and their spike episodes stay below the emergency threshold
(doc 03): the highest sustained composite of any of their 13 episodes in the backfill is 4.04. That is
a property of the frozen seed and the valid range, not a fault of the generator.

Expose a control topic `force/control/probe-01` accepting `{"inject": "spike", "sector_id": "...",
"signature": "sith_presence"}` so a demo can trigger a specific signature on demand. Implement it
by manipulating the three channels' targets to match the classification rule — that is how you
get reproducible demos and how the dashboard GIF gets made.
The topic also accepts `{"mode": "DISCONNECTED" | "STEALTH" | "CONNECTED", "for_seconds": 2700}` to force a mode for a period.
An injection defaults to ramp 2, hold 4 and decay 3 scans and may override them (`ramp`, `hold`, `decay`). Only the `operator`
user may publish to the topic (broker ACL); `probe_ctl.py` asks for that password at a prompt and never stores it.
`veiled_presence` is not injectable in Phase 3.
An injection produces an emergency-level reading; the control message carries no severity field
yet. Targets are computed, not listed, in `edge/forcesim/signatures.py`, and shared with the
backfill's historical emergencies. The target of a signature is the smallest whole-sigma point whose
reading classifies as that signature and whose nominal composite score (doc 03; under the calibrated
walk, z equals the target in sigmas) exceeds the doc 03 emergency threshold times a margin
`M = 1 / (1 - 3e)`, about 1.06. Here `e` is the sampling error of one 90-day window's standard
deviation, `sqrt((1 + phi^2) / (2 (1 - phi^2) n))` with phi = 0.85 and n = 8,640 scans, which is
1.9%. The margin lets an injected emergency still clear the threshold against a baseline whose SD
was estimated three standard errors high (a 0.13% chance per event). It is a margin on injection
intent, meaning the score the injection is meant to reach; it is not a margin on the signature's
region, whose boundary is never widened. Only channels with a directional condition in the
signature's rule (`>` or `<`) move; channels the rule bounds toward zero (`ABS(z) < c`), channels it
does not mention, and guard channels stay at 0. Ties go to the smaller largest deviation, then to
loading midichlorian, the channel with the widest valid range. The threshold comes from doc 03, so
tuning it recomputes every target, and a severity can be added later as a different threshold. An
injection holds at least 2 scans, because a disturbance needs 2 consecutive scans above the
threshold (doc 03).
During the hold phase the step noise on the channels that define the signature's region (its own
rule, plus any channel that guards against an earlier rule) is zero, so the value equals its
target exactly; without that, a target just inside a region would classify as itself only part of
the time. Other channels keep their ambient noise. An injection the planet cannot support (a target
outside a channel's valid range, or `civil_unrest` on a planet under 1e9 population) is refused.

**Backfill window.** The 90-day backfill (doc 07, Phase 2) runs this same generator. Its window is
`[start, end)` with `start = end - 90 days`, one scan per 15-minute boundary from `start` up to the
boundary before `end`. `end` is the last 15-minute UTC boundary before the earliest live probe event
already in bronze, not before generation time, so synthetic and live readings for `probe-01` never
overlap. `end` and the earliest live event time are explicit inputs, recorded in the backfill
manifest. The generator refuses a window that ends after that boundary, and refuses to write any
synthetic `event_time` at or after the earliest live `event_time`.

**Backfill timing and texture.** Each backfill row carries the `synthetic_ingest_ts` it would have had
live (doc 02). A normal scan reaches bronze 4 to 5 seconds after it starts. A `DISCONNECTED` gap keeps
every row: when the link returns, 5 seconds before the next scan, the buffered events are published in
batches of 500 in `event_time` order, 10 seconds apart, and each lands 1 second after its batch is
published. They keep the mode `DISCONNECTED`; the scan taken while the drain is still running has the
mode `BURST` (doc 02). The texture (drift, injected emergencies, the slow riser and the gaps) is in
`edge/backfill_texture.json`, and the manifest records its hash. The slow riser is checked as a trend:
it passes if the mean composite score (doc 03) of its last day is under the anomaly threshold. A single
scan is not the check, because ambient noise alone puts scans of every planet above that threshold.

### Fault injection

During `CONNECTED`, **2–3% of readings are deliberately faulty**:

- ~1.2% — one channel null
- ~1.0% — one channel outside its valid range (e.g. `kyber_resonance` of 140)
- ~0.3% — `sector_id` not in `dim_sector`. The injected id must never match any valid
  `sector_id`, including `uncharted` (the SWAPI `unknown` planet)
- ~0.2% — `event_time` in the future

Each reading is drawn independently at these rates. A faulty reading replaces the clean one, so a scan stays 60 events. A null or
out-of-range fault hits any one of the three science channels; an unknown `sector_id` is a unique generated id, so no two faults
share one; a future `event_time` is 24 hours ahead. Nothing is injected in `DISCONNECTED`, `BURST` or `STEALTH`.

This is what feeds `silver.rejects`. Real sensors produce occasional bad readings regardless of
mode, and without this the quarantine path stays empty and one of the better demonstrations in
the project goes dark.

Make the rate configurable and log the injected faults locally so you can reconcile against the
rejects table — a test that the quarantine catches exactly what was injected, no more and no
less, is worth writing.
`--fault-rate` scales all four rates together (default 1.0; 0 turns injection off). Each fault is appended to
`fault_injection.jsonl` in the probe's state directory before its event is buffered, one JSON object per line: `event_id`,
`scan_id`, `fault`, `expected_reject_reason` (a doc 03 reason), `event_time` and `sector_id` as emitted, the channel, the original
and injected values, and `logged_utc`. The count of rejects must equal the count in this log. The log is pulled off the Pi by hand
(`scp`) and never travels on the telemetry topic; how it reaches Databricks for the reconciliation is decided in Phase 4.

### The four modes

#### `CONNECTED`

Normal. Full 60-planet sweep every 15 minutes, published immediately via MQTT. Fault injection
active.

**Downstream:** the happy path, plus the rejects path.

#### `DISCONNECTED`

No connectivity. Scanning continues on schedule; every reading is written to local SQLite and
nothing is published.

**Downstream:** nothing arrives. The dashboard's source health panel should show the probe going
stale. Tests the "alive but silent" case.

Default schedule: 1–3 hours, roughly twice a day. Local demo: 3–5 minutes.

The schedule is a simulated outage. It is **off by default until the Phase 3 checkpoint passes**, then on. A mode can also be
forced by an operator (control topic, below), and `DISCONNECTED` is entered on a real loss of the broker (the client disconnects,
or QoS 1 publishes stay unacknowledged past a timeout). All three run the same buffering code. A reading taken before the probe
notices a real loss carries the mode it had when taken (`CONNECTED`) but stays buffered until acknowledged. Every mode transition
is appended to `mode_transitions.jsonl` (`ts_utc`, `from`, `to`, `reason`).

#### `BURST`

Entered automatically on reconnection from `DISCONNECTED`. Drains the SQLite buffer in
`event_time` order in batches of 500 with a short pause between batches. Live scanning continues
concurrently and is interleaved. Returns to `CONNECTED` when the buffer is empty.

**Rows are deleted from SQLite only after publish is confirmed.**

A batch of 500 is published, all 500 are acknowledged (QoS 1 PUBACK) and deleted in one transaction, and the probe then pauses
10 seconds, the same pause the backfill models. A batch that is not fully acknowledged stays in the buffer and is resent, so
delivery is at-least-once: a duplicate `event_id` is possible, and silver deduplicates. Live scans keep their schedule while the
drain runs and are published with mode `BURST`.

**Downstream:** this is the marquee feature. Replayed events arrive with `event_time` hours
behind `_ingest_ts`, producing large `ingest_lag_seconds` and `is_replayed = true`. Historical
windows in `gold.sector_reading` must be recomputed rather than duplicated, and
`gold.sector_baseline` must incorporate the recovered data on its next daily rebuild.

#### `STEALTH`

Limited connectivity or power. Scan interval extends to 60 minutes. Only `dark_side_activity` is
reported — `midichlorian_ppm` and `kyber_resonance` are null. Payload drops `sensor_temp_c`.
The sweep is on the hour, covers all 60 planets and carries one `scan_id`. `midichlorian_ppm` and `kyber_resonance` are JSON
`null`; `sensor_temp_c` is omitted; `battery_pct` stays. A `STEALTH` probe that loses the broker keeps its `STEALTH` cadence and
label, buffers, and drains with `BURST` when the broker returns.

**Downstream:** partial readings. These must route to `silver.probe_reading` with
`is_partial = true` and `channels_present = 1`, **not** to rejects. The composite score scales
via the `SQRT(3/channels_present)` term. The `veiled_presence` signature exists specifically for
dark-side anomalies detected under partial coverage.

Getting the STEALTH-versus-fault distinction right in the silver validation logic is a genuine
piece of pipeline engineering. It is the difference between "nulls are bad" and "nulls mean
different things depending on context."

### Local buffer

```sql
CREATE TABLE IF NOT EXISTS buffered_events (
  event_id   TEXT PRIMARY KEY,
  event_time TEXT NOT NULL,
  scan_id    TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_time ON buffered_events(event_time);
```

`scan_id` is `NOT NULL` in this table, so a housekeeping row (which has no scan_id) is stored here as the empty string; `payload`
keeps the real JSON `null` unchanged, and that is what a replay publishes.

Cap at 100,000 rows — about 17 days of scans. On overflow drop oldest and emit a
`buffer_overflow` event so the loss is visible in the data rather than silent.

`payload` holds the complete envelope as it will be published (compact JSON), so a replay is byte-identical and keeps `event_time`
and `mode`. Every reading is written before it is published (one transaction per scan) and deleted when its PUBACK arrives, in
every mode; this is the durability layer of doc 02. The database uses WAL mode with `synchronous=FULL` and lives in the service's
state directory, not in a tmpfs: about 24 KB per scan and about 200 commits a day. The cap is configurable for tests
(`--buffer-cap`, default 100000). The `buffer_overflow` event is a housekeeping event (doc 02).

### Deployment on the Pi

The Pi runs from a sparse git clone of the repository (`Force_Balance_Pipeline/edge`, `Force_Balance_Pipeline/warehouse/dbt/seeds`
and `Force_Balance_Pipeline/infra/pi`, so the unit files and the deploy script travel with the clone and nothing is `scp`'d
separately) pinned to a commit SHA that the probe logs at start, in a venv with `paho-mqtt` (pinned in `edge/requirements.txt`).
Unit files live under `infra/pi/`: `force-probe.service` runs as a dedicated user with `Restart=always`, after `time-sync.target` and
`network-online.target`, with its state (buffer database and logs) in `/var/lib/force-probe/`. Configuration and the MQTT password
are read from `/etc/force-probe/probe.env` (root-owned, mode 0600), which is never in the repository; `infra/pi/probe.env.example`
shows the keys with empty values.

---

## Web intake (browser)

Replaces the earlier survey-station concept. A human describes what they observed; a model infers
sensor-equivalent values and a relevance score.

### Interface

- Planet picker, populated from `dim_sector`
- Free-text description box
- Submit → backend inference → confirmation showing the inferred values and relevance, so the
  reporter sees what the system made of their report
- Recent reports list for that planet

### Inference backend

**The model call must happen server-side**, not in the browser — an API key in client JS is not
an option. Fold the endpoint into the collector bridge or run it as a small sidecar.

**OPEN:** temperature and thinking level for report inference. Google's Gemini 3 guidance is to
keep temperature at the default 1.0; decide both in Phase 5, with the intake fixture suite.

```
POST /api/report
  { "sector_id": "coruscant", "description": "..." }
→ { "relevance_score": 0.85,
    "midichlorian_ppm": 16800, "kyber_resonance": 40.1, "dark_side_activity": 78.5,
    "inference_rationale": "...", "inference_model": "..." }
```

The endpoint builds the full envelope with `source_type = "report"` and hands it to the bridge
like any other event.

### Inference prompt

```
You assess reports of Force-related phenomena. Given a planet and a witness description,
infer three sensor-equivalent values and a relevance score.

MIDICHLORIAN DENSITY (1000-25000 ppm) - ambient Force-sensitive life
KYBER RESONANCE (0-100) - crystalline Force resonance
DARK SIDE ACTIVITY (0-100) - ambient dark side presence

You will be given the planet's normal baseline values. Infer values relative to that baseline:
an unremarkable report should return values close to baseline.

RELEVANCE (0.0-1.0) - how much this description indicates a genuine Force disturbance.
  Weather, ordinary events, mundane observations: 0.0-0.15
  Vague unease, odd feelings, unexplained phenomena: 0.2-0.5
  Specific Force phenomena, unidentified Force users: 0.6-0.8
  Named dark side figures, overt Force violence, Sith artifacts: 0.85-1.0

Be conservative. Most reports are not emergencies. A high relevance score triggers a real
emergency response, so reserve scores above 0.7 for descriptions with specific, credible
Force-related content.

Return ONLY JSON: relevance_score, midichlorian_ppm, kyber_resonance, dark_side_activity,
inference_rationale.
```

Passing the planet's baseline into the prompt is what keeps inferred values on the same scale as
probe readings. Without it the model invents absolute numbers and the z-scores are meaningless.

### This model needs its own fixture suite

It is a second LLM with a different job from Yoda's, and it gates emergency creation. Build
`intake/fixtures/` alongside the agent's:

| Description | Assertion |
|---|---|
| "It rained today" | `relevance < 0.15` |
| "Saw Darth Vader walking through the market" | `relevance > 0.85`, `dark_side_activity` well above baseline |
| "My speeder broke down" | `relevance < 0.15` |
| "A cold feeling near the old temple, lights flickered" | `0.3 < relevance < 0.7` |
| "Two people fighting with laser swords" | `relevance > 0.8` |
| "" (empty) | rejected before inference |
| 5000-character rambling | no crash, bounded values |
| Prompt-injection attempt in the description | values stay in range, relevance stays low |

That last one matters — this endpoint takes untrusted user input and feeds a model whose output
drives automated action. Assert that output values are clamped to valid ranges in code
regardless of what the model returns, and that `relevance_score` cannot be talked upward.

### Client behavior

Buffer submissions to `IndexedDB` on network failure and replay on reconnect — the same
store-and-forward pattern as the probe, which keeps the late-arriving path exercised from both
sources.

---

## Collector bridge

Runs on the GCP e2-micro.

### Responsibilities

1. Subscribe to MQTT `force/telemetry/#` with a fixed client id, a persistent session and QoS 1, so
   the broker queues messages while the bridge restarts
2. Serve `POST /api/report` — inference then envelope construction
3. Validate envelope structure only, never payload contents. Structural failures go to a local
   dead-letter file
4. Accumulate and flush NDJSON to `/Volumes/force/raw/telemetry/dt=.../hh=.../` via the Files API
5. Expose health metrics — buffer depth, flush count, last flush — for the dashboard. Phase 2 logs
   these counters to the console; the HTTP endpoint is built in Phase 7 (doc 07), when the
   dashboard needs it.

### What it must not do

- **Never overwrite `event_time`.** If a device sends an event timestamped three hours ago, that
  is correct and load-bearing
- Never reorder events
- Never deduplicate — that is silver's job, and doing it here hides duplicate bugs

**Phase 3 staging.** The Phase 3 broker runs in Docker on the developer's desktop, bound to the desktop's LAN address and
`127.0.0.1` (never `0.0.0.0`), with `allow_anonymous false`, a password file kept outside the repository, and per-user topic
permissions (`infra/mosquitto/acl`): `probe-01` writes `force/telemetry/probe-01` and reads `force/control/probe-01`;
`force-bridge` reads `force/telemetry/#`; `operator` writes `force/control/probe-01`. A Windows Firewall rule admits TCP 1883 from
the Pi's address only. There is no TLS, so the passwords cross the LAN in clear text: accepted for staging.

**OPEN:** TLS, and the e2-micro (broker and bridge, doc 01), move together to a later phase. The local-only config
(`allow_anonymous true`, `127.0.0.1`) is for single-machine development.

### Configuration

The bridge takes command-line flags and environment variables. There is no config file, so nothing
on the e2-micro parses YAML. The defaults below are checked against the code by
`ingest/tests/test_cli.py`.

| Flag | Default | Meaning |
|---|---|---|
| `--mqtt-host` | `localhost` | broker host |
| `--mqtt-port` | `1883` | broker port |
| `--topic` | `force/telemetry/#` | subscription, at QoS 1 |
| `--client-id` | `force-bridge` | fixed id; with `clean_session=false` the broker keeps the session and queues QoS 1 messages while the bridge is down (Mosquitto `max_queued_messages 50000`, about 8 days of scans) |
| `--max-bytes` | `4194304` | flush when the buffer reaches this size |
| `--max-seconds` | `90` | flush when the oldest buffered event is this old |
| `--scan-size` | `60` | events in a complete scan, which flushes as its own file |
| `--volume-path` | `/Volumes/force/raw/telemetry` | the landing zone (workspace mode) |
| `--oauth-scope` | `files` | the scope in the OAuth token request (workspace mode); the force-bridge secret is scoped to the Files API, and `all-apis` is for an unscoped secret |
| `--local-dir` | none | write here instead of the volume (local demo and tests) |
| `--dead-letter` | `logs/bridge_dead_letter.ndjson` | structural failures, and events that could not be uploaded |
| `--env-file` | `.env.bridge` | the credentials file, below |

Environment, in workspace mode: `DATABRICKS_HOST`, `BRIDGE_DATABRICKS_CLIENT_ID` and
`BRIDGE_DATABRICKS_CLIENT_SECRET`, the files-scoped service principal of doc 05, exchanged at
`/oidc/v1/token`. Locally they come from `.env.bridge` (gitignored; template `.env.bridge.example`),
which holds only those three keys; values already in the process environment win, and on the
e2-micro they come from Secret Manager. The bridge never uses `DATABRICKS_TOKEN` and refuses an env
file that contains it. Every outbound request sends the project User-Agent,
`"Force_Balance_Pipeline/0.1 (+https://github.com/jivejong/JiveRepo/tree/main/Force_Balance_Pipeline)"`.
The report endpoint (Phase 5) adds `INFERENCE_MODEL` and `GEMINI_API_KEY` (local `.env`, Secret
Manager on the bridge) and an `--http-port` flag (default `8080`).

Against an authenticated broker (Phase 3 staging) the bridge also reads `BRIDGE_MQTT_USERNAME` and `BRIDGE_MQTT_PASSWORD`, from
the process environment or from `.env.mqtt` (gitignored, template `.env.mqtt.example`), never from `.env.bridge`, which keeps
its three keys.
