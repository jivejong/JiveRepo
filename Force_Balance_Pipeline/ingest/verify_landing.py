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
"""
import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "bridge"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "edge"))

import bridge  # noqa: E402
import files_api  # noqa: E402
from files_api import TransportError  # noqa: E402
from forcesim.envelope import check_envelope  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402
from workspace_check import Reporter, error_summary  # noqa: E402

FILE_RE = re.compile(r"^dt=(\d{4}-\d{2}-\d{2})/hh=(\d{2})/probe-01-([0-9A-HJKMNP-TV-Z]{26})\.ndjson$")
LIVE_FLAGS = '"is_synthetic":false,"synthetic_ingest_ts":null'


class Volume:
    """GET-only view of one volume through the Files API."""

    def __init__(self, tokens, host, volume_path, http, report):
        self.tokens, self.host, self.http, self.report = tokens, host, http, report
        self.root = "/" + volume_path.strip("/")

    def _get(self, path):
        try:
            return self.http("GET", self.host + path, headers={"Authorization": f"Bearer {self.tokens.token()}"})
        except TransportError as e:
            self.report.result("FAIL", f"GET {path.split('?')[0]} -> network error ({e})")
            return None, b""

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
            self.report.result("FAIL", f"GET /api/2.0/fs/files/{relpath} -> {status}{error_summary(body) if status else ''}")
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


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sent-log", type=Path, required=True, help="the probe's --sent-log file")
    p.add_argument("--files", type=int, default=3)
    p.add_argument("--lines", type=int, default=60)
    args, rest = p.parse_known_args(argv)
    bargs = bridge.parse_args(rest)  # --env-file, --volume-path, --oauth-scope
    if bargs.local_dir:
        raise SystemExit("verify_landing needs workspace mode: drop --local-dir")
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
