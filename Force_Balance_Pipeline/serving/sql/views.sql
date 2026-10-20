-- The dashboard's panel views over schema serving (doc 07 Phase 7, "Panels" and "Display rules"; doc 06 "User-initiated deployment"
-- and "Re-escalation"; doc 05 "Serving (Phase 7)"). CREATE OR REPLACE, so applying it twice is safe. No view uses now(): every
-- time window is anchored to the data, so a view gives the same answer whenever it is read.
-- Thresholds and where they come from:
--   4.0   anomaly threshold   (doc 03 "Thresholds"; edge/forcesim/signatures.py ANOMALY_THRESHOLD)
--   5.75  emergency threshold (doc 03 "Thresholds"; edge/forcesim/signatures.py EMERGENCY_THRESHOLD)
--   2026-10-12T00:00:00Z AGENT_EPOCH (doc 06 "Runtime"; agent/config.py)
--   4.40  severity floor, 12.0 master line, master-or-higher ranks (doc 06 "Constraint layer"; agent/constraints.py)

-- One row per incident with its newest decision. status: decided / open / historical (doc 06, "Active anomaly").
CREATE OR REPLACE VIEW serving.v_incidents AS
WITH latest AS (
  SELECT DISTINCT ON (disturbance_id) *
  FROM serving.deployment
  ORDER BY disturbance_id, decided_at DESC, deployment_id DESC
), joined AS (
  SELECT d.*, s.sector_name, s.region,
         l.deployment_id, l.decision, l.decided_by, l.decided_at, l.jedi_ids,
         (l.context_snapshot ->> 'severity')::double precision AS snapshot_severity
  FROM serving.disturbance d
  LEFT JOIN serving.dim_sector s ON s.sector_id = d.sector_id
  LEFT JOIN latest l ON l.disturbance_id = d.disturbance_id
)
SELECT j.disturbance_id, j.sector_id, j.detected_at, j.scan_id, j.imbalance_score, j.z_midi, j.z_kyber, j.z_dark, j.signature,
       j.severity, j.is_report_sourced, j.report_description, j.report_relevance, j.sustained_scans, j.cooldown_conflict,
       j.agent_processed, j.sector_name, j.region, j.deployment_id, j.decision, j.decided_by, j.decided_at, j.snapshot_severity,
       CASE WHEN j.deployment_id IS NOT NULL THEN 'decided'
            WHEN j.detected_at >= timestamptz '2026-10-12 00:00:00+00' THEN 'open'
            ELSE 'historical' END AS status,
       -- doc 06 "Re-escalation", Flag: severity crossed a boundary the decision depended on. Never null.
       COALESCE(
         (j.decision = 'stand_down' AND j.snapshot_severity < 4.40 AND j.severity >= 4.40)
         OR (j.decision = 'deploy' AND j.snapshot_severity <= 12.0 AND j.severity > 12.0
             AND NOT EXISTS (
               SELECT 1
               FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(j.jedi_ids) = 'array' THEN j.jedi_ids ELSE '[]'::jsonb END) AS pj(jedi_id)
               JOIN serving.dim_jedi dj ON dj.jedi_id = pj.jedi_id
               WHERE dj.rank IN ('master', 'council_member', 'grand_master'))),
         false) AS severity_rose_after_decision
FROM joined j;

-- Galaxy view: the latest probe reading per planet and its colour band; a report row never colours a planet.
CREATE OR REPLACE VIEW serving.v_galaxy AS
WITH latest AS (
  SELECT DISTINCT ON (sector_id) sector_id, event_time, imbalance_score, signature
  FROM serving.sector_reading
  WHERE source_type = 'probe'
  ORDER BY sector_id, event_time DESC
), open_incident AS (
  SELECT DISTINCT ON (sector_id) sector_id, disturbance_id
  FROM serving.v_incidents
  WHERE status = 'open'
  ORDER BY sector_id, detected_at DESC, disturbance_id DESC
)
SELECT s.sector_id, s.sector_name, s.system_name, s.region, s.is_unknown,
       l.event_time, l.imbalance_score, l.signature,
       CASE WHEN l.imbalance_score IS NULL THEN 'no_data'
            WHEN l.imbalance_score > 5.75 THEN 'emergency'
            WHEN l.imbalance_score > 4.0 THEN 'anomaly'
            ELSE 'normal' END AS band,
       o.disturbance_id AS open_disturbance_id
