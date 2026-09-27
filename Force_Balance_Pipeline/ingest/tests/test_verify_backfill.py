"""ingest/verify_landing.py --backfill against a stub workspace volume: the files under the pinned prefix must be
exactly the local files, byte for byte; it must catch missing, extra, altered, mis-sized and double-landed files and
non-synthetic lines, make only GET requests, support sampling, and never print a credential. Offline."""
import functools
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _support as S  # noqa: E402
import files_api  # noqa: E402
import verify_landing  # noqa: E402
from files_api import TokenProvider  # noqa: E402
from forcesim.envelope import new_ulid, to_ndjson_line, ts_ms  # noqa: E402
from forcesim.probe import SimProbe  # noqa: E402
from test_verify_landing import volume_handler  # noqa: E402
from test_workspace_check import CLIENT_ID, CLIENT_SECRET, HOST, StubWorkspace, TOKEN  # noqa: E402
from workspace_check import Reporter  # noqa: E402

BASE = "dt=2026-09-26/hh=17/"
PREFIX = ("2026-09-26", "17")
T0 = datetime(2026, 7, 17, 14, 15, tzinfo=timezone.utc)


def scan_bytes(i, synthetic=True):
    """One scan as the bytes of a backfill file: 60 valid envelopes, synthetic (or live, for the negative test)."""
    when = T0 + timedelta(minutes=15 * i)
    probe = SimProbe(S.SECTORS, seed=i + 1)
    envelopes = probe.sweep(i, when, is_synthetic=synthetic,
                            synthetic_ingest_ts=(when + timedelta(seconds=5)) if synthetic else None)
    return "".join(to_ndjson_line(e) for e in envelopes).encode("utf-8")


