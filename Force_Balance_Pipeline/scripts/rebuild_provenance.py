#!/usr/bin/env python3
"""Rebuild warehouse/dbt/seeds/ENRICHMENT_PROVENANCE.md from the provenance sidecars (doc 08).

The file is generated: promote (enrich_planets.py / enrich_jedi.py --from-response --write) rewrites
it, and this script rebuilds it from the sidecars already on disk. It reads only
seeds/*.provenance.json and writes only ENRICHMENT_PROVENANCE.md (plus one sidecar field with
--reviewed-by). No seed CSV is touched, and there is no API call.

'Reviewed by' cannot be typed into the generated file, because the next rebuild would overwrite it.
It is recorded in the sidecar instead:

Usage:
    python scripts/rebuild_provenance.py
    python scripts/rebuild_provenance.py --seed dim_sector --reviewed-by "J Lee"
"""
import argparse
import json
import sys
from pathlib import Path

import enrich_common as ec


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out-dir", type=Path, default=ec.SEEDS_DIR,
                        help=f"the seeds folder holding the sidecars (default: {ec.SEEDS_DIR})")
    parser.add_argument("--seed", help="with --reviewed-by: the seed whose sidecar records the reviewer, "
                                       "for example dim_sector")
    parser.add_argument("--reviewed-by", metavar="NAME",
                        help="record NAME as the reviewer of --seed in its sidecar, then rebuild")
    args = parser.parse_args()

    if bool(args.seed) != bool(args.reviewed_by):
        raise SystemExit("--seed and --reviewed-by go together")
    if args.reviewed_by:
        side = ec.sidecar_path(args.out_dir, args.seed)
        if not side.exists():
            raise SystemExit(f"{side} does not exist; promote {args.seed} first")
        rec = json.loads(side.read_text(encoding="utf-8"))
        rec["reviewed_by"] = args.reviewed_by.strip()
        ec.write_sidecar(args.out_dir, args.seed, rec)
        print(f"recorded reviewer {rec['reviewed_by']!r} in {side.name}")

    path, count = ec.write_provenance_md(args.out_dir)
    if path is None:
        raise SystemExit(f"no *{ec.SIDECAR_SUFFIX} sidecar in {args.out_dir}; nothing to build from")
    print(f"wrote {path} from {count} sidecar(s); no CSV was touched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
