#!/usr/bin/env python3
"""Snapshot the SWAPI reference data (doc 07, Phase 1 step 1).

Pulls planets, people, species and starships from swapi.info and writes the raw JSON to
data/swapi_snapshot/. Standard library only.

Every request sends the project User-Agent (doc 04). The default Python-urllib UA gets a
Cloudflare 403 / error code 1010 that looks like blocked egress but is not (doc 05).

Nothing is written unless every resource fetched and validated, and existing snapshot files are
not overwritten without --force: the snapshot feeds the frozen enrichment layer (doc 08).

Usage:
    python scripts/fetch_swapi.py [--out-dir DIR] [--force]
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "Force_Balance_Pipeline/0.1 (+https://github.com/jivejong/JiveRepo/tree/main/Force_Balance_Pipeline)"
BASE = "https://swapi.info/api"
RESOURCES = ("planets", "people", "species", "starships")
# Doc 01 D2: "roughly 60 planets, 80 people, 37 species, 36 starships". Informational only.
EXPECTED = {"planets": 60, "people": 80, "species": 37, "starships": 36}
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "swapi_snapshot"
RETRIES = 3


def http_get(url):
    """GET a URL with the explicit User-Agent; retry on 5xx and network errors."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code == 403 and b"1010" in body:
                sys.exit(f"{url}: 403 'error code: 1010' is a Cloudflare client-fingerprint "
                         "block, not blocked egress")
            if e.code < 500 or attempt == RETRIES:
                sys.exit(f"{url}: HTTP {e.code}")
        except urllib.error.URLError as e:
            if attempt == RETRIES:
                sys.exit(f"{url}: {e.reason}")
        time.sleep(2 * attempt)


def fetch_resource(name):
    """Return (bytes to write, records).

    The bytes are exactly what the server sent when the endpoint returns the whole list in one
    response. A paginated response ({"results": [...], "next": ...}) is merged into one list.
    """
    raw = http_get(f"{BASE}/{name}")
    data = json.loads(raw)
    if isinstance(data, list):
        return raw, data
    if not isinstance(data, dict) or "results" not in data:
        sys.exit(f"{BASE}/{name}: unexpected response shape ({type(data).__name__})")
    records, page = [], data
    while True:
        records.extend(page["results"])
        if not page.get("next"):
            break
        page = json.loads(http_get(page["next"]))
    return (json.dumps(records, indent=2) + "\n").encode("utf-8"), records


def validate(name, records):
    if not records:
        sys.exit(f"{name}: no records returned")
    bad = [r for r in records if not isinstance(r, dict) or "url" not in r]
    if bad:
        sys.exit(f"{name}: {len(bad)} record(s) without a `url` field; refusing to write")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--force", action="store_true", help="overwrite existing snapshot files")
    args = p.parse_args()

    existing = [args.out_dir / f"{n}.json" for n in RESOURCES if (args.out_dir / f"{n}.json").exists()]
    if existing and not args.force:
        sys.exit("snapshot already exists (use --force to overwrite): "
                 + ", ".join(str(f) for f in existing))

    fetched = {}
    for name in RESOURCES:
        raw, records = fetch_resource(name)
        validate(name, records)
        fetched[name] = (raw, records)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"User-Agent: {UA}")
    print(f"{'resource':<10} {'records':>7} {'expected~':>9} {'bytes':>8}  sha256[:12]")
    for name, (raw, records) in fetched.items():
        (args.out_dir / f"{name}.json").write_bytes(raw)
        print(f"{name:<10} {len(records):>7} {EXPECTED[name]:>9} {len(raw):>8}  "
              f"{hashlib.sha256(raw).hexdigest()[:12]}")
    print(f"wrote {len(fetched)} files to {args.out_dir}")


if __name__ == "__main__":
    main()
