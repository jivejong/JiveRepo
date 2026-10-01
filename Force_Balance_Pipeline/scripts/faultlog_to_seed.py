#!/usr/bin/env python3
"""Convert a pulled fault_injection.jsonl (doc 04, "Fault injection"; edge/probe/faults.py's own record shape)
into a frozen dbt seed for Phase 4's reconciliation (doc 04: "converted to a frozen dbt seed
(fault_injection_<period>.csv, scripts/faultlog_to_seed.py, with a test) ... joined against silver.rejects on
event_id in a dbt test" -- that join is ingest/phase4_checkpoint.sql's p4-12).

Columns: event_id and expected_reject_reason only -- the two columns p4-12's join and reconciliation actually
use. The log carries more (fault, scan_id, channel, original, injected, event_time, sector_id, logged_utc); none
of it is dropped from the log itself (scp pulls the real file, this script only shapes what gets committed), it's
just not duplicated into the seed, which is frozen and reviewed like the enrichment seeds (doc 08) and should
carry only what the dbt test needs.

Usage:
    python scripts/faultlog_to_seed.py fault_injection.jsonl --out warehouse/dbt/seeds/fault_injection_<period>.csv

Refuses to overwrite an existing seed (--force to override) -- once committed, a seed is frozen, the same
discipline the enrichment seeds use (doc 08).
"""
import argparse
import csv
import json
import sys
from pathlib import Path

FIELDNAMES = ("event_id", "expected_reject_reason")


def convert(lines):
    """Yield {event_id, expected_reject_reason} dicts from fault_injection.jsonl lines, in order. Blank lines
    (a trailing newline, most commonly) are skipped; everything else must be a complete JSON object with both
    keys, or this raises -- a malformed log line should stop the conversion, not silently drop a fault."""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        yield {"event_id": record["event_id"], "expected_reject_reason": record["expected_reject_reason"]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("log_path", type=Path, help="the pulled fault_injection.jsonl")
    p.add_argument("--out", type=Path, required=True,
                   help="seed CSV path, e.g. warehouse/dbt/seeds/fault_injection_<period>.csv")
    p.add_argument("--force", action="store_true", help="overwrite an existing seed")
    args = p.parse_args(argv)

    if args.out.exists() and not args.force:
        raise SystemExit(f"{args.out} already exists -- a seed is frozen once committed; use --force to overwrite")

    with args.log_path.open(encoding="utf-8") as f:
        rows = list(convert(f))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow(row)

    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
