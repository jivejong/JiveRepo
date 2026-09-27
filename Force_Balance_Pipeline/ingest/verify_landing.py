#!/usr/bin/env python3
"""Read-only check of what the bridge landed in the UC volume (doc 05, Phase 2): lists the landing volume
through the Files API, downloads each landed file, and verifies it against the probe's sent log.

Uses only GET requests with the bridge's own files-scoped credential (from .env.bridge or the environment),
so it cannot change anything. Checks:
  * the volume's top level holds only the dt=... directories (nothing stray, e.g. no test leftovers)
  * exactly --files files, each at dt=YYYY-MM-DD/hh=HH/probe-01-<ULID>.ndjson
  * each file has --lines lines (default 60), every line a valid doc 02 envelope with is_synthetic=false and
    synthetic_ingest_ts=null, one scan_id per file, the planets in dim_sector order
  * each file is byte-for-byte the probe's sent lines for that scan (order, duplicates, event_time untouched)
Prints paths, sizes and line counts. It never prints a token, a secret, the host or the client id.

Usage:  edge/.venv/Scripts/python.exe ingest/verify_landing.py --sent-log SENT.ndjson [--files 3] [--lines 60]
        edge/.venv/Scripts/python.exe ingest/verify_landing.py --backfill --out DIR [--only-day D] [--sample N]
        (backfill mode: the files under the pinned prefix are exactly the local ones, byte for byte)
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "bridge"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "edge"))

import bridge  # noqa: E402
import files_api  # noqa: E402
from files_api import TransportError  # noqa: E402
from forcesim import backfill as bf  # noqa: E402
from forcesim.envelope import check_envelope  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402
from workspace_check import Reporter, error_summary  # noqa: E402

FILE_RE = re.compile(r"^dt=(\d{4}-\d{2}-\d{2})/hh=(\d{2})/probe-01-([0-9A-HJKMNP-TV-Z]{26})\.ndjson$")
LIVE_FLAGS = '"is_synthetic":false,"synthetic_ingest_ts":null'


class Volume:
    """GET-only view of one volume through the Files API."""

    ATTEMPTS = 3                       # a read is retried on a network error or HTTP 429/5xx, then reported
    RETRY_STATUSES = (429, 500, 502, 503, 504)

    def __init__(self, tokens, host, volume_path, http, report, sleep=time.sleep):
        self.tokens, self.host, self.http, self.report, self.sleep = tokens, host, http, report, sleep
        self.root = "/" + volume_path.strip("/")
        self.retries = 0

    def _get(self, path):
        """GET with a short bounded retry: a check that reads thousands of files should not fail on one dropped
        connection. Only reads are retried (nothing here writes). A persistent failure is reported as a FAIL."""
        for attempt in range(1, self.ATTEMPTS + 1):
            try:
                status, body = self.http("GET", self.host + path,
                                         headers={"Authorization": f"Bearer {self.tokens.token()}"})
            except TransportError as e:
                if attempt < self.ATTEMPTS:
                    self.retries += 1
                    self.sleep(attempt)
                    continue
                self.report.result("FAIL", f"GET {path.split('?')[0]} -> network error after {attempt} attempts ({e})")
                return None, b""
            if status in self.RETRY_STATUSES and attempt < self.ATTEMPTS:
                self.retries += 1
                self.sleep(attempt)
                continue
            return status, body

    def list_dir(self, directory):
        """Every entry of a directory, following pages. Returns None if the listing failed."""
        entries, token = [], None
        while True:
            query = "?page_size=1000" + (f"&page_token={urllib.parse.quote(token)}" if token else "")
            status, body = self._get(f"/api/2.0/fs/directories{urllib.parse.quote(directory, safe='/=')}{query}")
            if status != 200:
                self.report.result("FAIL", f"GET /api/2.0/fs/directories{directory} -> {status}{error_summary(body) if status else ''}")
                return None
            page = json.loads(body or b"{}")
            entries += page.get("contents", [])
            token = page.get("next_page_token")
            if not token:
                return entries

    def walk_files(self):
        """(relative path, size) of every file below the volume root, and the top-level entry names."""
        top = self.list_dir(self.root)
        if top is None:
            return None, None
        files, pending = [], [(self.root, e) for e in top]
        while pending:
            parent, entry = pending.pop()
            name = entry.get("name") or entry.get("path", "").rsplit("/", 1)[-1]
            path = entry.get("path") or f"{parent}/{name}"
            if entry.get("is_directory"):
                children = self.list_dir(path)
                if children is None:
                    return None, None
                pending += [(path, c) for c in children]
            else:
                files.append((path[len(self.root):].lstrip("/"), entry.get("file_size")))
        return sorted(files), sorted(e.get("name") or e.get("path", "").rsplit("/", 1)[-1] for e in top)

    def download(self, relpath):
        status, body = self._get(f"/api/2.0/fs/files{urllib.parse.quote(self.root + '/' + relpath, safe='/=')}")
        if status != 200:
            if status is not None:      # a network failure was already reported by _get
                self.report.result("FAIL", f"GET /api/2.0/fs/files/{relpath} -> {status}{error_summary(body)}")
            return None
        return body


def verify(volume, sent_lines, expected_files, expected_lines, report):
    planets = [s.sector_id for s in load_sectors()]
    sent_by_scan = {}
    for line in sent_lines:
        sent_by_scan.setdefault(json.loads(line)["scan_id"], []).append(line)
    report.line("1. list the landing volume")
    files, top = volume.walk_files()
    if files is None:
        return
    report.result("INFO", f"top level of the volume: {', '.join(top) or '(empty)'}")
    report.result("PASS" if top and all(re.fullmatch(r"dt=\d{4}-\d{2}-\d{2}", n) for n in top) else "FAIL",
                  "only dt=... directories at the top level (nothing stray)")
    report.result("PASS" if len(files) == expected_files else "FAIL", f"{len(files)} files in the volume (expected {expected_files})")
    report.line("2. each landed file")
    landed_scans = []
    for relpath, listed_size in files:
        m = FILE_RE.match(relpath)
        if not m:
            report.result("FAIL", f"{relpath} does not match dt=/hh=/probe-01-<ULID>.ndjson")
            continue
        body = volume.download(relpath)
        if body is None:
            continue
        lines = body.decode("utf-8").splitlines(keepends=True)  # text, like the probe's sent log
        report.result("INFO", f"{relpath}: {len(lines)} lines, {len(body)} bytes"
                              + ("" if listed_size in (None, len(body)) else f" (listing says {listed_size})"))
        envelopes = [json.loads(l) for l in lines]
        problems = [p for e in envelopes for p in check_envelope(e)]
        scan_ids = {e["scan_id"] for e in envelopes}
        ok = (len(lines) == expected_lines and not problems and len(scan_ids) == 1
              and [e["sector_id"] for e in envelopes] == planets and all(LIVE_FLAGS in l for l in lines))
        report.result("PASS" if ok else "FAIL", f"  {len(lines)} lines (expected {expected_lines}), valid envelopes "
                      f"({len(problems)} problems), one scan_id, planets in dim_sector order, live flags on every line")
        (scan_id,) = scan_ids
        landed_scans.append(scan_id)
        report.result("PASS" if lines == sent_by_scan.get(scan_id) else "FAIL",
                      "  byte-for-byte what the probe published for that scan")
    report.result("PASS" if sorted(landed_scans) == sorted(sent_by_scan) else "FAIL",
                  f"every published scan landed exactly once ({len(sent_by_scan)} published, {len(landed_scans)} landed)")
    report.result("PASS" if len(set(landed_scans)) == expected_files else "FAIL",
                  f"{expected_files} distinct scan_ids, so {expected_files} separate files")


def verify_backfill(volume, expected, prefix, report, lines_per_file=60, sample=0):
    """Check a backfill upload (all of it, or one day) against the local files it came from. `expected` maps each
    file name to its local Path; `prefix` is the pinned (dt, hh). GET requests only.

      1. every expected name is under dt=<dt>/hh=<hh>/ and nothing else is (no missing file, no extra one), the
         listed sizes equal the local sizes, no expected name also landed under another prefix (it would load
         twice), and nothing stray sits at the top level;
      2. downloaded files (all, or `sample` of them spread evenly) are byte-for-byte the local files, and every
         line is a valid synthetic envelope, lines_per_file per file, one scan_id per file."""
    dt, hh = prefix
    base = f"dt={dt}/hh={hh}/"
    report.line(f"1. list the landing volume; expecting {len(expected)} files under {base}")
    files, top = volume.walk_files()
    if files is None:
        return
    listed = dict(files)
    report.result("INFO", f"top level of the volume: {', '.join(top) or '(empty)'}")
    report.result("PASS" if top and all(re.fullmatch(r"dt=\d{4}-\d{2}-\d{2}", n) for n in top) else "FAIL",
                  "only dt=... directories at the top level (nothing stray)")
    under = {r[len(base):]: size for r, size in listed.items() if r.startswith(base) and "/" not in r[len(base):]}
    missing, extra = sorted(set(expected) - set(under)), sorted(set(under) - set(expected))
    if missing or extra:
        report.result("FAIL", f"{len(under)} files under {base}: {len(missing)} expected files missing"
                              + (f" (first: {missing[0]})" if missing else "") + f", {len(extra)} unexpected"
                              + (f" (first: {extra[0]})" if extra else ""))
    else:
        report.result("PASS", f"{len(under)} files under {base}, exactly the {len(expected)} expected names")
    wrong_size = [n for n in expected if n in under and under[n] not in (None, expected[n].stat().st_size)]
    report.result("PASS" if not wrong_size else "FAIL",
                  "every listed size equals the local file's size" if not wrong_size
                  else f"{len(wrong_size)} listed sizes differ from the local files (first: {wrong_size[0]})")
    other = {r: size for r, size in listed.items() if not r.startswith(base)}
    twice = sorted(r for r in other if r.rsplit("/", 1)[-1] in expected)
    report.result("PASS" if not twice else "FAIL",
                  "no expected file also landed under another prefix" if not twice
                  else f"{len(twice)} expected files also landed under another prefix (would load twice), first: {twice[0]}")
    report.result("INFO", f"{len(other)} other files in the volume, not part of this upload"
                          + (": " + ", ".join(sorted(other)[:5]) + (" ..." if len(other) > 5 else "") if other else ""))
    report.line("2. file contents")
    names = sorted(n for n in expected if n in under)
    if sample and sample < len(names):
        stride = -(-len(names) // sample)
        names = names[::stride] + ([names[-1]] if names[-1] not in names[::stride] else [])
    bad_bytes = bad_lines = downloaded = 0
    for name in names:
        body = volume.download(base + name)
        if body is None:
            bad_bytes += 1
            continue
        downloaded += 1
        if body != expected[name].read_bytes():
            bad_bytes += 1
            report.result("FAIL", f"{name}: bytes differ from the local file")
            continue
        lines = body.decode("utf-8").splitlines()
        envelopes = [json.loads(line) for line in lines]
        problems = [p for e in envelopes for p in check_envelope(e)]
        ok = (len(lines) == lines_per_file and not problems and len({e["scan_id"] for e in envelopes}) == 1
              and all(e["is_synthetic"] is True and e["synthetic_ingest_ts"] for e in envelopes))
        if not ok:
            bad_lines += 1
            report.result("FAIL", f"{name}: {len(lines)} lines (expected {lines_per_file}), {len(problems)} envelope "
                                  "problems, or not one scan_id, or not every line synthetic")
    report.result("PASS" if not bad_bytes else "FAIL",
                  f"{downloaded} downloaded files are byte-for-byte the local files" if not bad_bytes
                  else f"{bad_bytes} of {len(names)} checked files are missing or differ from the local files")
    report.result("PASS" if not bad_lines else "FAIL",
                  f"each has {lines_per_file} valid synthetic envelopes and one scan_id" if not bad_lines
                  else f"{bad_lines} files failed the line checks")
    if len(names) < len(expected):
        report.result("INFO", f"{len(names)} of {len(expected)} files were downloaded (sample); the rest were checked "
                              "by name and size only")


def backfill_main(args, bargs):
    """verify_landing --backfill: read the pinned prefix and the local files, then verify_backfill()."""
    import upload
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    out = Path(args.out).resolve()
    state = Path(args.state_file) if args.state_file else out / "upload_state.json"
    if not state.exists():
        raise SystemExit(f"{state} does not exist: run `edge/backfill.py upload` first (or --pin-prefix)")
    dt, hh = upload._saved_prefix(state, manifest["content_hash"]["value"])
    expected = dict(bf.select_files(out, manifest, args.only_day))
    uploader = bridge.build_uploader(bargs)
    report = Reporter(sys.stdout, [uploader.host, urllib.parse.urlparse(uploader.host).netloc,
                                   uploader.tokens._client_id, uploader.tokens._client_secret])
    volume = Volume(uploader.tokens, uploader.host, uploader.volume_path, files_api.http_request, report)
    print(f"landing volume {uploader.volume_path}; scope {uploader.tokens.scope!r}; backfill run "
          f"{manifest['content_hash']['value'][:12]}, prefix dt={dt} hh={hh}", flush=True)
    verify_backfill(volume, expected, (dt, hh), report, manifest["lines_per_file"], args.sample)
    print("\n" + ("LANDING VERIFIED" if not report.failures else f"{len(report.failures)} CHECK(S) FAILED"))
    return 1 if report.failures else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sent-log", type=Path, help="the probe's --sent-log file (live mode)")
    p.add_argument("--backfill", action="store_true", help="check a backfill upload against the local files (needs --out)")
    p.add_argument("--out", help="backfill mode: the directory edge/backfill.py generate wrote")
    p.add_argument("--manifest", default=str(HERE.parent / "edge" / "backfill_manifest.json"), help="backfill mode (default: the committed manifest)")
    p.add_argument("--only-day", type=int, help="backfill mode: check only this day (-90..-1)")
    p.add_argument("--state-file", help="backfill mode: default OUT/upload_state.json")
    p.add_argument("--sample", type=int, default=0, help="backfill mode: download only N files spread evenly (0 = all)")
    p.add_argument("--files", type=int, default=3)
    p.add_argument("--lines", type=int, default=60)
    args, rest = p.parse_known_args(argv)
    bargs = bridge.parse_args(rest)  # --env-file, --volume-path, --oauth-scope
    if bargs.local_dir:
        raise SystemExit("verify_landing needs workspace mode: drop --local-dir")
    if args.backfill:
        return backfill_main(args, bargs)
    if not args.sent_log:
        raise SystemExit("--sent-log is required (or use --backfill)")
    uploader = bridge.build_uploader(bargs)
    report = Reporter(sys.stdout, [uploader.host, urllib.parse.urlparse(uploader.host).netloc,
                                   uploader.tokens._client_id, uploader.tokens._client_secret])
    sent = args.sent_log.read_text(encoding="utf-8").splitlines(keepends=True)
    volume = Volume(uploader.tokens, uploader.host, uploader.volume_path, files_api.http_request, report)
    print(f"landing volume {uploader.volume_path}; scope {uploader.tokens.scope!r}; {len(sent)} events were published", flush=True)
    verify(volume, sent, args.files, args.lines, report)
    print("\n" + ("LANDING VERIFIED" if not report.failures else f"{len(report.failures)} CHECK(S) FAILED"))
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