FROM serving.dim_sector s
LEFT JOIN latest l ON l.sector_id = s.sector_id
LEFT JOIN open_incident o ON o.sector_id = s.sector_id;

-- Planet detail: the 90-day baseline with 1 and 2 sigma bands.
CREATE OR REPLACE VIEW serving.v_baseline_bands AS
SELECT sector_id, channel, mean_90d, stddev_90d, sample_count, computed_at,
       mean_90d - stddev_90d AS lower_1, mean_90d + stddev_90d AS upper_1,
       mean_90d - 2 * stddev_90d AS lower_2, mean_90d + 2 * stddev_90d AS upper_2
FROM serving.sector_baseline;

CREATE OR REPLACE VIEW serving.v_signature_breakdown AS
SELECT signature,
       count(*) AS incidents_total,
       count(*) FILTER (WHERE status = 'open') AS incidents_open,
       count(*) FILTER (WHERE status = 'decided') AS incidents_decided,
       count(*) FILTER (WHERE is_report_sourced) AS incidents_report_sourced,
       max(detected_at) AS last_detected_at
FROM serving.v_incidents
GROUP BY signature;

-- One row per scan: complete when all 60 planets are present.
CREATE OR REPLACE VIEW serving.v_scan_completeness AS
SELECT scan_id,
       min(event_time) AS scan_event_time,
       count(*) AS rows,
       count(*) >= 60 AS complete,
       bool_or(mode = 'DISCONNECTED') AS any_disconnected,
       count(*) FILTER (WHERE is_replayed) AS replayed_rows
FROM serving.probe_reading
GROUP BY scan_id;

-- Source health: the newest snapshot per source and the newest scan. There is no buffer_depth: it is always NULL upstream (the UI shows n/a).
CREATE OR REPLACE VIEW serving.v_source_health AS
WITH snap AS (
  SELECT DISTINCT ON (source_id) source_id, current_mode, last_seen_at, published_at
  FROM serving.source_health_snapshot
  ORDER BY source_id, published_at DESC
), newest AS (
  SELECT scan_id, scan_event_time, rows, complete
  FROM serving.v_scan_completeness
  ORDER BY scan_event_time DESC, scan_id DESC
  LIMIT 1
)
SELECT s.source_id, s.current_mode, s.last_seen_at, s.published_at,
       n.scan_id AS last_scan_id, n.scan_event_time AS last_scan_event_time,
       n.rows AS last_scan_rows, n.complete AS last_scan_complete
FROM snap s
LEFT JOIN newest n ON true;

-- Ingest lag histogram: the last 7 days before the newest arrival, future-time fault rows excluded, ten fixed bins that always appear.
CREATE OR REPLACE VIEW serving.v_ingest_lag_histogram AS
WITH anchor AS (
  SELECT max(arrival_ts) AS a FROM serving.probe_reading
), kept AS (
  SELECT 1 AS one, p.ingest_lag_seconds AS lag, p.is_replayed, p.was_buffered
  FROM serving.probe_reading p, anchor
  WHERE p.arrival_ts > anchor.a - interval '7 days'
    AND p.event_time <= p.arrival_ts + interval '10 minutes'
), bins (bin_order, label, lower_s, upper_s) AS (
  VALUES (1, '< 0 s', NULL::bigint, 0::bigint),
         (2, '0-5 s', 0, 5),
         (3, '5-30 s', 5, 30),
         (4, '30-60 s', 30, 60),
         (5, '1-5 min', 60, 300),
         (6, '5-15 min', 300, 900),
         (7, '15-30 min', 900, 1800),
         (8, '30-60 min', 1800, 3600),
         (9, '1-6 h', 3600, 21600),
         (10, '> 6 h', 21600, NULL)
)
SELECT b.bin_order, b.label, b.lower_s, b.upper_s,
       count(k.one) AS rows,
       count(k.one) FILTER (WHERE k.is_replayed) AS replayed_rows,
       count(k.one) FILTER (WHERE k.was_buffered) AS buffered_rows
