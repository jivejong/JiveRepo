"""The 90-day backfill: generate the NDJSON files and the manifest locally, and verify a regeneration.

  python edge/backfill.py generate --out DIR --earliest-live 2026-09-26T14:27:00.000Z [--only-day -71] [--manifest FILE]
  python edge/backfill.py verify   [--manifest FILE] [--out DIR] [--disk-only]   (default: edge/backfill_manifest.json)

  python edge/backfill.py upload   --out DIR [--only-day D] [--workers 4] [--manifest FILE] [--state-file FILE]

`generate` writes under DIR/scans/ (one file per scan) and refuses a non-empty DIR or one inside the repository
(the data is about 200 MB and is regenerated from the committed manifest, not committed). `upload` puts the files
in the landing volume through the Files API as the force-bridge service principal (credentials from .env.bridge,
see ingest/bridge/bridge.py), reusing the tested upload core: deterministic names, overwrite=false, and a 409 means
already landed, so a rerun or an interrupted run duplicates nothing. It refuses to start unless a disk-only
`verify` of DIR against the manifest passes. The dt=/hh= prefix is ingest time (doc 02) and is pinned in
DIR/upload_state.json on first use (keyed by the manifest's content hash), so every later run of the same backfill,
including the full upload after a one-day upload, lands in the same directory. If that file is lost, recreate it
with --pin-prefix DT HH (the prefix the files already landed under); never let a new one be assigned. --earliest-live is the earliest live probe event already
in bronze (query e0 in ingest/phase2_checkpoint.sql); the window ends at the last 15-minute UTC boundary before it.
--only-day D writes only the scans of day D (D counts back from the window end, as in the texture file: -71 is the
tatooine emergency day), for a dry run; the report still covers the whole window.
"""
import argparse
import functools
import json
import sys
import time
import urllib.parse
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
    u = sub.add_parser("upload", help="upload the generated files to the landing volume (force-bridge)")
    u.add_argument("--out", required=True, help="the directory `generate` wrote (holds scans/)")
    u.add_argument("--manifest", default=str(MANIFEST_PATH), help="default: edge/backfill_manifest.json")
    u.add_argument("--texture", default=str(TEXTURE_PATH))
    u.add_argument("--only-day", type=int, help="upload only this day's 96 files (-90..-1, as for generate)")
    u.add_argument("--workers", type=int, default=4, help="files in flight at once (default %(default)s)")
    u.add_argument("--state-file", help="the pinned-prefix state file (default: OUT/upload_state.json); must be "
                                        "outside the repository")
    u.add_argument("--pin-prefix", nargs=2, metavar=("DT", "HH"),
                   help="recreate a lost state file with the prefix the files already landed under")
    u.add_argument("--volume-path", help="the landing volume (default: the bridge's, /Volumes/force/raw/telemetry)")
    u.add_argument("--oauth-scope", default="files", help="OAuth scope for the token request (default %(default)s)")
    u.add_argument("--env-file", help="the bridge's credentials file (default .env.bridge at the repo root)")
    u.add_argument("--local-dir", help="rehearsal: write under this directory instead of the volume; no credentials")
    v = sub.add_parser("verify", help="regenerate from the manifest and compare")
    v.add_argument("--out", help="also compare the files in this directory")
    v.add_argument("--manifest", default=str(MANIFEST_PATH), help="default: edge/backfill_manifest.json")
    v.add_argument("--texture", default=str(TEXTURE_PATH))
    v.add_argument("--disk-only", action="store_true",
                   help="only check that the files in --out still match the manifest (no regeneration; fast)")
    return p


def main(argv=None, sectors=None, sector_path=SECTOR_PATH, **hooks):
    """`hooks` (uploader, sleep, clock, now, log) exist for the tests: a stub uploader and a controllable clock."""
    args = build_parser().parse_args(argv)
    sectors = sectors if sectors is not None else load_sectors(sector_path)
    try:
        if args.command == "generate":
            return cmd_generate(args, sectors, sector_path)
        if args.command == "upload":
            return cmd_upload(args, sectors, sector_path, **hooks)
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


def _ingest_modules():
    """The tested upload core and the bridge's credential loading, imported only when an upload runs."""
    sys.path.insert(0, str(REPO_ROOT / "ingest" / "bridge"))
    import bridge
    import upload
    return upload, bridge


