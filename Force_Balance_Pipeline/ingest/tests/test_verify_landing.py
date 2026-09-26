"""ingest/verify_landing.py against a stub workspace volume: it must count files, lines and scans, compare bytes with
the probe's sent log, catch strays and shortfalls, make only GET requests, and never print a credential. Offline."""
import functools
import io
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _support as S  # noqa: E402
import bridge  # noqa: E402
import files_api  # noqa: E402
import verify_landing  # noqa: E402
from files_api import TokenProvider  # noqa: E402
from test_workspace_check import CLIENT_ID, CLIENT_SECRET, HOST, StubWorkspace, TOKEN, token_reply  # noqa: E402
from workspace_check import Reporter  # noqa: E402

ROOT = "/Volumes/force/raw/telemetry"


def scans(n=3):
    """n scans as the lines a probe publishes (strings with newline), one list per scan."""
    return [[m.decode() + "\n" for m in S.scan_messages(i, seed=i + 1)] for i in range(n)]


def landed(scan_lines, hour="04"):
    ulid = bridge.new_ulid(1_788_000_000_000 + len(scan_lines[0]))
    return f"dt=2026-09-26/hh={hour}/probe-01-{ulid}.ndjson", "".join(scan_lines).encode()


def volume_handler(files, extra_top=(), page_size=None):
    """A stub volume serving GET directory listings (optionally paged) and GET file downloads."""
    def entries_of(directory):
        prefix = directory.rstrip("/") + "/"
        seen, out = set(), []
        for rel in files:
            path = f"{ROOT}/{rel}"
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            head = rest.split("/")[0]
            if head in seen:
                continue
            seen.add(head)
            if "/" in rest:
                out.append({"name": head, "path": prefix + head, "is_directory": True})
            else:
                out.append({"name": head, "path": prefix + head, "is_directory": False, "file_size": len(files[rel])})
        if directory.rstrip("/") == ROOT:
            out += [{"name": n, "path": f"{ROOT}/{n}", "is_directory": True} for n in extra_top]
        return out

    def handler(method, path, query, body):
        assert method in ("GET", "POST"), f"unexpected {method} {path}"
        if path == "/oidc/v1/token":
            return token_reply()
        if path.startswith("/api/2.0/fs/directories"):
            directory = unquote(path[len("/api/2.0/fs/directories"):])
            entries = entries_of(directory)
            token = parse_qs(query).get("page_token", [None])[0]
            if page_size and directory == ROOT:
                start = int(token or 0)
                page = entries[start:start + page_size]
                more = start + page_size < len(entries)
                return 200, json.dumps({"contents": page, **({"next_page_token": str(start + page_size)} if more else {})}).encode()
            return 200, json.dumps({"contents": entries}).encode()
        if path.startswith("/api/2.0/fs/files"):
            rel = unquote(path[len("/api/2.0/fs/files"):])[len(ROOT) + 1:]
            return (200, files[rel]) if rel in files else (404, b'{"error_code":"NOT_FOUND"}')
        return 404, b"{}"
    return handler


def run(handler, sent_lines, expected_files=3, expected_lines=60):
    workspace = StubWorkspace(handler)
    http = functools.partial(files_api.http_request, opener=workspace)
    tokens = TokenProvider(HOST, CLIENT_ID, CLIENT_SECRET, http=http, clock=S.Clock(1_000_000.0).now)
    out = io.StringIO()
    report = Reporter(out, [HOST, tokens.host, CLIENT_ID, CLIENT_SECRET])
    volume = verify_landing.Volume(tokens, tokens.host, ROOT, http, report)
    verify_landing.verify(volume, sent_lines, expected_files, expected_lines, report)
    return report.failures, out.getvalue(), workspace


def good(n=3):
    all_scans = scans(n)
    files = dict(landed(s, hour=f"0{i + 1}") for i, s in enumerate(all_scans))
    return files, [line for s in all_scans for line in s]


class HappyPathTests(unittest.TestCase):
    def setUp(self):
        self.files, self.sent = good()
        self.failures, self.out, self.ws = run(volume_handler(self.files), self.sent)

    def test_three_files_of_sixty_lines_verify(self):
        self.assertEqual(self.failures, [], self.out)
        self.assertEqual(self.out.count("60 lines,"), 3)
        self.assertIn("3 files in the volume (expected 3)", self.out)
        self.assertIn("every published scan landed exactly once (3 published, 3 landed)", self.out)

    def test_each_path_and_line_count_is_printed(self):
        for rel in self.files:
            self.assertIn(f"{rel}: 60 lines, {len(self.files[rel])} bytes", self.out)

    def test_only_get_requests_are_made_and_each_carries_the_ua_and_token(self):
        methods = {r.get_method() for r in self.ws.requests}
        self.assertEqual(methods, {"GET", "POST"})  # POST is the token exchange only
        for r in self.ws.requests:
            self.assertEqual(r.get_header("User-agent"), files_api.UA)
            if r.get_method() == "GET":
                self.assertEqual(r.get_header("Authorization"), f"Bearer {TOKEN}")
                self.assertIsNone(r.data)

    def test_no_credential_or_host_in_the_output(self):
        for secret in (TOKEN, CLIENT_SECRET, CLIENT_ID, HOST):
            self.assertNotIn(secret, self.out)


