"""The serving pull core (doc 05 "Serving (Phase 7)": "Reads"): read the warehouse sources with read-only SELECTs, then write them to
the local Postgres in ONE transaction.

run_pulls(client, conn, now) does, in order: (1) read the watermarks and decide whether the dims are due, then end that transaction;
(2) every warehouse read, in a fixed order (dims if due, sector_baseline, disturbance, deployment, source_health, sector_reading,
probe_reading, rejects), each through client.read (two attempts, 5 s apart) with expected_types; (3) one Postgres transaction: full tables are replaced
(DELETE then INSERT), the source_health snapshot is added, the incremental tables are upserted, the watermarks move, and 30-day
retention is applied. Any error rolls everything back and is re-raised; a failed read changes nothing because no write has started.
Watermarks: sector_reading on _gold_built_at (upsert, because a probe row is recomputed in place); probe_reading and rejects on
arrival_ts with a 1-hour overlap (insert-only, DO NOTHING). With no watermark yet, an incremental source backfills one UTC day per
statement. Table names come only from config.SOURCES.

expected_types are Databricks manifest type_names. DOCUMENTED (dbt yml, seeds' column_types or create_deployment.sql): deployment, the
dims, scan_completeness, sample_count. INFERRED from the model SQL and not yet observed on the live warehouse (the first live pull is the
check; a wrong one raises SchemaMismatch and writes nothing): every gold_sector_reading, gold_disturbance, gold_sector_baseline,
silver_probe_reading, silver_rejects and silver_source_health column, including BIGINT shown as LONG, count(*) columns as LONG and
VARIANT shown as VARIANT.
"""
from datetime import timedelta

from psycopg.types.json import Jsonb

from serving import config

DEFAULT_BACKFILL_DAYS = 7
DIMS_MAX_AGE = timedelta(hours=24)
RETENTION = timedelta(days=30)
OVERLAP = timedelta(hours=1)
BACKFILL_TAIL = timedelta(hours=1)

S, D, B, T, I, L, V, A = "STRING", "DOUBLE", "BOOLEAN", "TIMESTAMP", "INT", "LONG", "VARIANT", "ARRAY"


def _spec(key, table, types, mode, **extra):
    return dict(key=key, table=table, cols=tuple(types), types=dict(types), mode=mode, **extra)


