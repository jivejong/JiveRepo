-- Phase 3 checkpoint queries (doc 07, Phase 3), written before the run. Run them in the workspace SQL editor as yourself, not as force-bridge.
--
-- Fill in the placeholders first, from the probe's own log on the Pi (mode_transitions.jsonl), not from the wall clock:
--   <outage_start_utc>  the ts_utc of the transition into DISCONNECTED, e.g. 2026-09-27T12:32:30.000Z
--   <outage_end_utc>    the ts_utc of the transition into BURST (the replay begins)
--   <run_start_utc>, <run_end_utc>  the span of the whole live run being checked, on quarter-hour boundaries
--   <replay_dt>, <replay_hh>  (p3-3b only) the INGEST day and hour of the replay files, from the dt=/hh= of the bridge's three "landed"
--                       lines or from p3-6, e.g. 2026-09-29 and 00 (two digits, as in the path)
-- The checkpoint runs with fault injection OFF (--fault-rate 0): a clean outage. A 45-minute outage contains three quarter-hour boundaries, so
-- expect 3 scans = 180 rows. Expectations are in the comments; VARIANT paths are cast before IS NULL because a JSON null is not a SQL NULL.
-- Start the cut a few minutes after a quarter-hour boundary (:16 or :31 past the hour, doc 05): a scan taken in the ~90 s detection
-- window is still labelled CONNECTED (doc 02) but buffered, and starting the cut there keeps that scan out of <outage_start_utc>.
-- _ingest_ts is the time the NOTEBOOK processed a file (current_timestamp() in ingest/autoloader_bronze.py), not the bridge's landing time.
-- One notebook run after the restore would stamp every row alike and p3-3 would show nothing. Run the notebook right before the cut, and
-- again as soon as the replay has landed (the bridge log shows three "landed" lines within seconds of <outage_end_utc>); then p3-3 measures
-- the replay. p3-3b proves the same thing from arrival time alone, straight from the landed files, whenever the notebook ran; so do the
-- bridge's "landed" lines and the dt=/hh= path (p3-6).

-- (p3-0) BEFORE the run: what is already there, so the run's rows can be told apart. Housekeeping events (payload.kind) are not readings.
SELECT count(*) AS live_readings_before, max(event_time) AS newest_event_time
FROM force.bronze.events
WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL;

-- (p3-1) the buffered scans: n = 60 x scans, sectors = 60, first_event and last_event inside the outage and spanning it
SELECT count(*) AS n, count(DISTINCT scan_id) AS scans, count(DISTINCT sector_id) AS sectors,
       min(event_time) AS first_event, max(event_time) AS last_event
FROM force.bronze.events
WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL
  AND event_time >= TIMESTAMP '<outage_start_utc>' AND event_time < TIMESTAMP '<outage_end_utc>';

-- (p3-2) their modes: DISCONNECTED for every scan taken after the probe noticed the cut; a scan taken in the ~90 s before it noticed is
--        CONNECTED (the mode when taken, doc 02) but was still buffered and is still here
SELECT mode, count(*) AS n, count(DISTINCT scan_id) AS scans
FROM force.bronze.events
WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL
  AND event_time >= TIMESTAMP '<outage_start_utc>' AND event_time < TIMESTAMP '<outage_end_utc>'
GROUP BY mode ORDER BY mode;

-- (p3-3) _ingest_ts clustered at the replay: first_ingest at or after outage_end, last_ingest minutes after it at most (one batch of 500 per
--        10 s), min_lag_s small (the newest scan), max_lag_s over 1800 (the oldest scan is_replayed), rows_replayed > 0
SELECT min(_ingest_ts) AS first_ingest, max(_ingest_ts) AS last_ingest,
       min(unix_timestamp(_ingest_ts) - unix_timestamp(event_time)) AS min_lag_s,
       max(unix_timestamp(_ingest_ts) - unix_timestamp(event_time)) AS max_lag_s,
       count_if(unix_timestamp(_ingest_ts) - unix_timestamp(event_time) > 1800) AS rows_replayed
FROM force.bronze.events
WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL
  AND event_time >= TIMESTAMP '<outage_start_utc>' AND event_time < TIMESTAMP '<outage_end_utc>';

-- (p3-3b) the replay's ARRIVAL, proved from the landed files themselves, whenever the notebook ran. It reads the landing volume, not
--         bronze. _metadata.file_modification_time is when the file was written to the volume, that is, when it arrived; each line's
--         event_time is parsed out of the file. If the replay straddles an hour, run it once per hour.
--         Expected for a 45-minute cut (3 scans): files = 3 and n = 180; first_landed at or after <outage_end_utc> (a few seconds: the
--         probe's BURST, then the bridge's upload); landing_spread_s under about 10 (the drain sends the three scans back to back);
--         min_lag_s about 60 to 160 (the newest scan: outage_end less its boundary, plus the reconnect and the upload); max_lag_s about
--         1860 to 1960 (over 1800, the oldest scan); rows_replayed = 60 (only the oldest scan is over doc 03's 1800 s; the second is
--         about 16 minutes old). The ranges are wide because the reconnect backoff (up to about 35 s) and where in the minute the
--         cut started both move them; the claim is max_lag_s over 1800 and min_lag_s small.
SELECT count(DISTINCT file) AS files, count(*) AS n, min(landed_at) AS first_landed, max(landed_at) AS last_landed,
       unix_timestamp(max(landed_at)) - unix_timestamp(min(landed_at)) AS landing_spread_s,
       min(unix_timestamp(landed_at) - unix_timestamp(event_time)) AS min_lag_s,
       max(unix_timestamp(landed_at) - unix_timestamp(event_time)) AS max_lag_s,
       count_if(unix_timestamp(landed_at) - unix_timestamp(event_time) > 1800) AS rows_replayed
