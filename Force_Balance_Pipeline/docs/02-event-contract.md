# 02 — Event contract

The interface between the edge tier and the platform. Settle it before writing device code.

## Design principle

**One envelope, many payloads.** Both sources — the probe and the web intake — emit the same
outer structure with a source-specific `payload`. One Auto Loader stream, one bronze table. The
split happens at silver.

---

## Envelope

```json
{
  "event_id":       "01K4X8QP2M7YRZ9NBVCDXWQF3T",
  "source_id":      "probe-01",
  "source_type":    "probe",
  "schema_version": 1,
  "event_time":     "2026-09-05T14:22:07.412Z",
  "mode":           "CONNECTED",
  "scan_id":        "01K4X8QP2K",
  "sector_id":      "tatooine",
  "is_synthetic":        false,
  "synthetic_ingest_ts": null,
  "payload":        { }
}
```

| Field | Type | Notes |
|---|---|---|
| `event_id` | string | ULID. Primary dedup key |
| `source_id` | string | `probe-01`, `web-a3f2` |
| `source_type` | string | `probe` \| `report`. **Load-bearing** — see below |
| `schema_version` | int | Increment on breaking payload change |
| `event_time` | string | ISO 8601 UTC. **When the reading was taken**, not when sent |
| `mode` | string | Probe operating mode when the reading was taken. A reading buffered while `DISCONNECTED` keeps that value when drained; scans taken during a drain are `BURST`. Null for reports |
| `scan_id` | string | Groups the 60 readings of one sweep. Null for reports |
| `sector_id` | string | SWAPI planet slug. FK to `dim_sector` |
| `is_synthetic` | boolean | True only for rows written by the backfill generator. Always present; false for live probe and report events |
| `synthetic_ingest_ts` | string | ISO 8601 UTC. The ingest time a backfill row simulates. Set only where `is_synthetic`; always present, null otherwise |
| `payload` | object | Source-specific. `VARIANT` in bronze |

### `source_type` is the most important field in the system

`probe` readings are telemetry. They contribute to rolling baselines and can trigger emergencies.

`report` readings are human sightings with model-inferred values. **They never contribute to
baselines.** They can trigger emergencies, but incidents they produce are tagged so that the
agent and the dashboard know they are acting on a sighting rather than on sensor data.

If inferred values entered the baseline, one dramatic description would permanently shift a
planet's statistical profile and corrupt every subsequent z-score for that sector. Enforce this
with a `WHERE source_type = 'probe'` filter in the baseline model and a dbt test asserting no
report-sourced rows appear in it.

`event_time` is the other load-bearing field. A probe replaying a `DISCONNECTED` buffer produces
events whose `event_time` is hours behind `_ingest_ts`. That gap is the point. The bridge must
never overwrite it with send time.

Backfill rows are written straight to the volume, so their real `_ingest_ts` is the upload time,
not a plausible arrival time. Silver measures ingest lag for `is_synthetic` rows from
`synthetic_ingest_ts`, and for every other row from `_ingest_ts`. Both fields are always emitted,
because Auto Loader's `rescue` schema-evolution mode sends a field missing from the inferred schema to
`_rescued_data` instead of a column.

---

## Payload — probe

```json
{
  "midichlorian_ppm":   14200.5,
  "kyber_resonance":    62.4,
  "dark_side_activity": 11.8,
  "sensor_temp_c":      41.2,
  "battery_pct":        87
}
```

| Field | Type | Valid range | Nullable |
|---|---|---|---|
| `midichlorian_ppm` | float | 0 – 30000 | in `STEALTH` and fault injection |
| `kyber_resonance` | float | 0 – 100 | in `STEALTH` and fault injection |
| `dark_side_activity` | float | 0 – 100 | fault injection only |
| `sensor_temp_c` | float | -50 – 120 | yes |
| `battery_pct` | int | 0 – 100 | no |

In `STEALTH` mode the probe reports `dark_side_activity` only; the other two channels are null.
This is expected behavior, not a fault — silver must route these to a partial-reading path
rather than to rejects, and the composite score must be computable from available channels.

## Payload — report (web intake)

```json
{
  "description":         "Saw a figure in black robes near the eastern spaceport. The air felt cold.",
  "relevance_score":     0.85,
  "midichlorian_ppm":    16800.0,
  "kyber_resonance":     40.1,
  "dark_side_activity":  78.5,
  "inference_model":     "<model-id>",
  "inference_rationale": "Black robes and cold sensation are strong dark side indicators",
  "reporter_id":         "web-a3f2"
}
```

| Field | Type | Notes |
|---|---|---|
| `description` | string | The human's free text, verbatim. Never modified |
| `relevance_score` | float | 0.0–1.0, model-assigned. "Rainy day" ≈ 0.05, "Darth Vader sighting" ≈ 0.95 |
| `midichlorian_ppm`, `kyber_resonance`, `dark_side_activity` | float | Model-inferred, same units as probe |
| `inference_model` | string | Which model produced these values. Required for audit |
| `inference_rationale` | string | Why the model inferred what it did |
| `reporter_id` | string | Browser session identifier |

The original `description` must survive intact all the way to `gold.disturbance` and into the
agent's context. It is the most useful single field the agent receives on a report-sourced
incident, and a system that discards it in favor of the numbers has thrown away the signal.

`relevance_score` gates escalation. Low-relevance reports are stored and visible but do not
trigger emergencies. See doc 03 for thresholds.

---

## Landing zone layout

```
/Volumes/force/raw/telemetry/
  dt=2026-09-05/
    hh=14/
      probe-01-01K4X8QP2M7YRZ9NBVCDXWQF3T.ndjson
      web-a3f2-01K4X8QT9F3BCDPQRSTUVWXY.ndjson
```

Hive-style `dt=` and `hh=` prefixes, inferred by Auto Loader as partition columns. Use ingest
wall-clock time for the prefix — it describes when the file was written, not what's in it.
This holds for backfill files too: their `dt=` and `hh=` name the day and hour they were uploaded.

Filename is `{source_id}-{ulid}.ndjson`. Immutable and uniquely named, so a retrying bridge
cannot produce a duplicate and Auto Loader's file tracking prevents reprocessing.

One JSON object per line. No wrapping array.

---

## Flush policy

**The probe emits its full 60-planet sweep as one burst at the top of each 15-minute cycle.**
One sweep produces one file.

This is deliberate. Spreading 60 readings evenly across 15 minutes would mean one event every
15 seconds, and a 60-second flush timer would produce files containing four events each — a
small-file problem from day one. Bursting the sweep gives clean file boundaries that match the
mental model of a scan.

Bridge flush conditions:

- 4 MB accumulated, **or**
- 90 seconds elapsed since the first buffered event, **or**
- a complete `scan_id` has been received (all 60 planets)

The third condition is what normally fires. The other two are safety nets for `BURST` mode and
for reports, which arrive irregularly.

Reports flush on the 90-second timer since they are low-volume and human-paced.

If the bridge crashes with events buffered, those events are lost. Acceptable: the probe's SQLite
buffer is the durability layer and clears only on confirmed publish.

---

## Versioning

`schema_version` starts at 1. Increment only on a breaking payload change — removed field,
renamed field, changed unit. Adding a nullable field is not breaking; the `VARIANT` bronze
column absorbs it without migration.

When a version bumps, silver models keep handling the old version until no unprocessed data of
that version remains.
