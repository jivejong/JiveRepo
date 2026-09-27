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

## Threshold decision and the regenerated backfill (local generation)

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

### Upload to the landing volume (2026-09-26)

`python edge/backfill.py upload` as the `force-bridge` service principal (Files API, scope `files`), from the
directory `generate` wrote, after a disk-only `verify` against the committed manifest. The dt=/hh= prefix is ingest
time (doc 02) and was pinned on first use in `upload_state.json`, keyed by the manifest's content hash, so the full
upload reused it. Run id (content hash) `8308472c95e55e6360ec8168f61f6ffb56bd8abb2b9d1dd9e67ecdd9505ffc62`.

| # | Check | Expected | Actual | Evidence |
|---|---|---|---|---|
| U1 | Pinned prefix | One prefix for the whole backfill, assigned once | **`dt=2026-09-26/hh=22`**, assigned 2026-09-26T22:48:42Z, kept by the state file for the second upload | `upload` output; `upload_state.json` |
| U2 | One-day upload (day -71, the tatooine emergency day), 4 workers | 96 files, no 409s on a first run | 96 selected, **96 uploaded, 0 already landed (409), 0 failed, 0 retries**, 11.8 s (8.16 files/s, 0.21 MB/s, 2.4 MB). Projection for 8,640 files: 17.6 min | `upload` output |
| U3 | Landing check on those 96 (`verify_landing.py --backfill --only-day -71`) | The files under the prefix are exactly the local ones | **LANDING VERIFIED.** 96 files under `dt=2026-09-26/hh=22/`, exactly the expected names; every listed size equals the local size; no expected file under another prefix; 96 of 96 downloaded files byte-for-byte the local files, each 60 valid synthetic envelopes with one `scan_id`. The 3 live files at `hh=14` untouched. | `verify_landing` output |
| U4 | Directory moved to a stable location, then disk-only verify | Same content hash | The output directory (with `upload_state.json`) was moved from the session temp folder to `~/.force_balance_pipeline/backfill/full575/`, outside the repository. **`verify` OK**: 8,640 files, content hash `8308472c...ffc62` | `verify --disk-only` output |
| U5 | Full upload, 4 workers, same run id and prefix | 96 already landed (409) for day -71; nothing under a second prefix | 8,640 selected, **8,544 uploaded, 96 already landed (409), 0 failed, 0 retries**, 1,185.3 s (19.8 min; 7.21 files/s, 0.18 MB/s, 218.0 MB). It took 12% longer than the 17.6 min projected from the one-day run. | `upload` output |
| U6 | Landing check on the whole backfill (`verify_landing.py --backfill --sample 300`) | Names and sizes for all 8,640; 300 downloads byte-for-byte | **First run: 3 FAIL lines, all one dropped connection.** Names, sizes and prefix checks passed (8,640 files, exactly the expected names, every size equal, none under another prefix), but one download failed with a network error (`RemoteDisconnected`). The listing showed that file with the right size, and the dropped read was likely caused by the owner toggling a VPN mid-run (owner-reported, not confirmed from the output). The verifier had no retry on reads, so it now retries a read up to 3 attempts on a network error or HTTP 429/5xx (a denied read is not retried), with tests. **Rerun, same sample: LANDING VERIFIED.** 299 of 8,640 files downloaded, all byte-for-byte the local files, each with 60 valid synthetic envelopes and one `scan_id`; the other 8,341 checked by name and size. | `verify_landing` output, both runs |
| U7 | What is in the volume | The backfill under one prefix, plus the 3 live files | 8,640 files under `dt=2026-09-26/hh=22/` and 3 live files under `dt=2026-09-26/hh=14/`. The Auto Loader notebook has not been run on the backfill. | `verify_landing` output |

**If `upload_state.json` is lost** (it lives next to the data, in `~/.force_balance_pipeline/backfill/full575/`), do not
let a new prefix be assigned: the prefix is ingest time, so a fresh state file would name a new hour and the same file
names would land a second time under it. Recreate it with the prefix in U1, then rerun; every file skips via 409:

```
python edge/backfill.py upload --out ~/.force_balance_pipeline/backfill/full575 --pin-prefix 2026-09-26 22   # bash; use the full path elsewhere
```

`--pin-prefix` refuses a prefix that contradicts an existing state file or a run id that does not match. If the data is
lost too, `generate` rebuilds it byte-for-byte from the committed manifest (same run id) before pinning.

### Rows in bronze

The Auto Loader notebook was run over the uploaded backfill (reported by the user: it ran successfully) and the queries
below were run as the user. They are the `(e1)` to `(e8)` statements of `ingest/phase2_checkpoint.sql`, with the query
text as pasted. The user's six pasted results were, in order: the backfill rows, scans with a count other than 60, `dt`
partitions, modes, `no_overlap` and `total_rows`, and inconsistent synthetic flags; the duplicate check and the write
history were pasted separately.

| # | Check | Query | Expected | Actual |
|---|---|---|---|---|
| B1 | Rows in bronze | (e1) | 518,400 with `is_synthetic`; 8,640 scans; 60 sectors; about 90 days | **Pass.** n = 518,400; scans = 8,640; sectors = 60; first `event_time` 2026-06-28T14:15:00.000Z (the window start); last 2026-09-26T14:00:02.950Z (the last synthetic event in the manifest); 91 distinct calendar dates (a 90-day window that starts at 14:15 UTC touches 91 dates). |
| B2 | Every backfill scan complete | (e2) | 0 scans with a row count other than 60 | **Pass.** 0. |
| B3 | Partitions | (e3) | 1 or 2 `dt` partitions (the upload days) | **Pass.** 1 (all files were uploaded on 2026-09-26). |
| B4 | Modes | (e4) | `CONNECTED`, `DISCONNECTED` and `BURST` rows present (texture check) | **Pass.** CONNECTED 515,160; BURST 360; DISCONNECTED 2,880, exactly the counts in the manifest. |
| B5 | No overlap with live data, and the table's total | (e5) | The latest synthetic `event_time` is before the earliest live one | **Pass.** `no_overlap` = true; `total_rows` = 518,580, which is the 518,400 backfill rows plus the 180 live rows of L5. |
| B6 | Synthetic flags still consistent across the whole table | (e6) | 0 rows where `is_synthetic` and `synthetic_ingest_ts` disagree | **Pass.** 0. |
| B7 | No duplicate event ids; how the backfill was written | (e7), (e8) | 0 duplicate `event_id`s; the backfill loaded by the notebook run | **Pass.** `duplicate_event_ids` = 0. `DESCRIBE HISTORY` shows four versions: 0 (`CREATE TABLE`) and 1 (the 180 live rows, 2026-09-26T15:27:34Z), then the backfill as two `STREAMING UPDATE` appends by the same stream query in one notebook run: version 2 (epoch 1, 2026-09-27T00:16:52Z) 90,000 rows in 1 file, and version 3 (epoch 2, 2026-09-27T00:17:13Z) 428,400 rows in 1 file. 180 + 90,000 + 428,400 = 518,580, the `total_rows` of B5, and 90,000 + 428,400 = 518,400 backfill rows. Both appends are blind appends with nothing removed (`numRemovedFiles` 0), on Databricks Runtime 19.6.x. |

The rows match `edge/backfill_manifest.json` (518,400 rows, 8,640 scans, the mode counts and the window). Not recorded: an
exactly-once rerun of the notebook after the backfill load. The history has no version after 3, so no later write
happened.
