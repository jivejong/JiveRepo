-- Phase 6 checkpoint queries (doc 07, Phase 6; doc 06, "Evaluation fixtures" and "Monitoring and cost"). Run as yourself in the workspace SQL
-- editor one statement at a time, or with run_sql_file.py (python run_sql_file.py ingest/phase6_checkpoint.sql [C1 C2 ...]).
-- Read-only throughout: every statement is a SELECT.
--
-- The checkpoint: after the first production cycle of the ForceAgent task (R6b), two real incidents have a decision in force.gold.deployment:
--   geonosis   probe-sourced  sith_presence  detected_at 2026-10-14 07:15:00.9Z  (severity 16.81)
--   coruscant  report-sourced dark_adept     detected_at 2026-10-12 18:23:54.7Z  (severity 29.31, the Vader report; scan_id NULL)
-- Both are found by sector and time, not by id, so the file does not depend on the deterministic ids.
-- Target schema: the statements read the production tables force.gold.*. To check a dev run, replace "force.gold." with "force.dev_jivejong."
-- throughout (dev has the same table names; the agent's --target dev writes force.dev_jivejong.deployment).
-- C1 and C2 are read by eye (they show what the agent decided and said); C3 to C6 have an expected result stated in the heading.

-- C1 DECISIONS. One row per checkpoint incident. Expect: 2 rows, decision non-null on both, eta_hours and released_at non-null for a deploy
--   (released_at = decided_at + eta + 24 h), rationale_chars above 0, severity 16.8 and 29.3 in the snapshot, evidence_kind 'probe' and 'report'.
WITH cp AS (
  SELECT d.disturbance_id, d.sector_id, d.signature, d.severity, d.is_report_sourced,
         CASE WHEN d.is_report_sourced THEN 'coruscant report' ELSE 'geonosis probe' END AS label
  FROM force.gold.gold_disturbance d
  WHERE (d.sector_id = 'geonosis' AND NOT d.is_report_sourced
         AND d.detected_at >= '2026-10-14 07:15:00' AND d.detected_at < '2026-10-14 07:16:00')
     OR (d.sector_id = 'coruscant' AND d.is_report_sourced
         AND d.detected_at >= '2026-10-12 18:23:54' AND d.detected_at < '2026-10-12 18:23:55')
)
SELECT cp.label, cp.disturbance_id, p.decision, p.decided_by, p.jedi_ids, p.starship_id, round(p.eta_hours, 3) AS eta_hours,
       p.released_at, p.decided_at, length(p.rationale) AS rationale_chars,
       round(p.context_snapshot:severity::double, 2) AS snapshot_severity, p.context_snapshot:evidence_kind::string AS evidence_kind
FROM cp LEFT JOIN force.gold.deployment p ON p.disturbance_id = cp.disturbance_id
ORDER BY cp.label;

-- C2 RATIONALE MENTIONS. Expect: 2 rows. mentions_sector and mentions_signature true on both; for the report incident mentions_report_word is true
--   (the rationale uses 'lightsaber', 'witness' or 'report'), and null for the probe incident. A false is a finding to read, not an error.
WITH cp AS (
  SELECT d.disturbance_id, d.sector_id, d.is_report_sourced,
         CASE WHEN d.is_report_sourced THEN 'coruscant report' ELSE 'geonosis probe' END AS label
  FROM force.gold.gold_disturbance d
  WHERE (d.sector_id = 'geonosis' AND NOT d.is_report_sourced
         AND d.detected_at >= '2026-10-14 07:15:00' AND d.detected_at < '2026-10-14 07:16:00')
     OR (d.sector_id = 'coruscant' AND d.is_report_sourced
         AND d.detected_at >= '2026-10-12 18:23:54' AND d.detected_at < '2026-10-12 18:23:55')
)
SELECT cp.label, p.decision,
       contains(lower(p.rationale), lower(s.sector_name)) AS mentions_sector,
       contains(lower(p.rationale), lower(replace(p.signature_at_decision, '_', ' '))) AS mentions_signature,
       CASE WHEN cp.is_report_sourced
            THEN (contains(lower(p.rationale), 'lightsaber') OR contains(lower(p.rationale), 'witness') OR contains(lower(p.rationale), 'report'))
       END AS mentions_report_word
FROM cp
JOIN force.gold.dim_sector s ON s.sector_id = cp.sector_id
LEFT JOIN force.gold.deployment p ON p.disturbance_id = cp.disturbance_id
ORDER BY cp.label;

-- C3 SPECIALTY MATCHED. Expect: 2 rows; specialty_matched true on both (true when the incident has no signature specialty; null for a stand_down).
--   For a deploy: at least one deployed Jedi (dim_jedi joined on jedi_ids) has the context's signature_specialty as primary or secondary.
--   The VARIANT field is extracted to a string column in the dep CTE first: Databricks refuses a correlated reference to a VARIANT column.
WITH cp AS (
  SELECT d.disturbance_id, CASE WHEN d.is_report_sourced THEN 'coruscant report' ELSE 'geonosis probe' END AS label
  FROM force.gold.gold_disturbance d
  WHERE (d.sector_id = 'geonosis' AND NOT d.is_report_sourced
         AND d.detected_at >= '2026-10-14 07:15:00' AND d.detected_at < '2026-10-14 07:16:00')
     OR (d.sector_id = 'coruscant' AND d.is_report_sourced
         AND d.detected_at >= '2026-10-12 18:23:54' AND d.detected_at < '2026-10-12 18:23:55')
),
dep AS (
  SELECT p.disturbance_id, p.decision, p.jedi_ids, p.context_snapshot:signature_specialty::string AS spec
  FROM force.gold.deployment p
)
SELECT cp.label, dep.decision, dep.spec AS signature_specialty, dep.jedi_ids,
       CASE
         WHEN dep.decision <> 'deploy' THEN NULL
         WHEN dep.spec IS NULL THEN true
         ELSE (SELECT count(*) FROM force.gold.dim_jedi j
               WHERE array_contains(dep.jedi_ids, j.jedi_id)
                 AND (j.primary_specialty = dep.spec OR j.secondary_specialty = dep.spec)) > 0
       END AS specialty_matched
FROM cp LEFT JOIN dep ON dep.disturbance_id = cp.disturbance_id
ORDER BY cp.label;

-- C4 PROCESSED FLAG. Expect: 2 rows, agent_processed true on both.
SELECT CASE WHEN d.is_report_sourced THEN 'coruscant report' ELSE 'geonosis probe' END AS label, d.disturbance_id, d.agent_processed
FROM force.gold.gold_disturbance d
WHERE (d.sector_id = 'geonosis' AND NOT d.is_report_sourced
       AND d.detected_at >= '2026-10-14 07:15:00' AND d.detected_at < '2026-10-14 07:16:00')
   OR (d.sector_id = 'coruscant' AND d.is_report_sourced
       AND d.detected_at >= '2026-10-12 18:23:54' AND d.detected_at < '2026-10-12 18:23:55')
ORDER BY label;

-- C5 IDEMPOTENCY. disturbance_id values with more than one deployment row. Expect: 0 rows (Delta enforces no unique key; the agent's MERGE does).
SELECT disturbance_id, count(*) AS deployment_rows, min(decided_at) AS first_decided_at, max(decided_at) AS last_decided_at
FROM force.gold.deployment
GROUP BY disturbance_id
HAVING count(*) > 1;

-- C6 EPOCH. A deployment row for an incident detected before AGENT_EPOCH (2026-10-12 00:00:00 UTC). Expect: 0 rows (older incidents are never processed).
SELECT p.deployment_id, p.disturbance_id, d.detected_at
FROM force.gold.deployment p
JOIN force.gold.gold_disturbance d ON d.disturbance_id = p.disturbance_id
WHERE d.detected_at < '2026-10-12 00:00:00';
