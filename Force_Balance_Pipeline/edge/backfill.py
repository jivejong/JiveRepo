"""The 90-day backfill: generate the NDJSON files and the manifest locally, and verify a regeneration.

  python edge/backfill.py generate --out DIR --earliest-live 2026-09-26T14:27:00.000Z [--only-day -71] [--manifest FILE]
  python edge/backfill.py verify   [--manifest FILE] [--out DIR] [--disk-only]   (default: edge/backfill_manifest.json)

There is no upload command yet: uploading is a separate, approved step. `generate` writes under DIR/scans/ (one
file per scan) and refuses a non-empty DIR or one inside the repository (the data is about 200 MB and is
regenerated from the committed manifest, not committed). --earliest-live is the earliest live probe event already
in bronze (query e0 in ingest/phase2_checkpoint.sql); the window ends at the last 15-minute UTC boundary before it.
--only-day D writes only the scans of day D (D counts back from the window end, as in the texture file: -71 is the
tatooine emergency day), for a dry run; the report still covers the whole window.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forcesim import backfill as bf  # noqa: E402
from forcesim.constants import SCANS_PER_DAY  # noqa: E402
from forcesim.sectors import DEFAULT_SEED as SECTOR_PATH  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402
from forcesim.window import SCANS, OverlapRefused, make_window  # noqa: E402

TEXTURE_PATH = Path(__file__).resolve().parent / "backfill_texture.json"
MANIFEST_PATH = Path(__file__).resolve().parent / "backfill_manifest.json"   # the committed, frozen manifest
REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_utc(text):
    try:
        moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ" if "." in text else "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise SystemExit(f"backfill: {text!r} is not an ISO 8601 UTC time ending in Z, for example 2026-09-26T14:27:00.000Z")
    return moment.replace(tzinfo=timezone.utc)


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate", help="write the files and the manifest")
    g.add_argument("--out", required=True, help="output directory, outside the repository")
    g.add_argument("--earliest-live", required=True, help="the earliest live probe event_time in bronze (UTC, ends in Z)")
    g.add_argument("--end", help="window end (UTC); default: the last 15-minute boundary before --earliest-live")
    g.add_argument("--seed", type=int, default=bf.DEFAULT_RUN_SEED)
    g.add_argument("--texture", default=str(TEXTURE_PATH))
    g.add_argument("--only-day", type=int, help="write only this day's scans (-90..-1, counted back from the window end)")
    g.add_argument("--manifest", help="where to save the manifest (default: OUT/manifest.json)")
    v = sub.add_parser("verify", help="regenerate from the manifest and compare")
    v.add_argument("--out", help="also compare the files in this directory")
    v.add_argument("--manifest", default=str(MANIFEST_PATH), help="default: edge/backfill_manifest.json")
    v.add_argument("--texture", default=str(TEXTURE_PATH))
    v.add_argument("--disk-only", action="store_true",
                   help="only check that the files in --out still match the manifest (no regeneration; fast)")
    return p


def main(argv=None, sectors=None, sector_path=SECTOR_PATH):
    args = build_parser().parse_args(argv)
    sectors = sectors if sectors is not None else load_sectors(sector_path)
    try:
        if args.command == "generate":
            return cmd_generate(args, sectors, sector_path)
        return cmd_verify(args, sectors, sector_path)
    except (bf.BackfillError, OverlapRefused) as e:
        print(f"backfill: refused: {e}", file=sys.stderr)
        return 2


def cmd_generate(args, sectors, sector_path):
    out = Path(args.out).resolve()
    if out == REPO_ROOT or REPO_ROOT in out.parents:
        raise bf.BackfillError(f"{out} is inside the repository; the generated files are not committed. Use a directory outside it")
    live = parse_utc(args.earliest_live)
    window = make_window(live, end=parse_utc(args.end) if args.end else None)
    texture = bf.load_texture(args.texture, sectors)
    scan_range = None
    if args.only_day is not None:
        if not -90 <= args.only_day <= -1:
            raise bf.BackfillError("--only-day must be between -90 and -1")
        first = SCANS + args.only_day * SCANS_PER_DAY
        scan_range = (first, first + SCANS_PER_DAY)
    manifest = bf.generate(sectors, window, texture, args.texture, args.seed, out, scan_range=scan_range,
                           sector_path=sector_path)
    target = Path(args.manifest) if args.manifest else out / "manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline=chr(10)) as f:
        f.write(bf.manifest_text(manifest))
    print(bf.format_report(manifest))
    print(chr(10) + f"manifest written to {target}")
    return 0 if manifest["report"]["riser"]["trend_below_anomaly"] else 3


def cmd_verify(args, sectors, sector_path):
    import json
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if args.disk_only and not args.out:
        raise bf.BackfillError("--disk-only needs --out")
    problems = bf.verify(manifest, sectors, args.texture, out_dir=args.out, sector_path=sector_path,
                         regenerate=not args.disk_only)
    for p in problems:
        print(f"backfill: verify: {p}", file=sys.stderr)
    if not problems:
        what = ("the files on disk" if args.disk_only else "a regeneration and the files on disk" if args.out
                else "a regeneration")
        print(f"verify OK: {what} match {manifest['files']} files, content hash "
              f"{manifest['content_hash']['value']}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