FROM (
  SELECT _metadata.file_name AS file, _metadata.file_modification_time AS landed_at,
         to_timestamp(get_json_object(value, '$.event_time')) AS event_time
  FROM read_files('/Volumes/force/raw/telemetry/dt=<replay_dt>/hh=<replay_hh>/', format => 'text')
)
WHERE event_time >= TIMESTAMP '<outage_start_utc>' AND event_time < TIMESTAMP '<outage_end_utc>';

-- (p3-3b, directory listing) the same proof with nothing parsed: LIST returns one row per file with its modification_time (epoch
--         milliseconds; timestamp_millis() turns one into a timestamp). Expect the replay's three files with modification_times within
--         seconds of each other and all after <outage_end_utc>, beside the normal scans' files 15 minutes apart. Run it on its own: LIST
--         cannot be combined with another statement, and it is the fallback if read_files does not return _metadata in your workspace.
LIST '/Volumes/force/raw/telemetry/dt=<replay_dt>/hh=<replay_hh>/';

-- (p3-4) no gaps over the whole run: slots_seen = slots_expected (a slot is a 15-minute boundary; STEALTH hours are checked in p3-7)
WITH scans AS (
  SELECT scan_id, min(event_time) AS t
  FROM force.bronze.events
  WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL AND mode <> 'STEALTH'
    AND event_time >= TIMESTAMP '<run_start_utc>' AND event_time < TIMESTAMP '<run_end_utc>'
  GROUP BY scan_id
), slots AS (
  SELECT DISTINCT timestamp_seconds(floor(unix_timestamp(t) / 900) * 900) AS slot FROM scans
)
SELECT count(*) AS slots_seen,
       (unix_timestamp(max(slot)) - unix_timestamp(min(slot))) / 900 + 1 AS slots_expected
FROM slots;

-- (p3-5) no duplicates, and every scan complete: both counts 0 over the run
SELECT count(*) AS duplicate_event_ids FROM (
  SELECT event_id FROM force.bronze.events
  WHERE source_type = 'probe' AND NOT is_synthetic AND event_time >= TIMESTAMP '<run_start_utc>' AND event_time < TIMESTAMP '<run_end_utc>'
  GROUP BY event_id HAVING count(*) > 1);
SELECT count(*) AS scans_not_60 FROM (
  SELECT scan_id FROM force.bronze.events
  WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL AND mode <> 'STEALTH'
    AND event_time >= TIMESTAMP '<run_start_utc>' AND event_time < TIMESTAMP '<run_end_utc>'
  GROUP BY scan_id HAVING count(*) <> 60);

-- (p3-6) the replay's files landed under the INGEST hour and day, not the event hour: at least one file per buffered scan (the drain sends
--        batches of 500, so a scan can straddle two batches; the pause is 10 s, under the bridge's 90 s timer, so it stays one file)
SELECT dt, hh, count(DISTINCT _source_file) AS files, count(*) AS n
FROM force.bronze.events
WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL
  AND event_time >= TIMESTAMP '<outage_start_utc>' AND event_time < TIMESTAMP '<outage_end_utc>'
GROUP BY dt, hh ORDER BY dt, hh;

-- (p3-7) STEALTH (a separate hour): every row has midichlorian and kyber null, dark present, sensor_temp_c absent; 60 rows per scan;
--        scans 3600 s apart
SELECT count(*) AS n, count(DISTINCT scan_id) AS scans,
       sum(CASE WHEN payload:midichlorian_ppm::double IS NULL AND payload:kyber_resonance::double IS NULL THEN 1 ELSE 0 END) AS two_null_channels,
       sum(CASE WHEN payload:dark_side_activity::double IS NOT NULL THEN 1 ELSE 0 END) AS dark_present,
       sum(CASE WHEN payload:sensor_temp_c::double IS NULL THEN 1 ELSE 0 END) AS temperature_absent,
       min(event_time) AS first_scan, max(event_time) AS last_scan
FROM force.bronze.events
WHERE source_type = 'probe' AND NOT is_synthetic AND mode = 'STEALTH';

-- (p3-8) informational, in the separate fault-injection period: rows shaped like the four faults, to compare with the counts in the Pi's
--        fault_injection.jsonl. The exact reconciliation (rejects = the log) waits for silver in Phase 4.
SELECT
  sum(CASE WHEN mode = 'CONNECTED' AND (payload:midichlorian_ppm::double IS NULL OR payload:kyber_resonance::double IS NULL
                                        OR payload:dark_side_activity::double IS NULL) THEN 1 ELSE 0 END) AS null_channel,
  sum(CASE WHEN payload:midichlorian_ppm::double > 30000 OR payload:kyber_resonance::double > 100
            OR payload:dark_side_activity::double > 100 THEN 1 ELSE 0 END) AS out_of_range,
  sum(CASE WHEN sector_id LIKE 'unknown-%' THEN 1 ELSE 0 END) AS unknown_sector,
  sum(CASE WHEN event_time > _ingest_ts THEN 1 ELSE 0 END) AS event_time_ahead_of_ingest
FROM force.bronze.events
WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string IS NULL
  AND event_time >= TIMESTAMP '<fault_period_start_utc>' AND event_time < TIMESTAMP '<fault_period_end_utc>';

-- (p3-9) housekeeping events: 0 in the checkpoint (the buffer never reached its cap); a buffer_overflow event is a housekeeping event (doc 02)
SELECT count(*) AS housekeeping_events, max(payload:dropped::int) AS most_dropped
FROM force.bronze.events
WHERE source_type = 'probe' AND NOT is_synthetic AND payload:kind::string = 'buffer_overflow';