SPECS = {
    "dim_sector": _spec("dim_sector", "dim_sector", {
        "sector_id": S, "sector_name": S, "climate": S, "terrain": S, "population": D, "diameter_km": D, "system_name": S,
        "region": S, "midi_baseline": D, "midi_sigma": D, "kyber_baseline": D, "kyber_sigma": D, "dark_baseline": D, "dark_sigma": D,
        "dark_spike_probability": D, "description": S, "force_history": S, "canon_confidence": D, "is_unknown": B}, "dims"),
    "dim_jedi": _spec("dim_jedi", "dim_jedi", {
        "jedi_id": S, "jedi_name": S, "species_id": S, "homeworld_sector_id": S, "rank": S, "primary_specialty": S,
        "secondary_specialty": S, "power_rating": I, "lightsaber_form": S, "notable_for": S, "canon_confidence": D}, "dims"),
    "dim_starship": _spec("dim_starship", "dim_starship", {
        "starship_id": S, "starship_name": S, "model": S, "starship_class": S, "hyperdrive_rating": D, "mglt": I, "crew": I,
        "passengers": I}, "dims"),
    "sector_baseline": _spec("sector_baseline", "sector_baseline", {
        "sector_id": S, "channel": S, "mean_90d": D, "stddev_90d": D, "sample_count": L, "computed_at": T}, "full"),
    "disturbance": _spec("disturbance", "disturbance", {
        "disturbance_id": S, "sector_id": S, "detected_at": T, "scan_id": S, "imbalance_score": D, "z_midi": D, "z_kyber": D,
        "z_dark": D, "signature": S, "severity": D, "is_report_sourced": B, "report_description": S, "report_relevance": D,
        "sustained_scans": L, "cooldown_conflict": B, "agent_processed": B}, "full"),
    "deployment": _spec("deployment", "deployment", {
        "deployment_id": S, "disturbance_id": S, "decided_by": S, "decision": S, "jedi_ids": A, "starship_id": S, "eta_hours": D,
        "released_at": T, "rationale": S, "context_snapshot": V, "signature_at_decision": S, "model": S, "decision_latency_ms": L,
        "guardrail_overrides": V, "decided_at": T}, "full", json_cols=("jedi_ids", "context_snapshot", "guardrail_overrides")),
    "source_health": _spec("source_health", "source_health_snapshot", {
        "source_id": S, "current_mode": S, "last_seen_at": T, "last_scan_id": S, "scan_completeness": B}, "snapshot"),
    "sector_reading": _spec("sector_reading", "sector_reading", {
        "event_id": S, "sector_id": S, "scan_id": S, "event_time": T, "source_type": S, "midichlorian_ppm": D, "kyber_resonance": D,
        "dark_side_activity": D, "z_midi": D, "z_kyber": D, "z_dark": D, "imbalance_score": D, "signature": S, "channels_present": I,
        "is_replayed": B, "_gold_built_at": T}, "incremental", wm_col="_gold_built_at", page_col="event_time", overlap=timedelta(0),
        conflict="update"),
    "probe_reading": _spec("probe_reading", "probe_reading", {
        "event_id": S, "source_id": S, "sector_id": S, "scan_id": S, "event_time": T, "arrival_ts": T, "mode": S, "is_synthetic": B,
        "ingest_lag_seconds": L, "is_replayed": B, "was_buffered": B, "is_partial": B, "channels_present": I},
        "incremental", wm_col="arrival_ts", page_col="arrival_ts", overlap=OVERLAP, conflict="nothing"),
    "rejects": _spec("rejects", "rejects", {
        "event_id": S, "source_id": S, "sector_id": S, "scan_id": S, "event_time": T, "arrival_ts": T, "mode": S, "reject_reason": S},
        "incremental", wm_col="arrival_ts", page_col="arrival_ts", overlap=OVERLAP, conflict="nothing"),
}
DIM_KEYS = ("dim_sector", "dim_jedi", "dim_starship")
READ_ORDER = ("sector_baseline", "disturbance", "deployment", "source_health", "sector_reading", "probe_reading", "rejects")
FULL_KEYS = ("sector_baseline", "disturbance", "deployment")
INCREMENTAL_KEYS = ("sector_reading", "probe_reading", "rejects")
RETENTION_COLUMNS = (("sector_reading", "event_time"), ("probe_reading", "arrival_ts"), ("rejects", "arrival_ts"),
                     ("source_health_snapshot", "published_at"))


def _select(spec, where=""):
    return f"SELECT {', '.join(spec['cols'])} FROM {config.SOURCES[spec['key']]}{where}"


def build_select(spec, wm=None, start=None, end=None):
    """(sql, named params) for one statement. No SELECT *, no ORDER BY. Incremental with a watermark: WHERE <wm_col> > :wm (the stored
    watermark minus the source's overlap). Backfill page: WHERE <page_col> >= :start AND <page_col> < :end."""
    if spec["mode"] in ("full", "dims", "snapshot"):
        return _select(spec), {}
    if wm is not None:
        return _select(spec, f" WHERE {spec['wm_col']} > :wm"), {"wm": (wm - spec["overlap"], "TIMESTAMP")}
    col = spec["page_col"]
    return _select(spec, f" WHERE {col} >= :start AND {col} < :end"), {"start": (start, "TIMESTAMP"), "end": (end, "TIMESTAMP")}


def backfill_pages(now, days):
    """days + 1 (start, end) UTC day pages ending at now + 1 hour."""
    first = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    pages = []
    for i in range(days + 1):
        start = first + timedelta(days=i)
        pages.append((start, now + BACKFILL_TAIL if i == days else start + timedelta(days=1)))
    return pages