class ProblemTests(unittest.TestCase):
    def test_a_missing_file_fails(self):
        files, sent = good()
        files.pop(next(iter(files)))
        failures, out, _ = run(volume_handler(files), sent)
        self.assertTrue(any("2 files in the volume (expected 3)" in f for f in failures))
        self.assertTrue(any("every published scan landed exactly once" in f for f in failures))

    def test_a_short_file_fails(self):
        files, sent = good()
        key = next(iter(files))
        files[key] = b"".join(files[key].splitlines(keepends=True)[:59])
        failures, out, _ = run(volume_handler(files), sent)
        self.assertTrue(any("59 lines (expected 60)" in f for f in failures))

    def test_altered_bytes_fail_the_byte_for_byte_check(self):
        files, sent = good()
        key = next(iter(files))
        files[key] = files[key].replace(b"2026-06-01", b"2026-06-02", 1)
        failures, out, _ = run(volume_handler(files), sent)
        self.assertTrue(any("byte-for-byte" in f for f in failures))

    def test_a_stray_top_level_entry_fails(self):
        files, sent = good()
        failures, out, _ = run(volume_handler(files, extra_top=("_connectivity_check",)), sent)
        self.assertTrue(any("only dt=... directories" in f for f in failures))

    def test_a_duplicate_landing_of_a_scan_fails(self):
        files, sent = good()
        first = next(iter(files))
        files["dt=2026-09-26/hh=09/probe-01-" + bridge.new_ulid(1_788_000_100_000) + ".ndjson"] = files[first]
        failures, out, _ = run(volume_handler(files), sent, expected_files=3)
        self.assertTrue(failures)

    def test_a_synthetic_line_fails_the_live_flag_check(self):
        files, sent = good()
        key = next(iter(files))
        files[key] = files[key].replace(b'"is_synthetic":false', b'"is_synthetic":true', 1)
        failures, out, _ = run(volume_handler(files), sent)
        self.assertTrue(failures)

    def test_an_unexpected_file_name_fails(self):
        files, sent = good()
        files["dt=2026-09-26/hh=04/notes.txt"] = b"x"
        failures, out, _ = run(volume_handler(files), sent, expected_files=4)
        self.assertTrue(any("does not match dt=/hh=/probe-01-<ULID>.ndjson" in f for f in failures))

    def test_a_denied_listing_fails_without_crashing(self):
        def handler(method, path, query, body):
            if path == "/oidc/v1/token":
                return token_reply()
            return 403, b'{"error_code":"PERMISSION_DENIED","message":"denied"}'
        files, sent = good()
        failures, out, ws = run(handler, sent)
        self.assertEqual(len(failures), 1)
        self.assertIn("PERMISSION_DENIED", out)
        self.assertEqual(len(ws.requests), 2)  # the token exchange and the one listing


class ListingTests(unittest.TestCase):
    def test_pagination_is_followed(self):
        files, sent = good()
        files = {k.replace("dt=2026-09-26", f"dt=2026-09-2{i + 4}"): v for i, (k, v) in enumerate(files.items())}  # three dt directories
        failures, out, ws = run(volume_handler(files, extra_top=(), page_size=1), sent)
        self.assertEqual(failures, [], out)
        listings = [r for r in ws.requests if urlparse(r.full_url).path.endswith(ROOT)]
        self.assertEqual(len(listings), 3, "three top-level dt directories, one per page")
        self.assertTrue(any("page_token=" in r.full_url for r in listings))

    def test_nested_directories_are_walked(self):
        files, sent = good()
        failures, out, ws = run(volume_handler(files), sent)
        dir_calls = [urlparse(r.full_url).path for r in ws.requests if "/fs/directories" in r.full_url]
        self.assertTrue(any("/hh=01" in p for p in dir_calls) and any("/dt=2026-09-26" in p for p in dir_calls))


class ScopeTests(unittest.TestCase):
    def test_the_script_only_reads(self):
        source = (Path(__file__).resolve().parents[1] / "verify_landing.py").read_text(encoding="utf-8")
        for verb in ('"PUT"', '"DELETE"', '"POST"', '"PATCH"'):
            self.assertNotIn(verb, source)

    def test_local_dir_mode_is_refused(self):
        with self.assertRaises(SystemExit):
            verify_landing.main(["--sent-log", "x", "--local-dir", "y"])


if __name__ == "__main__":
    unittest.main()
