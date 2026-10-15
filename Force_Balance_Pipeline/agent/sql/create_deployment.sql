CREATE TABLE IF NOT EXISTS {deployment} (
  deployment_id STRING NOT NULL,
  disturbance_id STRING NOT NULL,
  decided_by STRING NOT NULL,
  decision STRING NOT NULL,
  jedi_ids ARRAY<STRING>,
  starship_id STRING,
  eta_hours DOUBLE,
  released_at TIMESTAMP,
  rationale STRING NOT NULL,
  context_snapshot VARIANT,
  signature_at_decision STRING,
  model STRING,
  decision_latency_ms BIGINT,
  guardrail_overrides VARIANT,
  decided_at TIMESTAMP NOT NULL
) USING DELTA
