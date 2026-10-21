"""The SQL of the dashboard's read API (doc 07 Phase 7 "Panels"; doc 05 "Serving (Phase 7)": "Dashboard backend").

One constant per query. Values reach a statement only as psycopg parameters (%s or %(name)s); nothing is formatted into the text. Every
query reads the serving views or tables of the local Postgres, never the lakehouse.
"""
DATABASE = "SELECT current_database()"

META = ("SELECT kind, status, finished_at, job1_run_end, statements FROM serving.publish_log "
        "ORDER BY started_at DESC, publish_id DESC LIMIT 1")

GALAXY = "SELECT * FROM serving.v_galaxy ORDER BY region, sector_name"

PLANET = "SELECT * FROM serving.dim_sector WHERE sector_id = %s"
PLANET_BANDS = "SELECT * FROM serving.v_baseline_bands WHERE sector_id = %s ORDER BY channel"
PLANET_READINGS = (
    "SELECT event_time, source_type, midichlorian_ppm, kyber_resonance, dark_side_activity, z_midi, z_kyber, z_dark, "
    "imbalance_score, signature, is_replayed FROM serving.sector_reading "
    "WHERE sector_id = %(sector)s AND event_time > "
    "(SELECT max(event_time) FROM serving.sector_reading WHERE sector_id = %(sector)s) - interval '72 hours' "
    "ORDER BY event_time ASC")
PLANET_INCIDENTS = "SELECT * FROM serving.v_incidents WHERE sector_id = %s ORDER BY detected_at DESC, disturbance_id"

SIGNATURES = "SELECT * FROM serving.v_signature_breakdown ORDER BY signature"

SOURCE_HEALTH = "SELECT * FROM serving.v_source_health ORDER BY source_id"
SCAN_COMPLETENESS = ("SELECT * FROM (SELECT * FROM serving.v_scan_completeness ORDER BY scan_event_time DESC, scan_id DESC LIMIT 96) t "
                     "ORDER BY scan_event_time ASC, scan_id ASC")

INGEST_LAG = "SELECT * FROM serving.v_ingest_lag_histogram ORDER BY bin_order"

REJECTION_RATE = "SELECT * FROM serving.v_rejection_rate ORDER BY hour"
REJECTS_BY_REASON = "SELECT * FROM serving.v_rejects_by_reason ORDER BY hour, reject_reason"

INCIDENTS = ("SELECT * FROM serving.v_incidents WHERE (%(status)s::text IS NULL OR status = %(status)s) "
             "ORDER BY detected_at DESC, disturbance_id LIMIT %(limit)s")
DEPLOYMENTS = "SELECT * FROM serving.v_deployment_log ORDER BY decided_at DESC, deployment_id LIMIT %s"