def _redactor(uploader):
    """A function that removes the host, the client id and the secret from any text before it is printed."""
    secrets = []
    host = getattr(uploader, "host", None)
    if host:
        secrets += [host, urllib.parse.urlparse(host).netloc]
    tokens = getattr(uploader, "tokens", None)
    secrets += [getattr(tokens, "_client_id", None), getattr(tokens, "_client_secret", None)]
    secrets = [s for s in secrets if s]

    def clean(text):
        text = str(text)
        for s in secrets:
            text = text.replace(s, "<redacted>")
        return text
    return clean


def cmd_upload(args, sectors, sector_path, uploader=None, sleep=None, clock=None, now=None, log=None):
    up, bridge = _ingest_modules()
    sleep, clock, now, log = sleep or time.sleep, clock or time.monotonic, now or time.time, log or print
    if args.workers < 1:
        raise bf.BackfillError("--workers must be at least 1")
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    out = Path(args.out).resolve()
    problems = bf.verify(manifest, sectors, args.texture, out_dir=out, sector_path=sector_path, regenerate=False)
    if problems:
        raise bf.BackfillError("refusing to upload: the files in " + str(out) + " do not verify against the manifest:"
                               + chr(10) + chr(10).join("  " + p for p in problems))
    state = Path(args.state_file).resolve() if args.state_file else out / "upload_state.json"
    if state == REPO_ROOT or REPO_ROOT in state.parents:
        raise bf.BackfillError(f"the state file {state} is inside the repository; it must live outside it (or be "
                               "gitignored). Use --state-file")
    run_id = manifest["content_hash"]["value"]
    files = bf.select_files(out, manifest, args.only_day)      # before a prefix is assigned: a refusal leaves no state
    try:
        if args.pin_prefix:
            dt, hh = up.pin_prefix(state, args.pin_prefix[0], args.pin_prefix[1], run_id)
        else:
            dt, hh = up.fixed_prefix(state, now(), run_id)
    except up.PrefixConflict as e:
        raise bf.BackfillError(str(e)) from None
    if uploader is None:
        ns = argparse.Namespace(local_dir=args.local_dir, env_file=args.env_file, oauth_scope=args.oauth_scope,
                                volume_path=args.volume_path or bridge.DEFAULT_VOLUME_PATH)
        uploader = bridge.build_uploader(ns)
    clean = _redactor(uploader)
    say = lambda text="": log(clean(text))  # noqa: E731
    total_bytes = sum(path.stat().st_size for _, path in files)
    say(f"backfill upload: {len(files)} files ({total_bytes / 1e6:.1f} MB), {args.workers} workers, run {run_id[:12]}, "
        f"prefix dt={dt} hh={hh}")
    say(f"pinned-prefix state file: {state}")
    items = [(up.backfill_relpath(dt, hh, manifest["source_id"], bf.ulid_of(name, manifest["source_id"])),
              functools.partial(Path.read_bytes, path)) for name, path in files]
    step = max(1, len(items) // 10)

    def progress(relpath, result, summary):
        done = summary.uploaded + summary.already_landed + summary.failed
        if done % step == 0 or done == len(items):
            say(f"  {done}/{len(items)} done: {summary.uploaded} uploaded, {summary.already_landed} already landed "
                f"(409), {summary.failed} failed, {summary.retries} retries, {clock() - started:.1f} s")

    started = clock()
    summary = up.upload_tree_parallel(uploader, items, workers=args.workers, sleep=sleep, log=say, progress=progress)
    elapsed = clock() - started
    say("")
    say(f"prefix: dt={dt} hh={hh}")
    say(f"files: {len(files)} selected, {summary.uploaded} uploaded, {summary.already_landed} already landed (409), "
        f"{summary.failed} failed, {summary.retries} retries")
    say(f"elapsed: {elapsed:.1f} s")
    if summary.uploaded and elapsed > 0:
        rate = summary.uploaded / elapsed
        per_file = total_bytes / len(files)
        say(f"rate: {rate:.2f} files/s ({rate * per_file / 1e6:.2f} MB/s) with {args.workers} workers")
        say(f"projection for {bf.SCANS:,} files at this rate: {bf.SCANS / rate / 60:.1f} min "
            f"({bf.SCANS / rate:.0f} s)")
    for relpath in summary.failed_paths:
        say(f"FAILED: {relpath}")
    return 1 if summary.failed else 0


def cmd_verify(args, sectors, sector_path):
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
