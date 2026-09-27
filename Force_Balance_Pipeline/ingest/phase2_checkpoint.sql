-- Phase 2 checkpoint queries (doc 07, doc 05). Run them in the workspace SQL editor or a notebook SQL cell,
-- as yourself (not as force-bridge, which has no access to bronze). Query 0 comes first: it settles whether the
-- notebook's `payload` line stores real numbers. Live = NOT is_synthetic; the backfill (Phase 2, later) = is_synthetic.

-- 0. FIRST QUERY after the first notebook run: is `payload` a VARIANT with numeric fields?
--    PASS: payload_type = 'variant' and every field in payload_schema is numeric, for example
--          OBJECT<battery_pct: BIGINT, dark_side_activity: DECIMAL(3,1), kyber_resonance: DECIMAL(3,1),
--                 midichlorian_ppm: DECIMAL(6,1), sensor_temp_c: DECIMAL(3,1)>
--          (JSON numbers with a decimal point may show as DECIMAL(p,s) or DOUBLE; both are numeric).
--          null_payloads (0c below) is 0.
--    FAIL: any field typed STRING (numbers stored as text), or the whole payload typed STRING, or any NULL payload
--          (try_parse_json returns NULL for a malformed one). On a FAIL, do the reset in docs/05-platform-setup.md.
SELECT event_id, sector_id,
       typeof(payload)                  AS payload_type,
       schema_of_variant(payload)       AS payload_schema,
       payload:midichlorian_ppm::double AS midi,
       payload:battery_pct::int         AS battery
FROM force.bronze.events
WHERE NOT is_synthetic
ORDER BY event_time
LIMIT 5;

-- 0b. The same across every row (one schema if all rows agree).
SELECT schema_of_variant_agg(payload) AS payload_schema_all_rows FROM force.bronze.events;

-- 0c. try_parse_json turns a malformed payload into NULL instead of failing the stream, so a bad event shows up
--     here. Must be 0.
SELECT count(*) AS null_payloads FROM force.bronze.events WHERE payload IS NULL;

-- (a) 180 rows from 3 live scans
SELECT count(*) AS n, count(DISTINCT scan_id) AS scans, count(DISTINCT sector_id) AS sectors,
       count(DISTINCT event_id) AS distinct_events
FROM force.bronze.events WHERE NOT is_synthetic;                                    -- 180, 3, 60, 180
SELECT scan_id, count(*) AS n, min(event_time), max(event_time), min(_ingest_ts), max(_ingest_ts)
FROM force.bronze.events WHERE NOT is_synthetic GROUP BY scan_id ORDER BY 3;        -- 3 rows, n = 60

-- (b) partitions
DESCRIBE DETAIL force.bronze.events;                                                -- partitionColumns = ["dt"]
DESCRIBE TABLE force.bronze.events;                                                 -- dt date, hh int, payload variant
SELECT dt, hh, count(*) AS n, count(DISTINCT _source_file) AS files
FROM force.bronze.events WHERE NOT is_synthetic GROUP BY dt, hh ORDER BY dt, hh;
SELECT count(*) FROM force.bronze.events                                            -- 0: dt/hh match the path
WHERE _source_file NOT LIKE concat('%/dt=', dt, '/hh=', lpad(cast(hh AS STRING), 2, '0'), '/%');

-- (c) payload queryable as VARIANT, nothing missing or rescued
SELECT count(*) FROM force.bronze.events WHERE NOT is_synthetic
  AND (payload:midichlorian_ppm IS NULL OR payload:kyber_resonance IS NULL
       OR payload:dark_side_activity IS NULL);                                      -- 0 (CONNECTED, no faults)
SELECT count(*) FROM force.bronze.events WHERE _rescued_data IS NOT NULL;           -- 0
SELECT count(*) FROM force.bronze.events
WHERE (is_synthetic AND synthetic_ingest_ts IS NULL)
   OR (NOT is_synthetic AND synthetic_ingest_ts IS NOT NULL);                       -- 0

-- (d) exactly-once: record these, rerun the notebook with no new files, run them again
SELECT count(*) AS n, count(DISTINCT _source_file) AS files FROM force.bronze.events;
DESCRIBE HISTORY force.bronze.events LIMIT 5;                                       -- no new write with rows > 0
SELECT event_id, count(*) FROM force.bronze.events GROUP BY event_id HAVING count(*) > 1;   -- 0 rows

-- (e0) BEFORE the backfill is generated: the earliest live event in bronze. It fixes the backfill window's end,
--      the last 15-minute UTC boundary before it (docs 04 and 07), so synthetic and live readings never overlap.
--      From the live checkpoint run this should be 2026-09-26T14:27:00.000Z, giving an end of 14:15:00Z.
SELECT min(event_time) AS earliest_live_event_time FROM force.bronze.events WHERE NOT is_synthetic;

-- (e) backfill, after it is loaded. These are the statements that were run; docs/PHASE2-RESULTS.md records
--     their results as B1 to B7.
-- (e1) the backfill rows                                   -- 518,400; 8,640; 60; 2026-06-28T14:15Z .. 2026-09-26T14:00:02.950Z; 91 dates
SELECT count(*) AS n, count(DISTINCT scan_id) AS scans, count(DISTINCT sector_id) AS sectors,
       min(event_time) AS first_ts, max(event_time) AS last_ts,
       count(DISTINCT to_date(event_time)) AS days
FROM force.bronze.events WHERE is_synthetic;
-- (e2) every scan has 60 readings                          -- 0
SELECT count(*) FROM (SELECT scan_id FROM force.bronze.events WHERE is_synthetic
                      GROUP BY scan_id HAVING count(*) <> 60);
-- (e3) partitions                                          -- 1 (the upload day)
SELECT count(DISTINCT dt) AS dt_partitions FROM force.bronze.events WHERE is_synthetic;
-- (e4) modes                                               -- CONNECTED 515,160; BURST 360; DISCONNECTED 2,880 (the manifest's counts)
SELECT mode, count(*) FROM force.bronze.events WHERE is_synthetic GROUP BY mode;
-- (e5) no overlap with live, and the whole table's total   -- true; 518,580 (the backfill plus the 180 live rows)
SELECT (SELECT max(event_time) FROM force.bronze.events WHERE is_synthetic)
     < (SELECT min(event_time) FROM force.bronze.events WHERE NOT is_synthetic) AS no_overlap,
       (SELECT count(*) FROM force.bronze.events) AS total_rows;
-- (e6) flags still consistent across everything            -- 0
SELECT count(*) FROM force.bronze.events
WHERE (is_synthetic AND synthetic_ingest_ts IS NULL) OR (NOT is_synthetic AND synthetic_ingest_ts IS NOT NULL);
-- (e7) no duplicate event ids                              -- 0
SELECT count(*) AS duplicate_event_ids
FROM (SELECT event_id FROM force.bronze.events GROUP BY event_id HAVING count(*) > 1);
-- (e8) the write history                                   -- the backfill loaded as versions 2 and 3: 90,000 + 428,400 rows
DESCRIBE HISTORY force.bronze.events LIMIT 5;