FROM bins b
LEFT JOIN kept k ON (b.lower_s IS NULL OR k.lag >= b.lower_s) AND (b.upper_s IS NULL OR k.lag < b.upper_s)
GROUP BY b.bin_order, b.label, b.lower_s, b.upper_s;

-- Rejection rate per hour (UTC) over the 7 days before the newest arrival in probe_reading or rejects.
CREATE OR REPLACE VIEW serving.v_rejection_rate AS
WITH anchor AS (
  SELECT greatest((SELECT max(arrival_ts) FROM serving.probe_reading), (SELECT max(arrival_ts) FROM serving.rejects)) AS a
), rj AS (
  SELECT date_trunc('hour', r.arrival_ts, 'UTC') AS h, count(*) AS n
  FROM serving.rejects r, anchor WHERE r.arrival_ts > anchor.a - interval '7 days' GROUP BY 1
), ac AS (
  SELECT date_trunc('hour', p.arrival_ts, 'UTC') AS h, count(*) AS n
  FROM serving.probe_reading p, anchor WHERE p.arrival_ts > anchor.a - interval '7 days' GROUP BY 1
)
SELECT COALESCE(rj.h, ac.h) AS hour,
       COALESCE(rj.n, 0) AS rejects,
       COALESCE(ac.n, 0) AS accepted,
       COALESCE(rj.n, 0)::double precision / NULLIF(COALESCE(rj.n, 0) + COALESCE(ac.n, 0), 0) AS rate
FROM rj FULL OUTER JOIN ac ON rj.h = ac.h;

CREATE OR REPLACE VIEW serving.v_rejects_by_reason AS
WITH anchor AS (
  SELECT greatest((SELECT max(arrival_ts) FROM serving.probe_reading), (SELECT max(arrival_ts) FROM serving.rejects)) AS a
)
SELECT date_trunc('hour', r.arrival_ts, 'UTC') AS hour, r.reject_reason, count(*) AS rejects
FROM serving.rejects r, anchor
WHERE r.arrival_ts > anchor.a - interval '7 days'
GROUP BY 1, 2;

-- Deployment log: orphaned when no incident matches; capacity_reached when an override lists deployment_capacity_reached in its
-- "reasons" array (the shape agent/loop.py writes: {"proposal": ..., "reasons": [...]}).
CREATE OR REPLACE VIEW serving.v_deployment_log AS
SELECT p.deployment_id, p.disturbance_id, p.decided_by, p.decision, p.jedi_ids, p.starship_id, p.eta_hours, p.released_at,
       p.rationale, p.context_snapshot, p.signature_at_decision, p.model, p.decision_latency_ms, p.guardrail_overrides, p.decided_at,
       d.sector_id, s.sector_name,
       (d.disturbance_id IS NULL) AS orphaned,
       p.decided_at + p.eta_hours * interval '1 hour' AS arrival_at,
       jsonb_array_length(COALESCE(p.guardrail_overrides, '[]'::jsonb)) AS override_count,
       EXISTS (
         SELECT 1
         FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.guardrail_overrides) = 'array' THEN p.guardrail_overrides ELSE '[]'::jsonb END) AS o(item)
         WHERE jsonb_typeof(o.item -> 'reasons') = 'array' AND (o.item -> 'reasons') @> '["deployment_capacity_reached"]'::jsonb
       ) AS capacity_reached
FROM serving.deployment p
LEFT JOIN serving.disturbance d ON d.disturbance_id = p.disturbance_id
LEFT JOIN serving.dim_sector s ON s.sector_id = d.sector_id;
