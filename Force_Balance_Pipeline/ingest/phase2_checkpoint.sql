-- Phase 2 checkpoint queries (doc 07, doc 05). Run them in the workspace SQL editor or a notebook SQL cell,
-- as yourself (not as force-bridge, which has no access to bronze). Query 0 comes first: it settles whether the
-- notebook's `payload` line stores real numbers. Live = NOT is_synthetic; the backfill (Phase 2, later) = is_synthetic.

-- 0. FIRST QUERY after the first notebook run: is `payload` a VARIANT with numeric fields?
--    PASS: payload_type = 'variant' and every field in payload_schema is numeric, for example
--          OBJECT<battery_pct: BIGINT, dark_side_activity: DECIMAL(3,1), kyber_resonance: DECIMAL(3,1),
--                 midichlorian_ppm: DECIMAL(6,1), sensor_temp_c: DECIMAL(3,1)>
--          (JSON numbers with a decimal point may show as DECIMAL(p,s) or DOUBLE; both are numeric).
--    FAIL: any field typed STRING (numbers stored as text), or the whole payload typed STRING
--          (double-encoded JSON), or payload NULL. On a FAIL, do the reset in docs/05-platform-setup.md.
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

-- (e) backfill, after it is loaded: ~518,400 rows
SELECT count(*) AS n, count(DISTINCT scan_id) AS scans, count(DISTINCT sector_id) AS sectors,
       min(event_time), max(event_time), count(DISTINCT to_date(event_time)) AS days
FROM force.bronze.events WHERE is_synthetic;                                        -- 518,400; 8,640; 60; ~90 days
SELECT scan_id FROM force.bronze.events WHERE is_synthetic
GROUP BY scan_id HAVING count(*) <> 60;                                             -- 0 rows
SELECT count(DISTINCT dt) AS partitions FROM force.bronze.events WHERE is_synthetic;  -- 1-2 (the upload days)
SELECT mode, count(*) FROM force.bronze.events WHERE is_synthetic GROUP BY mode;    -- texture check
