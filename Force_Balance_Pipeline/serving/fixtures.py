"""Fixture loader for the local serving Postgres (doc 05 "Serving (Phase 7)"; doc 07 Phase 7, "Order": panels first, on fixture data).

    serving\\.venv\\Scripts\\python.exe -m serving.fixtures [--database NAME] [--now ISO8601] [--reset]

This is DISPLAY DATA for building the UI. Its imbalance scores follow designed series, not doc 03's formula, and nothing here is a
measurement. For a given --now (default: the current UTC time rounded down to :00/:15/:30/:45) the load is deterministic: one
random.Random(7) and a fixed order. The dims come from the dbt seeds. Release times and ETAs call agent.distance.
Refuses (exit 2, nothing changed) when serving.publish_log holds a 'live' row, --reset included, and when a table is not empty
unless --reset is given; --reset truncates every serving table in one transaction before loading.
"""
import argparse
import csv
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from agent.distance import distance_units, eta_hours, released_at
from serving import config, schema

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "warehouse" / "dbt" / "seeds"
SOURCE_ID = "probe-01"
SCAN_MINUTES = 15
SCANS = 72 * 60 // SCAN_MINUTES                   # 288 scans, 72 hours
PER_HOUR = 60 // SCAN_MINUTES
TABLES = ("dim_sector", "dim_jedi", "dim_starship", "sector_baseline", "sector_reading", "disturbance", "deployment",
          "probe_reading", "rejects", "source_health_snapshot", "publish_log", "watermark")
# The warehouse vocabulary of silver_rejects.reject_reason (edge/probe/faults.py maps doc 04's fault names to these).
REJECT_REASONS = ("null_required_field", "out_of_range", "unknown_sector", "impossible_timestamp")

SECTOR_FLOATS = ("population", "diameter_km", "midi_baseline", "midi_sigma", "kyber_baseline", "kyber_sigma", "dark_baseline",
                 "dark_sigma", "dark_spike_probability", "canon_confidence")


class Refused(Exception):
    """The loader will not run; the message says why (never a value from .env.serving)."""


