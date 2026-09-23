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
python-ulid
```

Stdlib `sqlite3`. No cloud SDK on the device — the bridge handles upload. Run as a systemd
service with `Restart=always`.

### Scan cycle

Every 15 minutes the probe sweeps all 60 planets and emits the full sweep as a single burst
sharing one `scan_id`. This produces one clean file per scan rather than a trickle of
four-event files.

### Reading generation

Parameters come from `dim_sector` — `midi_baseline`/`midi_sigma`, `kyber_baseline`/`kyber_sigma`,
`dark_baseline`/`dark_sigma`/`dark_spike_probability` — loaded from the seed CSV at startup.

Per planet, per scan:

```python
# Mean-reverting random walk. State persists across scans per planet per channel.
value = prev + normal(0, sigma) + 0.15 * (baseline - prev)
```

Mean reversion keeps values anchored without them being independent draws, which is what makes
the series look like a physical process rather than noise.

Dark side additionally rolls `dark_spike_probability` each scan. On a hit, ramp toward
`baseline + (4 to 7) * sigma` over 2–4 scans, hold 1–2 scans, then decay over 3–5 scans. That
ramp-hold-decay shape is what produces `sustained_scans >= 2` and fires a real emergency.

Expose a control topic `force/control/probe-01` accepting `{"inject": "spike", "sector_id": "...",
"signature": "sith_presence"}` so a demo can trigger a specific signature on demand. Implement it
by manipulating the three channels' targets to match the classification rule — that is how you
get reproducible demos and how the dashboard GIF gets made.

### Fault injection

During `CONNECTED`, **2–3% of readings are deliberately faulty**:

- ~1.2% — one channel null
- ~1.0% — one channel outside its valid range (e.g. `kyber_resonance` of 140)
- ~0.3% — `sector_id` not in `dim_sector`
- ~0.2% — `event_time` in the future

This is what feeds `silver.rejects`. Real sensors produce occasional bad readings regardless of
mode, and without this the quarantine path stays empty and one of the better demonstrations in
the project goes dark.

Make the rate configurable and log the injected faults locally so you can reconcile against the
rejects table — a test that the quarantine catches exactly what was injected, no more and no
less, is worth writing.

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

#### `BURST`

Entered automatically on reconnection from `DISCONNECTED`. Drains the SQLite buffer in
`event_time` order in batches of 500 with a short pause between batches. Live scanning continues
concurrently and is interleaved. Returns to `CONNECTED` when the buffer is empty.

**Rows are deleted from SQLite only after publish is confirmed.**

**Downstream:** this is the marquee feature. Replayed events arrive with `event_time` hours
behind `_ingest_ts`, producing large `ingest_lag_seconds` and `is_replayed = true`. Historical
windows in `gold.sector_reading` must be recomputed rather than duplicated, and
`gold.sector_baseline` must incorporate the recovered data on its next daily rebuild.

#### `STEALTH`

Limited connectivity or power. Scan interval extends to 60 minutes. Only `dark_side_activity` is
reported — `midichlorian_ppm` and `kyber_resonance` are null. Payload drops `sensor_temp_c`.

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

Cap at 100,000 rows — about 17 days of scans. On overflow drop oldest and emit a
`buffer_overflow` event so the loss is visible in the data rather than silent.

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

1. Subscribe to MQTT `force/telemetry/#`
2. Serve `POST /api/report` — inference then envelope construction
3. Validate envelope structure only, never payload contents. Structural failures go to a local
   dead-letter file
4. Accumulate and flush NDJSON to `/Volumes/force/raw/telemetry/dt=.../hh=.../` via the Files API
5. Expose health metrics — buffer depth, flush count, last flush — for the dashboard

### What it must not do

- **Never overwrite `event_time`.** If a device sends an event timestamped three hours ago, that
  is correct and load-bearing
- Never reorder events
- Never deduplicate — that is silver's job, and doing it here hides duplicate bugs

### Configuration

```yaml
mqtt:
  host: localhost
  port: 1883
  topics: ["force/telemetry/#"]
http:
  port: 8080
flush:
  max_bytes: 4194304
  max_seconds: 90
  on_complete_scan: true
inference:
  provider: groq
  model: ${INFERENCE_MODEL}
  api_key: ${GROQ_API_KEY}
databricks:
  host: ${DATABRICKS_HOST}
  token: ${DATABRICKS_TOKEN}
  volume_path: /Volumes/force/raw/telemetry
  user_agent: "Force_Balance_Pipeline/0.1 (+https://github.com/<you>/Force_Balance_Pipeline)"
```