class Fixture(unittest.TestCase):
    """Three local files and a stub volume that holds the same three under the pinned prefix."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="verify-backfill-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.local, self.names = {}, []
        for i in range(3):
            name = f"probe-01-{new_ulid(ts_ms(T0 + timedelta(minutes=15 * i)), __import__('random').Random(i))}.ndjson"
            path = self.tmp / name
            path.write_bytes(scan_bytes(i))
            self.local[name] = path
            self.names.append(name)
        self.remote = {BASE + n: p.read_bytes() for n, p in self.local.items()}

    def check(self, remote=None, extra_top=(), sample=0, expected=None, wrap=None):
        handler = volume_handler(self.remote if remote is None else remote, extra_top=extra_top)
        if wrap:
            handler = wrap(handler)
        self.sleeps = []
        workspace = StubWorkspace(handler)
        http = functools.partial(files_api.http_request, opener=workspace)
        tokens = TokenProvider(HOST, CLIENT_ID, CLIENT_SECRET, http=http, clock=S.Clock(1_000_000.0).now)
        out = io.StringIO()
        report = Reporter(out, [HOST, tokens.host, CLIENT_ID, CLIENT_SECRET])
        volume = verify_landing.Volume(tokens, tokens.host, "/Volumes/force/raw/telemetry", http, report, sleep=self.sleeps.append)
        verify_landing.verify_backfill(volume, self.local if expected is None else expected, PREFIX, report,
                                       lines_per_file=60, sample=sample)
        self.workspace, self.volume = workspace, volume
        return report.failures, out.getvalue()


class HappyPathTests(Fixture):
    def test_the_files_under_the_pinned_prefix_are_exactly_the_local_ones(self):
        failures, out = self.check()
        self.assertEqual(failures, [], out)
        self.assertIn("3 files under dt=2026-09-26/hh=17/, exactly the 3 expected names", out)
        self.assertIn("every listed size equals the local file's size", out)
        self.assertIn("3 downloaded files are byte-for-byte the local files", out)
        self.assertIn("each has 60 valid synthetic envelopes and one scan_id", out)
        self.assertIn("no expected file also landed under another prefix", out)

    def test_only_get_requests_carry_the_token_and_the_user_agent(self):
        self.check()
        self.assertEqual({r.get_method() for r in self.workspace.requests}, {"GET", "POST"})   # POST: the token exchange
        for r in self.workspace.requests:
            self.assertEqual(r.get_header("User-agent"), files_api.UA)
            if r.get_method() == "GET":
                self.assertEqual(r.get_header("Authorization"), f"Bearer {TOKEN}")

    def test_no_credential_or_host_in_the_output(self):
        _, out = self.check()
        for secret in (TOKEN, CLIENT_SECRET, CLIENT_ID, HOST):
            self.assertNotIn(secret, out)

    def test_the_live_files_already_in_the_volume_are_reported_not_failed(self):
        live = {"dt=2026-09-26/hh=14/probe-01-01M3F1SETJ7HMB5JDECTCWQY9P.ndjson": scan_bytes(9, synthetic=False)}
        failures, out = self.check(remote={**self.remote, **live})
        self.assertEqual(failures, [], out)
        self.assertIn("1 other files in the volume, not part of this upload: dt=2026-09-26/hh=14/", out)

    def test_a_partial_day_is_checked_against_only_its_own_names(self):
        one = {self.names[0]: self.local[self.names[0]]}
        failures, out = self.check(remote={BASE + self.names[0]: self.remote[BASE + self.names[0]]}, expected=one)
        self.assertEqual(failures, [], out)
        self.assertIn("1 files under dt=2026-09-26/hh=17/, exactly the 1 expected names", out)


class ProblemTests(Fixture):
    def test_a_missing_file_fails(self):
        del self.remote[BASE + self.names[1]]
        failures, out = self.check()
        self.assertTrue(any("1 expected files missing" in f for f in failures), failures)

    def test_an_unexpected_file_under_the_prefix_fails(self):
        self.remote[BASE + "probe-01-0ZZZZZZZZZZZZZZZZZZZZZZZZZ.ndjson"] = b"{}"
        failures, _ = self.check()
        self.assertTrue(any("1 unexpected" in f for f in failures), failures)

    def test_altered_bytes_of_the_same_length_fail_the_byte_check(self):
        key = BASE + self.names[2]
        self.remote[key] = self.remote[key].replace(b"tatooine", b"tatooinf", 1)
        failures, out = self.check()
        self.assertTrue(any("bytes differ from the local file" in f for f in failures), failures)
        self.assertTrue(any("missing or differ" in f for f in failures), failures)

    def test_a_different_length_fails_the_size_check_too(self):
        key = BASE + self.names[0]
        self.remote[key] = self.remote[key][:-100]
        failures, _ = self.check()
        self.assertTrue(any("listed sizes differ" in f for f in failures), failures)

    def test_a_file_that_also_landed_under_another_prefix_fails(self):
        self.remote["dt=2026-09-26/hh=18/" + self.names[0]] = self.remote[BASE + self.names[0]]
        failures, _ = self.check()
        self.assertTrue(any("also landed under another prefix (would load twice)" in f for f in failures), failures)

    def test_everything_under_the_wrong_prefix_fails(self):
        moved = {"dt=2026-09-26/hh=18/" + n: b for n, b in ((n, self.remote[BASE + n]) for n in self.names)}
        failures, _ = self.check(remote=moved)
        self.assertTrue(any("3 expected files missing" in f for f in failures), failures)
        self.assertTrue(any("also landed under another prefix" in f for f in failures), failures)

    def test_a_stray_top_level_entry_fails(self):
        failures, _ = self.check(extra_top=("notes",))
        self.assertTrue(any("only dt=... directories" in f for f in failures), failures)

    def test_a_live_line_in_a_backfill_file_fails(self):
        name = self.names[0]
        live = scan_bytes(0, synthetic=False)
        self.local[name].write_bytes(live)
        self.remote[BASE + name] = live                    # local and remote agree, but the lines are not synthetic
        failures, _ = self.check()
        self.assertTrue(any("not every line synthetic" in f for f in failures), failures)

    def test_a_short_file_fails_the_line_check(self):
        name = self.names[0]
        short = self.local[name].read_bytes().splitlines(keepends=True)[:59]
        self.local[name].write_bytes(b"".join(short))
        self.remote[BASE + name] = b"".join(short)
        failures, _ = self.check()
        self.assertTrue(any("59 lines (expected 60)" in f for f in failures), failures)

    def test_a_denied_listing_fails_without_crashing(self):
        def handler(method, path, query, body):
            if path == "/oidc/v1/token":
                from test_workspace_check import token_reply
                return token_reply()
            return 403, b'{"error_code":"PERMISSION_DENIED","message":"denied"}'
        workspace = StubWorkspace(handler)
        http = functools.partial(files_api.http_request, opener=workspace)
        tokens = TokenProvider(HOST, CLIENT_ID, CLIENT_SECRET, http=http, clock=S.Clock(1_000_000.0).now)
        out = io.StringIO()
        report = Reporter(out, [HOST, CLIENT_ID, CLIENT_SECRET])
        verify_landing.verify_backfill(verify_landing.Volume(tokens, tokens.host, "/Volumes/force/raw/telemetry", http, report),
                                       self.local, PREFIX, report)
        self.assertTrue(report.failures)


class SampleTests(Fixture):
    def downloads(self):
        return [r for r in self.workspace.requests if "/api/2.0/fs/files" in r.full_url]

    def test_every_file_is_downloaded_by_default(self):
        self.check()
        self.assertEqual(len(self.downloads()), 3)

    def test_a_sample_downloads_fewer_but_still_checks_every_name_and_size(self):
        failures, out = self.check(sample=1)
        self.assertEqual(failures, [], out)
        self.assertLess(len(self.downloads()), 3)
        self.assertIn("were downloaded (sample); the rest were checked by name and size only", out)
        del self.remote[BASE + self.names[1]]
        failures, _ = self.check(sample=1)
        self.assertTrue(failures, "a missing file is caught without downloading it")

    def test_a_sample_larger_than_the_files_downloads_all(self):
        self.check(sample=50)
        self.assertEqual(len(self.downloads()), 3)


def flaky(times, outcome):
    """Wrap a volume handler so the first `times` downloads of every file return `outcome`: an exception instance is
    raised (a network error), a tuple is returned as the response (an HTTP error)."""
    def wrap(handler):
        seen = {}

        def wrapped(method, path, query, body):
            if path.startswith("/api/2.0/fs/files"):
                seen[path] = seen.get(path, 0) + 1
                if seen[path] <= times:
                    return outcome
            return handler(method, path, query, body)
        return wrapped
    return wrap


class RetryTests(Fixture):
    """A check that reads thousands of files must survive a dropped connection, but never hide a real failure."""

    def test_a_dropped_connection_is_retried_and_the_check_passes(self):
        failures, out = self.check(wrap=flaky(1, files_api.TransportError("RemoteDisconnected")))
        self.assertEqual(failures, [], out)
        self.assertEqual(self.volume.retries, 3)                   # one retry for each of the three downloads
        self.assertEqual(self.sleeps, [1, 1, 1])
        self.assertIn("3 downloaded files are byte-for-byte the local files", out)

    def test_a_server_error_is_retried_too(self):
        failures, out = self.check(wrap=flaky(2, (503, b'{"error_code":"TEMPORARILY_UNAVAILABLE"}')))
        self.assertEqual(failures, [], out)
        self.assertEqual(self.sleeps, [1, 2] * 3)                  # two retries per file, waiting a little longer each time

    def test_a_persistent_network_error_is_one_named_failure_after_three_attempts(self):
        failures, out = self.check(wrap=flaky(99, files_api.TransportError("RemoteDisconnected")))
        network = [f for f in failures if "network error after 3 attempts" in f]
        self.assertEqual(len(network), 3, failures)                # one per file, not two
        self.assertFalse(any("-> None" in f for f in failures), failures)
        self.assertTrue(any("missing or differ" in f for f in failures), failures)

    def test_a_denied_read_is_not_retried(self):
        failures, out = self.check(wrap=flaky(99, (403, b'{"error_code":"PERMISSION_DENIED"}')))
        self.assertEqual(self.sleeps, [])
        self.assertTrue(any("-> 403" in f for f in failures), failures)

    def test_retries_do_not_change_what_is_read(self):
        failures, _ = self.check(wrap=flaky(1, files_api.TransportError("x")))
        self.assertEqual({r.get_method() for r in self.workspace.requests}, {"GET", "POST"})


class CommandLineTests(unittest.TestCase):
    def test_backfill_mode_needs_the_state_file(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        manifest = tmp / "m.json"
        manifest.write_text(json.dumps({"content_hash": {"value": "abc"}, "window": {}}), encoding="utf-8")
        args = verify_landing.argparse.Namespace(manifest=str(manifest), out=str(tmp), state_file=None, only_day=None, sample=0)
        with self.assertRaisesRegex(SystemExit, "does not exist: run `edge/backfill.py upload` first"):
            verify_landing.backfill_main(args, None)

    def test_live_mode_still_needs_a_sent_log(self):
        with self.assertRaisesRegex(SystemExit, "--sent-log is required"):
            verify_landing.main([])

    def test_local_dir_mode_is_still_refused_in_backfill_mode(self):
        with self.assertRaisesRegex(SystemExit, "workspace mode"):
            verify_landing.main(["--backfill", "--out", "x", "--local-dir", "y"])


if __name__ == "__main__":
    unittest.main()