def dims_due(conn, now):
    """True when a dim table is empty, or the 'dims' watermark is missing or older than 24 hours."""
    for key in DIM_KEYS:
        if not conn.execute(f"SELECT EXISTS (SELECT 1 FROM serving.{SPECS[key]['table']})").fetchone()[0]:
            return True
    row = conn.execute("SELECT value FROM serving.watermark WHERE source = 'dims'").fetchone()
    return row is None or now - row[0] > DIMS_MAX_AGE


def _watermarks(conn):
    return dict(conn.execute("SELECT source, value FROM serving.watermark").fetchall())


def _plan(due, wms, now, backfill_days):
    """The ordered warehouse reads: (spec key, [(sql, params), ...])."""
    plan = []
    for key in (DIM_KEYS if due else ()):
        plan.append((key, [build_select(SPECS[key])]))
    for key in READ_ORDER:
        spec = SPECS[key]
        if spec["mode"] != "incremental":
            plan.append((key, [build_select(spec)]))
        elif key in wms:
            plan.append((key, [build_select(spec, wm=wms[key])]))
        else:
            plan.append((key, [build_select(spec, start=s, end=e) for s, e in backfill_pages(now, backfill_days)]))
    return plan


def _value(spec, col, v):
    return Jsonb(v) if v is not None and col in spec.get("json_cols", ()) else v


def _insert(cur, spec, rows, extra_first=None, conflict=None):
    cols = spec["cols"]
    names = (["published_at"] if extra_first is not None else []) + list(cols)
    sql = f"INSERT INTO serving.{spec['table']} ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})"
    if conflict == "update":
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "event_id")
        sql += f" ON CONFLICT (event_id) DO UPDATE SET {sets}"
    elif conflict == "nothing":
        sql += " ON CONFLICT (event_id) DO NOTHING"
    data = [(([extra_first] if extra_first is not None else []) + [_value(spec, c, r[c]) for c in cols]) for r in rows]
    if data:
        cur.executemany(sql, data)


def run_pulls(client, conn, now, backfill_days=DEFAULT_BACKFILL_DAYS):
    """One pull. Returns {"statements": n, "rows": {serving table: rows pulled}}; raises (after a rollback) on any error."""
    before = client.statements
    wms = _watermarks(conn)
    due = dims_due(conn, now)
    conn.commit()                                           # end the read transaction before the long warehouse reads
    results = {}
    for key, statements in _plan(due, wms, now, backfill_days):
        spec = SPECS[key]
        rows = []
        for sql, params in statements:
            rows.extend(client.read(sql, params, expected_types=spec["types"]))
        results[key] = rows
    try:
        with conn.cursor() as cur:
            for key in FULL_KEYS + (DIM_KEYS if due else ()):
                spec = SPECS[key]
                cur.execute(f"DELETE FROM serving.{spec['table']}")
                _insert(cur, spec, results[key])
            if due:
                _put_watermark(cur, "dims", now)
            _insert(cur, SPECS["source_health"], results["source_health"], extra_first=now)
            for key in INCREMENTAL_KEYS:
                spec = SPECS[key]
                _insert(cur, spec, results[key], conflict=spec["conflict"])
                if results[key]:
                    _put_watermark(cur, key, max(r[spec["wm_col"]] for r in results[key]))
                elif key != "sector_reading" and key not in wms:
                    # An empty backfill: without a watermark every run would backfill this source again, so it starts at the
                    # run's time (arrival_ts is a landing time, so rows that land later are newer, with the 1-hour overlap).
                    _put_watermark(cur, key, now)
            for table, column in RETENTION_COLUMNS:
                cur.execute(f"DELETE FROM serving.{table} WHERE {column} < %s", (now - RETENTION,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    counts = {SPECS[k]["table"]: len(rows) for k, rows in results.items()}
    return {"statements": client.statements - before, "rows": counts}


def _put_watermark(cur, source, value):
    cur.execute("INSERT INTO serving.watermark (source, value) VALUES (%s, %s) "
                "ON CONFLICT (source) DO UPDATE SET value = EXCLUDED.value", (source, value))
