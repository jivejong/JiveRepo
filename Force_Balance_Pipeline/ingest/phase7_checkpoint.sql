-- Phase 7 checkpoint queries (doc 07, Phase 7 checkpoint; doc 05, "Runbook: Phase 7 checkpoint"; doc 06, "Human-first window" and "Manual deployment
-- path"). Run as yourself in the workspace SQL editor one statement at a time, or with run_sql_file.py (python run_sql_file.py
-- ingest/phase7_checkpoint.sql [p7-1 p7-2 ...]). Read-only throughout: every statement is a SELECT.
--
-- The checkpoint: two injected incidents, A at T0 and B two hours later in another region (Job 1 runs every 2 hours). The agent records A at the
-- first cycle after the gold build and decides it at the next cycle after a Job 1 run, two hours later; while A is deployed, a manual deploy of A's Jedi to B is rejected with jedi_unavailable and writes nothing.
-- Both incidents are found by sector and time, not by id, so the file does not depend on the deterministic ids.
-- Target schema: the statements read the production tables force.gold.*. To check a dev run, replace "force.gold." with "force.dev_jivejong."
-- throughout.
--
-- Placeholders (replace each before running; all four are used):
--   <sector_a>  the sector_id of the first injected planet (for example geonosis)
--   <sector_b>  the sector_id of the second injected planet, in another region
--   <t0_utc>    the UTC time of the first injection, 'YYYY-MM-DD HH:MM:SS' (every incident of the checkpoint is detected at or after it)
--   <jedi_x>    the jedi_id the agent deployed to A and the rejected manual attempt named on B
-- Expected results are stated in each heading; p7-4 and p7-5 are read by eye.

-- (p7-1) BOTH INCIDENTS EXIST. Expect: 2 rows, one per sector, detected_at at or after <t0_utc>, severity above 4.4, a non-null signature.
SELECT d.sector_id, d.disturbance_id, d.signature, d.detected_at, round(d.severity, 2) AS severity, d.is_report_sourced
FROM force.gold.gold_disturbance d
WHERE d.sector_id IN ('<sector_a>', '<sector_b>')
  AND d.detected_at >= TIMESTAMP '<t0_utc>'
ORDER BY d.detected_at;

-- (p7-2) A'S DECISION. Expect: 1 row; decision 'deploy', decided_by 'yoda_agent', jedi_includes_x true, minutes_after_onset at least 60 and
--   decided_after_a_cycle true (the human-first window: the agent decides A at least 30 minutes after it first sees it, which with Job 1 every
--   2 hours is two hours later, so the decision is well over an hour after the onset scan). The time is measured from the incident's onset scan
--   (disturbance.scan_id, whose gold_sector_reading row gives its event_time), not from detected_at, which advances while an incident continues.
SELECT d.sector_id, p.decision, p.decided_by, p.jedi_ids, p.starship_id, p.decided_at, d.detected_at, r.event_time AS onset_scan_time,
       array_contains(p.jedi_ids, '<jedi_x>') AS jedi_includes_x,
       timestampdiff(MINUTE, r.event_time, p.decided_at) AS minutes_after_onset,
       p.decided_at >= r.event_time + INTERVAL 60 MINUTE AS decided_after_a_cycle
FROM force.gold.gold_disturbance d
JOIN force.gold.deployment p ON p.disturbance_id = d.disturbance_id
LEFT JOIN force.gold.gold_sector_reading r ON r.sector_id = d.sector_id AND r.scan_id = d.scan_id AND r.source_type = 'probe'
WHERE d.sector_id = '<sector_a>'
  AND d.detected_at >= TIMESTAMP '<t0_utc>'
ORDER BY p.decided_at;

-- (p7-3) THE REJECTED ATTEMPT WROTE NOTHING. Expect: 1 row with user_rows_naming_x = 0 (no row for B decided by 'user' that names <jedi_x>).
SELECT count(*) AS user_rows_naming_x
FROM force.gold.gold_disturbance d
JOIN force.gold.deployment p ON p.disturbance_id = d.disturbance_id
WHERE d.sector_id = '<sector_b>'
  AND d.detected_at >= TIMESTAMP '<t0_utc>'
  AND p.decided_by = 'user'
  AND array_contains(p.jedi_ids, '<jedi_x>');

-- (p7-4) B'S LATER DECISION, FOR THE RECORD. Read by eye: the agent's decision on B, or a manual deploy if the optional step was done; zero rows
--   while B is still undecided.
SELECT d.sector_id, p.decision, p.decided_by, p.jedi_ids, p.starship_id, p.decided_at, p.released_at, length(p.rationale) AS rationale_chars
FROM force.gold.gold_disturbance d
JOIN force.gold.deployment p ON p.disturbance_id = d.disturbance_id
WHERE d.sector_id = '<sector_b>'
  AND d.detected_at >= TIMESTAMP '<t0_utc>'
ORDER BY p.decided_at;

-- (p7-5) EVERY DEPLOYMENT ROW SINCE <t0_utc>, WITH ITS DECIDED_BY. Read by eye: the agent's rows and any manual row, newest last.
SELECT p.deployment_id, p.disturbance_id, p.decided_by, p.decision, p.jedi_ids, p.starship_id, p.decided_at
FROM force.gold.deployment p
WHERE p.decided_at >= TIMESTAMP '<t0_utc>'
ORDER BY p.decided_at, p.deployment_id;
