# Phase 2 results

Checks for the ingestion path and the 90-day backfill (doc 07, Phase 2), recorded from command
output only. A cell stays empty until the output that supports it has been pasted in. Nothing here
is inferred. Live half first, backfill half after it.

Queries: `ingest/phase2_checkpoint.sql` (the query labels below are the file's). Run them in the
workspace SQL editor as yourself, not as `force-bridge`.

## Live half: 3 scans through the bridge into `force.bronze.events`

| # | Check | Expected | Actual | Evidence |
|---|---|---|---|---|
| L1 | Deviation from doc 07's checkpoint | 3 scans at the real 15-minute cadence (45 minutes) | **3 scans at a 60 s cadence.** The 15-minute cadence (quarter-hour alignment, an idle bridge between scans, hour-boundary paths) is exercised in Phase 3. | Deviation note in doc 07, Phase 2, dated 2026-09-26. The 3 files stay; nothing was deleted or rerun. |
| L2 | First query: is `payload` a VARIANT with numeric fields? | `payload_type` = `variant`; every field BIGINT, DECIMAL or DOUBLE; `payload:midichlorian_ppm::double` and `payload:battery_pct::int` return numbers | `payload_type` = `variant` on all 5 rows. Every field is numeric (BIGINT or DECIMAL). `midi` returns numbers (17557.5, 6080.9, 3986.8, 6250.7, 9396.8) and `battery` returns 97 on all 5 rows. Across all rows the schema is `OBJECT<battery_pct: BIGINT, dark_side_activity: DECIMAL(4,1), kyber_resonance: DECIMAL(3,1), midichlorian_ppm: DECIMAL(6,1), sensor_temp_c: DECIMAL(3,1)>`. **Pass**, after the fix in L3. | Output below, "L2 detail". |
| L3 | The notebook's `payload` line | `parse_json(to_json(payload))` (as first drafted) | **Failed at analysis** on the first run: `DATATYPE_MISMATCH.INVALID_JSON_SCHEMA`, `to_json(payload)` given the input schema `STRING`. With `inferColumnTypes = false`, `payload` arrives as a STRING of raw JSON. The line is now `F.expr("try_parse_json(payload)")` in the notebook and in doc 05, so a malformed payload becomes NULL in bronze instead of failing the stream. | Doc 05, "The first query", recorded result. The run reported by the user, with the error text above. |
| L4 | Query 0c: NULL payloads | `null_payloads` = 0 | The 0c query itself was not pasted. Query (c) returned `null_readings` = 0 over every row (see L7); a row with a NULL `payload` would have all three channel reads NULL and be counted there, so no row has a NULL payload. | Query (c) output, L7 detail. |
| L5 | (a) 180 rows from 3 live scans | n = 180, scans = 3, sectors = 60, distinct events = 180 | **Pass.** n = 180, scans = 3, sectors = 60, distinct_events = 180. | Query (a) output, L5 detail. |
| L6 | (b) partitions | Partition column `dt`; one `dt`/`hh` group for the 3 live files | **Partly recorded.** One group: `dt` = 2026-09-26, `hh` = 14, n = 180, files = 3. The table was created with `partitionBy` = `["dt"]`. `DESCRIBE TABLE` (`dt` date, `hh` int, `payload` variant) and the dt/hh-versus-path mismatch query were not pasted. | Query (b) output and the version 0 row of `DESCRIBE HISTORY`, L5 and L8 detail. |
| L7 | (c) payload queryable, nothing missing or rescued | 0 rows with a NULL channel; 0 rows with `_rescued_data`; 0 rows with `is_synthetic` and `synthetic_ingest_ts` disagreeing | **Pass.** `null_readings` = 0, `rescued` = 0, `bad_flags` = 0. | Query (c) output, L7 detail. |
| L8 | (d) exactly-once | Rerun with no new files adds no write with rows > 0; 0 duplicate `event_id` | **Pass.** `DESCRIBE HISTORY` shows two versions only: 0 (`CREATE TABLE`) and 1 (`STREAMING UPDATE`, `numOutputRows` = 180, `numAddedFiles` = 1); the rerun added no version. The duplicate `event_id` query returned 0. `n` and `files` from before the rerun were not pasted for a side-by-side. | `DESCRIBE HISTORY` and duplicate query output, L8 detail. |
| L9 | (e0) earliest live event | 2026-09-26T14:27:00.000Z, which fixes the backfill window's end at 2026-09-26T14:15:00Z | **Confirmed.** `earliest_live` = 2026-09-26T14:27:00.000+00:00, the same instant. The local backfill was generated with `--earliest-live 2026-09-26T14:27:00.000Z`, so it needed no regeneration (window end 2026-09-26T14:15:00.000Z, 8,640 scans). | Query (e0) output. |

### L5, L6, L7 detail

Query (a), live rows (`WHERE NOT is_synthetic`):

| n | scans | sectors | distinct_events |
|---|---|---|---|
| 180 | 3 | 60 | 180 |

Query (b), all rows:

| dt | hh | n | files |
|---|---|---|---|
| 2026-09-26 | 14 | 180 | 3 |

Query (c), all rows:

| null_readings | rescued | bad_flags |
|---|---|---|
| 0 | 0 | 0 |

### L8 detail

`DESCRIBE HISTORY force.bronze.events LIMIT 5`, columns shortened:

| version | timestamp | operation | operation parameters | metrics |
|---|---|---|---|---|
| 1 | 2026-09-26T15:27:34.000+00:00 | STREAMING UPDATE | outputMode Append, epochId 0 | numOutputRows 180, numOutputBytes 11631, numAddedFiles 1, numRemovedFiles 0; `isBlindAppend` true; readVersion 0 |
| 0 | 2026-09-26T15:27:22.000+00:00 | CREATE TABLE | partitionBy `["dt"]`, isManaged true | none |

Both versions were written by the notebook run (engine Databricks-Runtime 19.6.x, aarch64, Photon). The
duplicate `event_id` query (`SELECT count(*) FROM (... GROUP BY event_id HAVING count(*) > 1)`) returned 0.

### L9 detail

Query (e0), `SELECT min(event_time) FROM force.bronze.events WHERE NOT is_synthetic`:
`2026-09-26T14:27:00.000+00:00`.

### L2 detail

Query 0 output, as pasted (5 rows, live rows only, ordered by `event_time`):

| event_id | sector_id | payload_type | payload_schema | midi | battery |
|---|---|---|---|---|---|
| 01M3F1SES091VBV7AHSDGSHBZ4 | alderaan | variant | `OBJECT<battery_pct: BIGINT, dark_side_activity: DECIMAL(2,1), kyber_resonance: DECIMAL(3,1), midichlorian_ppm: DECIMAL(6,1), sensor_temp_c: DECIMAL(3,1)>` | 17557.5 | 97 |
| 01M3F1SETJ7HMB5JDECTCWQY9P | aleen_minor | variant | `OBJECT<battery_pct: BIGINT, dark_side_activity: DECIMAL(3,1), kyber_resonance: DECIMAL(3,1), midichlorian_ppm: DECIMAL(5,1), sensor_temp_c: DECIMAL(3,1)>` | 6080.9 | 97 |
| 01M3F1SEW4PCJWXSM2FYNJHPD3 | bespin | variant | `OBJECT<battery_pct: BIGINT, dark_side_activity: DECIMAL(3,1), kyber_resonance: DECIMAL(2,1), midichlorian_ppm: DECIMAL(5,1), sensor_temp_c: DECIMAL(3,1)>` | 3986.8 | 97 |
| 01M3F1SEXPCYZG7BX2WTJG68N9 | bestine_iv | variant | `OBJECT<battery_pct: BIGINT, dark_side_activity: DECIMAL(3,1), kyber_resonance: DECIMAL(3,1), midichlorian_ppm: DECIMAL(5,1), sensor_temp_c: DECIMAL(3,1)>` | 6250.7 | 97 |
| 01M3F1SEZ80RW00YKYCEEDKAT6 | cato_neimoidia | variant | `OBJECT<battery_pct: BIGINT, dark_side_activity: DECIMAL(2,0), kyber_resonance: DECIMAL(3,1), midichlorian_ppm: DECIMAL(5,1), sensor_temp_c: DECIMAL(3,1)>` | 9396.8 | 97 |

Query 0b (`schema_of_variant_agg(payload)` over every row):
`OBJECT<battery_pct: BIGINT, dark_side_activity: DECIMAL(4,1), kyber_resonance: DECIMAL(3,1), midichlorian_ppm: DECIMAL(6,1), sensor_temp_c: DECIMAL(3,1)>`.

Observation, cause not settled here: in row 5 `dark_side_activity` has the type `DECIMAL(2,0)` (a whole
number), where the other rows show one decimal place. Every field is still numeric, so the pass criterion
holds, and the aggregate schema across all rows is `DECIMAL(4,1)`.

## Threshold decision and the regenerated backfill (local; nothing uploaded)

### Decision (2026-09-26, by the user)

| Threshold | Old (doc 03 placeholder) | New | Basis |
|---|---|---|---|
| Emergency | 4.5 | **5.75** | False-alarm target: at most about one false sustained incident (2+ consecutive scans) per week galaxy-wide, on noise alone. 5.75 is the lowest threshold meeting it in the 90-day analysis. |
| Anomaly | 3.0 | **4.0**, provisional | About 1% of scans over it on noise. Provisional until Phase 6 defines "active anomaly" (doc 06 OPEN note). |

The riser stays a linear ramp. Doc 03, the `edge/forcesim` constants, the derived injection targets and
the backfill were changed together; retuning means rerunning `python edge/analyze_thresholds.py`.

### Analysis summary

Full local backfill (seed 20260926, 60 planets x 8,640 scans = 518,400 scans; 52 ambient spike episodes,
4 injected). Baseline: the 90-day window baseline of the generated data. "Noise-only" is outside every
injected and ambient-spike episode; "sustained" is 2 or more consecutive scans over the threshold. Rows
from the rerun of the analysis script on the regenerated backfill (the first analysis, on the backfill
generated for the old thresholds, gave the same 5.75 row: 9 runs, 32 of 52).

| Emergency T | noise scans | noise sustained pairs | noise sustained runs (90 days) | runs per week | ambient episodes firing | injected firing |
|---|---|---|---|---|---|---|
| 4.50 | 1,514 | 492 | 310 | 24.11 | 38/52 (73%) | 4/4 |
| 5.00 | 421 | 111 | 74 | 5.76 | 37/52 (71%) | 4/4 |
| 5.50 | 103 | 21 | 14 | 1.09 | 34/52 (65%) | 4/4 |
| **5.75** | **46** | **12** | **9** | **0.70** | **32/52 (62%)** | **4/4** |
| 6.00 | 20 | 5 | 5 | 0.39 | 32/52 (62%) | 4/4 |

The injected column is the data as generated, with targets built for 5.75. Analysed on the first backfill
(targets built for 4.5), only 2 of 4 injected emergencies would have fired at 5.0 and none at 6.0, which is
why the targets are derived from the threshold and the backfill was regenerated.

| Anomaly T | noise scans | share of all scans | per planet per day |
|---|---|---|---|
| 3.00 | 42,913 | 8.278% | 7.95 |
| **4.00** | **5,396** | **1.041%** | **1.00** |
| 5.00 | 421 | 0.081% | 0.08 |

Injection targets are recomputed at each threshold (whole sigmas, midi / kyber / dark). At 5.75:
sith_presence (0, -3, +4), nexus_awakening (+5, +4, 0), force_drain (-5, -4, 0), civil_unrest (0, 0, +5), all
feasible on the four texture planets at every threshold from 4.5 to 8.0. The realised composites at other
thresholds are derived, not simulated.

Mustafar and Dathomir (dark baselines 95 and 90) clamp at the 100 dark ceiling, so their 13 ambient spike
episodes have sustained peaks of 1.79 to 4.04 and cannot fire (doc 04). They are 13 of the 20 ambient
episodes that do not fire at 5.75.

### Regenerated backfill, checks

Generated with `python edge/backfill.py generate --earliest-live 2026-09-26T14:27:00.000Z` (window
2026-06-28T14:15:00Z to 2026-09-26T14:15:00Z, exclusive), then `verify`. The manifest is `edge/backfill_manifest.json`.

| # | Check | Expected | Actual | Evidence |
|---|---|---|---|---|
| R1 | Files and rows | 8,640 files x 60 lines = 518,400 rows | 8,640 files, 518,400 rows, 218,049,991 bytes | `generate` output, manifest |
| R2 | Verify | A regeneration and the files on disk are byte-identical to the manifest | **OK**: content hash `8308472c95e55e6360ec8168f61f6ffb56bd8abb2b9d1dd9e67ecdd9505ffc62` | `verify` output |
| R3 | All 4 injected emergencies fire | 2+ consecutive hold scans over 5.75, each classifying as its own signature | tatooine sith_presence: 4 of 4 hold scans, weakest composite 6.503. dantooine nexus_awakening: 3 of 3, 6.294. kamino force_drain: 5 of 5, 6.286. coruscant civil_unrest: 4 of 4, 7.295. All classify as their own signature. | Manifest report, `generate` output |
| R4 | Noise-only sustained runs at 5.75 | About one a week or fewer | 9 in 90 days (0.70 a week) | Manifest report, analysis script |
| R5 | Ambient episodes firing at 5.75 | Recorded, no target | 32 of 52 (62%) | Manifest report, analysis script |
| R6 | Riser (mon_cala, linear +5 sigma) | Last-day mean composite under the anomaly threshold 4.0 | 2.013 (last-day mean z_dark +1.265); passes. Its maximum single-scan composite is 4.970, so it never reaches 5.75; noise puts 44 scans over 4.0 (13 consecutive pairs). | Manifest report |
| R7 | Overlap with live data | No synthetic `event_time` at or after 2026-09-26T14:27:00.000Z | None: last synthetic event 2026-09-26T14:00:02.950Z; the generator's per-event guard did not refuse any | Manifest window, `generate` output |
| R8 | Modes and replayed rows | DISCONNECTED gaps and BURST recovery present | 6 gaps; DISCONNECTED 2,880 rows, BURST 360, CONNECTED 515,160; 2,260 rows with lag over 1,800 s | Manifest report |

## Backfill half: 90 days of synthetic history

Pending. Nothing has been uploaded. The rows below are filled from the `(e)` queries in
`ingest/phase2_checkpoint.sql` after the upload.

| # | Check | Expected | Actual | Evidence |
|---|---|---|---|---|
| B1 | Rows in bronze | 518,400 with `is_synthetic`; 8,640 scans; 60 sectors; about 90 days | | |
| B2 | Every backfill scan complete | 0 scans with a row count other than 60 | | |
| B3 | Partitions | 1 or 2 `dt` partitions (the upload days) | | |
| B4 | Modes | `CONNECTED`, `DISCONNECTED` and `BURST` rows present (texture check) | | |
| B5 | No overlap with live data | 0 synthetic rows with `event_time` at or after the earliest live event | | |
| B6 | No duplicates after the load | 0 rows from the duplicate `event_id` query | | |
