-- The serving schema of the local Postgres (doc 05 "Serving (Phase 7)"; doc 01 D4 amended). Idempotent: safe to apply twice.
-- Columns mirror the prod gold and silver tables the puller reads (round0-report C1). No views yet. deployment has no foreign
-- key on purpose: a deployment whose incident has vanished (identities can shift on a full refresh) is kept and shown as orphaned.
CREATE SCHEMA IF NOT EXISTS serving;

CREATE TABLE IF NOT EXISTS serving.dim_sector (
  sector_id text PRIMARY KEY, sector_name text, climate text, terrain text, population double precision,
  diameter_km double precision, system_name text, region text, midi_baseline double precision, midi_sigma double precision,
  kyber_baseline double precision, kyber_sigma double precision, dark_baseline double precision, dark_sigma double precision,
  dark_spike_probability double precision, description text, force_history text, canon_confidence double precision,
  is_unknown boolean
);

CREATE TABLE IF NOT EXISTS serving.dim_jedi (
  jedi_id text PRIMARY KEY, jedi_name text, species_id text, homeworld_sector_id text, rank text, primary_specialty text,
  secondary_specialty text, power_rating integer, lightsaber_form text, notable_for text, canon_confidence double precision
);

CREATE TABLE IF NOT EXISTS serving.dim_starship (
  starship_id text PRIMARY KEY, starship_name text, model text, starship_class text, hyperdrive_rating double precision,
  mglt integer, crew integer, passengers integer
);

CREATE TABLE IF NOT EXISTS serving.sector_baseline (
  sector_id text, channel text, mean_90d double precision, stddev_90d double precision, sample_count bigint,
  computed_at timestamptz, PRIMARY KEY (sector_id, channel)
);

CREATE TABLE IF NOT EXISTS serving.sector_reading (
  event_id text PRIMARY KEY, sector_id text NOT NULL, scan_id text, event_time timestamptz NOT NULL, source_type text NOT NULL,
  midichlorian_ppm double precision, kyber_resonance double precision, dark_side_activity double precision,
  z_midi double precision, z_kyber double precision, z_dark double precision, imbalance_score double precision, signature text,
  channels_present integer, is_replayed boolean, _gold_built_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sector_reading_sector_time ON serving.sector_reading (sector_id, event_time);
CREATE INDEX IF NOT EXISTS sector_reading_built_at ON serving.sector_reading (_gold_built_at);

CREATE TABLE IF NOT EXISTS serving.disturbance (
  disturbance_id text PRIMARY KEY, sector_id text NOT NULL, detected_at timestamptz NOT NULL, scan_id text,
  imbalance_score double precision, z_midi double precision, z_kyber double precision, z_dark double precision, signature text,
  severity double precision, is_report_sourced boolean, report_description text, report_relevance double precision,
  sustained_scans integer, cooldown_conflict boolean, agent_processed boolean
);

CREATE TABLE IF NOT EXISTS serving.deployment (
  deployment_id text PRIMARY KEY, disturbance_id text NOT NULL, decided_by text NOT NULL, decision text NOT NULL,
  jedi_ids jsonb, starship_id text, eta_hours double precision, released_at timestamptz, rationale text NOT NULL,
  context_snapshot jsonb, signature_at_decision text, model text, decision_latency_ms bigint, guardrail_overrides jsonb,
  decided_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS deployment_disturbance ON serving.deployment (disturbance_id);

CREATE TABLE IF NOT EXISTS serving.probe_reading (
  event_id text PRIMARY KEY, source_id text, sector_id text, scan_id text, event_time timestamptz NOT NULL,
  arrival_ts timestamptz NOT NULL, mode text, is_synthetic boolean, ingest_lag_seconds bigint, is_replayed boolean,
  was_buffered boolean, is_partial boolean, channels_present integer
);
CREATE INDEX IF NOT EXISTS probe_reading_arrival ON serving.probe_reading (arrival_ts);
CREATE INDEX IF NOT EXISTS probe_reading_scan ON serving.probe_reading (scan_id);

CREATE TABLE IF NOT EXISTS serving.rejects (
  event_id text PRIMARY KEY, source_id text, sector_id text, scan_id text, event_time timestamptz,
  arrival_ts timestamptz NOT NULL, mode text, reject_reason text NOT NULL
);
CREATE INDEX IF NOT EXISTS rejects_arrival ON serving.rejects (arrival_ts);

CREATE TABLE IF NOT EXISTS serving.source_health_snapshot (
  published_at timestamptz, source_id text, current_mode text, last_seen_at timestamptz, last_scan_id text,
  scan_completeness double precision, PRIMARY KEY (published_at, source_id)
);

CREATE TABLE IF NOT EXISTS serving.publish_log (
  publish_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('fixture', 'live')),
  started_at timestamptz NOT NULL, finished_at timestamptz, job1_run_id bigint, job1_run_end timestamptz,
  status text NOT NULL CHECK (status IN ('ok', 'skipped', 'failed')),
  statements integer, row_counts jsonb, error text
);

CREATE TABLE IF NOT EXISTS serving.watermark (
  source text PRIMARY KEY, value timestamptz NOT NULL
);