def floor_quarter(moment):
    return moment.replace(minute=moment.minute // 15 * 15, second=0, microsecond=0)


def read_seed(name, floats=(), ints=(), bools=()):
    rows = []
    with open(SEEDS / f"{name}.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row = {k: (v if v != "" else None) for k, v in row.items()}
            for k in floats:
                row[k] = float(row[k]) if row[k] is not None else None
            for k in ints:
                row[k] = int(row[k]) if row[k] is not None else None
            for k in bools:
                row[k] = row[k] is not None and row[k].strip().lower() == "true"
            rows.append(row)
    return rows


def require(rows, key, ids, what):
    have = {r[key] for r in rows}
    missing = [i for i in ids if i not in have]
    if missing:
        raise Refused(f"{what} not in the seeds: {', '.join(missing)}")


def series_value(sector_id, i, rng):
    """(imbalance_score override or None, signature override or None) for a designed series; i = scans before now."""
    if sector_id == "geonosis":
        rise = {27: 4.2, 26: 8.4, 25: 12.6, 24: 16.8}
        decay = {23: 5.0, 22: 3.5, 21: 2.5, 20: 2.0}
        if i in rise:
            return rise[i], "sith_presence"
        if i in decay:
            return decay[i], "sith_presence"
    if sector_id == "naboo" and i < 8:
        return round(rng.uniform(4.6, 5.2), 2), None
    if sector_id == "bespin" and i < 2:
        return (6.9 if i == 0 else 6.6), "civil_unrest"
    return None, None


def build(now):
    """Every table's rows for one --now, as dicts of lists of tuples. Pure: no database."""
    rng = random.Random(7)
    sectors = read_seed("dim_sector", floats=SECTOR_FLOATS, bools=("is_unknown",))
    jedi = read_seed("dim_jedi", floats=("canon_confidence",), ints=("power_rating",))
    ships = read_seed("dim_starship", floats=("hyperdrive_rating",), ints=("mglt", "crew", "passengers"))
    require(sectors, "sector_id", ("geonosis", "naboo", "bespin", "kamino", "coruscant", "tatooine"), "sector")
    require(jedi, "jedi_id", ("kit_fisto", "anakin_skywalker", "mace_windu"), "Jedi")
    require(ships, "starship_id", ("arc_170", "jedi_interceptor"), "starship")
    by_sector = {s["sector_id"]: s for s in sectors}
    ago = lambda **kw: now - timedelta(**kw)  # noqa: E731

    out = {}
    out["dim_sector"] = [tuple(s[c] for c in ("sector_id", "sector_name", "climate", "terrain", "population", "diameter_km",
                                              "system_name", "region", "midi_baseline", "midi_sigma", "kyber_baseline",
                                              "kyber_sigma", "dark_baseline", "dark_sigma", "dark_spike_probability",
                                              "description", "force_history", "canon_confidence", "is_unknown")) for s in sectors]
    out["dim_jedi"] = [tuple(j[c] for c in ("jedi_id", "jedi_name", "species_id", "homeworld_sector_id", "rank", "primary_specialty",
                                            "secondary_specialty", "power_rating", "lightsaber_form", "notable_for",
                                            "canon_confidence")) for j in jedi]
    out["dim_starship"] = [tuple(s[c] for c in ("starship_id", "starship_name", "model", "starship_class", "hyperdrive_rating",
                                                "mglt", "crew", "passengers")) for s in ships]
    computed = ago(hours=6)
    out["sector_baseline"] = [(s["sector_id"], ch, s[f"{p}_baseline"], s[f"{p}_sigma"], 8640, computed)
                              for s in sectors for ch, p in (("midichlorian", "midi"), ("kyber", "kyber"), ("dark_side", "dark"))]

    # readings: i = scans before now (0 = the newest), k = scan index (0 = the oldest)
    outage = {120: ("DISCONNECTED", 3600), 119: ("DISCONNECTED", 2700), 118: ("DISCONNECTED", 1800), 117: ("DISCONNECTED", 900)}
    negative_scan, short_scan = 50, 40
    readings, probes, zlookup = [], [], {}
    for k in range(SCANS):
        i = SCANS - 1 - k
        when = now - timedelta(minutes=SCAN_MINUTES * i)
        scan_id = f"FIX{k:04d}"
        mode, lag = outage.get(i, ("CONNECTED", 3))
        for n, s in enumerate(sectors):
            sid = s["sector_id"]
            midi = rng.gauss(s["midi_baseline"], s["midi_sigma"])
            kyber = rng.gauss(s["kyber_baseline"], s["kyber_sigma"])
            dark = rng.gauss(s["dark_baseline"], s["dark_sigma"])
            score = abs(rng.gauss(1.5, 0.6))
            override, sig = series_value(sid, i, rng)
            if override is not None:
                score = override
                dark += s["dark_sigma"] * override * 0.8
                midi += s["midi_sigma"] * override * 0.5
            midi, kyber, dark = min(max(midi, 0.0), 30000.0), min(max(kyber, 0.0), 100.0), min(max(dark, 0.0), 100.0)
            z = tuple((v - m) / sg if sg else 0.0 for v, m, sg in ((midi, s["midi_baseline"], s["midi_sigma"]),
                                                                   (kyber, s["kyber_baseline"], s["kyber_sigma"]),
                                                                   (dark, s["dark_baseline"], s["dark_sigma"])))
            zlookup[(sid, i)] = (z, score)
            event_id = f"{scan_id}-{sid}"
            replayed = lag > 1800
            readings.append((event_id, sid, scan_id, when, "probe", midi, kyber, dark, z[0], z[1], z[2], score,
                             sig or "unclassified", 3, replayed, now))
            if i == short_scan and n >= 55:
                continue                                     # the scan with only 55 of its 60 probe_reading rows
            row_lag = -1 if (i == negative_scan and n < 5) else lag
            probes.append((event_id, SOURCE_ID, sid, scan_id, when, when + timedelta(seconds=row_lag), mode, False, row_lag,
                           replayed, mode == "DISCONNECTED", False, 3))
    out["sector_reading"], out["probe_reading"] = readings, probes

    # incidents: (id, sector, delta, signature, severity, report?, processed, sustained)
    specs = [("FIXD1", "geonosis", timedelta(hours=6), "sith_presence", 16.8, False, True, 3),
             ("FIXD2", "kamino", timedelta(hours=30), "force_drain", 9.4, False, True, 2),
             ("FIXD3", "coruscant", timedelta(hours=20), "dark_adept", 29.3, True, True, 1),
             ("FIXD4", "bespin", timedelta(minutes=15), "civil_unrest", 7.2, False, False, 2)]
    dist = []
    for did, sid, delta, sig, sev, report, processed, sustained in specs:
        i = int(delta.total_seconds() // (SCAN_MINUTES * 60))
        z, score = zlookup[(sid, i)]
        scan_id = None if report else f"FIX{SCANS - 1 - i:04d}"
        dist.append((did, sid, now - delta, scan_id, round(sev / 1.4, 2), z[0], z[1], z[2], sig, sev, report,
                     "A hooded figure ignited a red lightsaber in the lower market and was gone." if report else None,
                     0.95 if report else None, sustained, False, processed))
    old = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    dist.append(("FIXD5", "tatooine", old, "FIXOLD", round(8.1 / 1.4, 2), None, None, None, "nexus_awakening", 8.1, False, None,
                 None, 2, False, False))
    out["disturbance"] = dist

    def eta(jedi_ids, sector_id, ship_id):
        region = by_sector[sector_id]["region"]
        home = {j["jedi_id"]: by_sector.get(j["homeworld_sector_id"], {}).get("region") for j in jedi}
        rating = {s["starship_id"]: s["hyperdrive_rating"] for s in ships}[ship_id]
        return max(eta_hours(distance_units(home[j], region), rating) for j in jedi_ids)

    deps = []
    t1 = ago(hours=5.5)
    e1 = eta(["kit_fisto", "anakin_skywalker"], "geonosis", "arc_170")
    deps.append(("FIXDEP1", "FIXD1", "yoda_agent", "deploy", Jsonb(["kit_fisto", "anakin_skywalker"]), "arc_170", e1,
                 released_at(t1, e1), "Geonosis shows a sith presence; two combat Jedi on a fast starfighter.",
                 Jsonb({"severity": 11.9, "signature": "sith_presence"}), "sith_presence", "gemini-3.1-flash-lite", 1900,
                 Jsonb([]), t1))
    t2 = ago(hours=29.5)
    deps.append(("FIXDEP2", "FIXD2", "yoda_agent", "stand_down", Jsonb([]), None, None, None,
                 "No capacity: the fleet cap is full, so no deployment is possible.",
                 Jsonb({"severity": 9.4, "signature": "force_drain"}), "force_drain", "gemini-3.1-flash-lite", 1500,
                 Jsonb([{"reason": "deployment_capacity_reached"}]), t2))
    t3 = ago(hours=19)
    e3 = eta(["mace_windu"], "coruscant", "jedi_interceptor")
    deps.append(("FIXDEP3", "FIXD3", "user", "deploy", Jsonb(["mace_windu"]), "jedi_interceptor", e3, released_at(t3, e3),
                 "Council asked me to act. <b>not bold</b>", Jsonb({"severity": 29.3, "signature": "dark_adept"}), "dark_adept",
                 None, None, Jsonb([]), t3))
    t4 = ago(hours=40)
    deps.append(("FIXDEPO", "FIXORPHAN", "yoda_agent", "stand_down", Jsonb([]), None, None, None,
                 "The incident was below the floor when decided.", Jsonb({"severity": 4.2, "signature": "unclassified"}),
                 "unclassified", "gemini-3.1-flash-lite", 1200, Jsonb([{"reason": "severity_below_threshold"}]), t4))
    out["deployment"] = deps

    rejects = []
    spike_start = ago(hours=12)
    for n in range(20):                                        # the spike: 20 in the hour from 12 h ago
        when = spike_start + timedelta(minutes=3 * n)
        rejects.append((f"FIXREJ{n:03d}", SOURCE_ID, sectors[n % 60]["sector_id"], None, when, when + timedelta(seconds=3),
                        "CONNECTED", REJECT_REASONS[n % 4]))
    for n in range(20, 40):                                    # the rest, outside that hour
        while True:
            when = ago(seconds=rng.randrange(0, 72 * 3600))
            if not spike_start <= when < spike_start + timedelta(hours=1):
                break
        rejects.append((f"FIXREJ{n:03d}", SOURCE_ID, sectors[n % 60]["sector_id"], None, when, when + timedelta(seconds=3),
                        "CONNECTED", rng.choice(REJECT_REASONS)))
    out["rejects"] = rejects

    out["source_health_snapshot"] = [(now - timedelta(hours=h), SOURCE_ID, "CONNECTED", now - timedelta(hours=h), None, 1.0)
                                    for h in (2, 1, 0)]
    out["source_health_snapshot"] = [(p, s, m, ls, f"FIX{SCANS - 1 - h * PER_HOUR:04d}", c)
                                     for (p, s, m, ls, _x, c), h in zip(out["source_health_snapshot"], (2, 1, 0))]
    out["watermark"] = []
    return out


INSERTS = {
    "dim_sector": 19, "dim_jedi": 11, "dim_starship": 8, "sector_baseline": 6, "sector_reading": 16, "disturbance": 16,
    "deployment": 15, "probe_reading": 13, "rejects": 8, "source_health_snapshot": 6,
}


def insert_all(cur, data):
    for table, width in INSERTS.items():
        cur.executemany(f"INSERT INTO serving.{table} VALUES ({', '.join(['%s'] * width)})", data[table])


def load(conn, now, reset=False):
    """Load the fixtures into an open connection. Raises Refused (nothing changed) or returns the row counts."""
    data = build(now)
    schema.apply(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM serving.publish_log WHERE kind = 'live'")
        if cur.fetchone()[0]:
            conn.rollback()
            raise Refused("serving.publish_log has a live row; the fixtures will not touch live data")
        if reset:
            cur.execute("TRUNCATE " + ", ".join(f"serving.{t}" for t in TABLES) + " RESTART IDENTITY")
        else:
            for table in TABLES:
                cur.execute(f"SELECT count(*) FROM serving.{table}")
                if cur.fetchone()[0]:
                    conn.rollback()
                    raise Refused(f"serving.{table} is not empty; use --reset to replace the fixtures")
        insert_all(cur, data)
        counts = {t: len(data[t]) for t in INSERTS}
        counts["publish_log"] = 1
        cur.execute("INSERT INTO serving.publish_log (kind, started_at, finished_at, status, statements, row_counts) "
                    "VALUES ('fixture', %s, %s, 'ok', 0, %s)", (now, now, Jsonb(counts)))
    conn.commit()
    return counts


def parse_now(text):
    if text is None:
        return floor_quarter(datetime.now(timezone.utc))
    moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return (moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="serving.fixtures")
    parser.add_argument("--database", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)
    env = config.load_env()
    now = parse_now(args.now)
    try:
        with config.connect(env, args.database) as conn:
            counts = load(conn, now, args.reset)
    except Refused as e:
        print(f"serving.fixtures: refused: {e}")
        return 2
    print(f"fixtures loaded for now={now:%Y-%m-%dT%H:%M:%SZ}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
