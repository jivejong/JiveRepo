"""`edge/backfill.py upload` (doc 07 step 5): it verifies the files against the manifest first, pins the dt/hh prefix
in a state file outside the repository, uploads through the tested core (deterministic names, 409 means already
landed), reports timing and a projection, and never prints a credential. Offline, with an in-memory volume, on the
nine planets the texture names.
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ingest" / "bridge"))

import backfill as cli  # noqa: E402
from forcesim import backfill as bf  # noqa: E402
from forcesim.constants import SCANS_PER_DAY  # noqa: E402
from forcesim.envelope import decode_ulid_time, ts_ms  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402
from forcesim.window import SCANS, make_window  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TEXTURE_PATH = ROOT / "edge" / "backfill_texture.json"
LIVE = datetime(2026, 9, 26, 14, 27, 0, tzinfo=timezone.utc)
NAMED = ["tatooine", "dantooine", "kamino", "coruscant", "mon_cala", "corellia", "kashyyyk", "endor", "bespin"]
SUBSET = [s for s in load_sectors() if s.sector_id in NAMED]
WINDOW = make_window(LIVE)
TEXTURE = bf.load_texture(TEXTURE_PATH, SUBSET)
DAY = -71
NOW = datetime(2026, 9, 26, 17, 40, 0, tzinfo=timezone.utc).timestamp()     # the pinned prefix: dt=2026-09-26 hh=17
LATER = datetime(2026, 9, 26, 21, 5, 0, tzinfo=timezone.utc).timestamp()
HOST, CLIENT_ID, SECRET = "https://adb-1234567890.7.stub.example", "cid-4f9a", "s3cr3t-value-xyz"


class Volume:
    """An in-memory landing volume with the Files API contract: never overwrites (409). Thread-safe."""

    def __init__(self, fail=(), transport_error=None):
        self.files, self.calls, self.fail = {}, [], tuple(fail)
        self.transport_error, self.lock = transport_error, threading.Lock()
        self.host = HOST
        self.tokens = SimpleNamespace(_client_id=CLIENT_ID, _client_secret=SECRET)

    def put(self, relpath, data):
        with self.lock:
            self.calls.append(relpath)
            if self.transport_error is not None:
                error, self.transport_error = self.transport_error, None
                raise error
            if any(f in relpath for f in self.fail):
                return 500
            if relpath in self.files:
                return 409
            self.files[relpath] = data
            return 204


class Ticker:
    """A clock that advances 0.25 s per call, so elapsed time is positive and repeatable."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        self.t += 0.25
        return self.t


SHARED = {}


def setUpModule():
    """One generated 90-day run (nine planets, about half a minute) shared by every test in this module. Tests that
    damage it do so in place and put it back."""
    tmp = Path(tempfile.mkdtemp(prefix="backfill-upload-"))
    out = tmp / "out"
    manifest = bf.generate(SUBSET, WINDOW, TEXTURE, TEXTURE_PATH, 42, out)
    manifest_path = tmp / "manifest.json"
    manifest_path.write_text(bf.manifest_text(manifest), encoding="utf-8")
    SHARED.update(tmp=tmp, out=out, manifest=manifest, manifest_path=manifest_path,
                  run_id=manifest["content_hash"]["value"],
                  day_names=[n for n, _ in bf.select_files(out, manifest, DAY)])


def tearDownModule():
    shutil.rmtree(SHARED["tmp"], ignore_errors=True)


class UploadTestCase(unittest.TestCase):
    def setUp(self):
        for key, value in SHARED.items():
            setattr(self, key, value)
        self.state = self.tmp / f"state-{self._testMethodName}.json"
        self.lines = []
        self.errors = io.StringIO()

    def upload(self, *extra, volume=None, out=None, now=NOW, **hooks):
        """(exit code, volume, everything printed: the log lines and stderr, where refusals go)."""
        volume = volume if volume is not None else Volume()
        argv = ["upload", "--out", str(out or self.out), "--manifest", str(self.manifest_path),
                "--state-file", str(self.state), *extra]
        with contextlib.redirect_stderr(self.errors):
            code = cli.main(argv, sectors=SUBSET, uploader=volume, sleep=lambda s: None, clock=Ticker(),
                            now=lambda: now, log=self.lines.append, **hooks)
        return code, volume, chr(10).join(self.lines) + chr(10) + self.errors.getvalue()


class OneDayTests(UploadTestCase):
    def test_a_day_uploads_96_files_under_the_pinned_prefix_with_the_local_bytes(self):
        code, volume, text = self.upload("--only-day", str(DAY))
        self.assertEqual(code, 0, text)
        self.assertEqual(len(volume.files), SCANS_PER_DAY)
        for relpath, data in volume.files.items():
            match = re.fullmatch(r"dt=2026-09-26/hh=17/(probe-01-[0-9A-HJKMNP-TV-Z]{26}[.]ndjson)", relpath)
            self.assertIsNotNone(match, relpath)
            self.assertEqual(data, (self.out / "scans" / match.group(1)).read_bytes())
        self.assertEqual(sorted(r.rsplit("/", 1)[1] for r in volume.files), sorted(self.day_names))

    def test_the_day_is_the_96_scans_the_texture_and_generate_call_that_day(self):
        first = SCANS + DAY * SCANS_PER_DAY
        indexes = sorted(bf.scan_index_of(n, WINDOW) for n in self.day_names)
        self.assertEqual(indexes, list(range(first, first + SCANS_PER_DAY)))
        self.assertEqual(WINDOW.scan_times()[first].strftime("%Y-%m-%d"), "2026-07-17")   # day -71 starts 07-17 14:15Z

    def test_the_state_file_pins_the_prefix_to_the_run(self):
        self.upload("--only-day", str(DAY))
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual((state["run_id"], state["dt"], state["hh"]), (self.run_id, "2026-09-26", "17"))
        self.assertNotIn(ROOT, self.state.parents)

    def test_the_report_has_the_prefix_counts_timing_and_the_projection(self):
        code, volume, text = self.upload("--only-day", str(DAY))
        self.assertIn("prefix: dt=2026-09-26 hh=17", text)
        self.assertIn(f"files: {SCANS_PER_DAY} selected, {SCANS_PER_DAY} uploaded, 0 already landed (409), 0 failed, 0 retries", text)
        self.assertIn("4 workers", text)                                                    # the default
        elapsed = float(re.search(r"elapsed: ([0-9.]+) s", text).group(1))
        rate = float(re.search(r"rate: ([0-9.]+) files/s", text).group(1))
        self.assertAlmostEqual(rate, SCANS_PER_DAY / elapsed, delta=0.01)
        minutes = float(re.search(r"projection for 8,640 files at this rate: ([0-9.]+) min", text).group(1))
        self.assertAlmostEqual(minutes, SCANS / rate / 60, delta=0.05)
        self.assertIn(str(self.state), text)                                                # where the state file is

    def test_the_default_is_four_workers_and_the_option_is_honoured(self):
        self.assertEqual(cli.build_parser().parse_args(["upload", "--out", "x"]).workers, 4)
        code, volume, text = self.upload("--only-day", str(DAY), "--workers", "2")
        self.assertIn("2 workers", text)
        self.assertEqual(code, 0)

    def test_workers_must_be_positive(self):
        code, volume, text = self.upload("--only-day", str(DAY), "--workers", "0")
        self.assertEqual(code, 2)
        self.assertEqual(volume.calls, [])


class RefusalTests(UploadTestCase):
    """The shared directory is damaged in place and put back (copying 8,640 files is slow), one test at a time."""

    def assert_refused_and_nothing_happened(self, problem):
        self.lines.clear()
        code, volume, text = self.upload("--only-day", str(DAY))
        self.assertEqual(code, 2, problem)
        self.assertIn("do not verify against the manifest", text)
        self.assertEqual(volume.calls, [], "nothing may be uploaded")
        self.assertFalse(self.state.exists(), "and no prefix may be assigned")

    def test_a_changed_byte_is_refused(self):
        victim = sorted((self.out / "scans").glob("*.ndjson"))[5]
        original = victim.read_bytes()
        victim.write_bytes(original.replace(b"tatooine", b"tatooinf", 1))
        try:
            self.assert_refused_and_nothing_happened("changed byte")
        finally:
            victim.write_bytes(original)

    def test_a_missing_file_is_refused(self):
        victim = sorted((self.out / "scans").glob("*.ndjson"))[5]
        parked = self.tmp / "parked.ndjson"
        os.replace(victim, parked)
        try:
            self.assert_refused_and_nothing_happened("missing file")
        finally:
            os.replace(parked, victim)

    def test_an_extra_file_is_refused(self):
        extra = self.out / "scans" / "probe-01-7ZZZZZZZZZZZZZZZZZZZZZZZZZ.ndjson"
        extra.write_bytes(b"{}")
        try:
            self.assert_refused_and_nothing_happened("extra file")
        finally:
            extra.unlink()

    def test_it_refuses_when_the_manifest_was_written_for_other_inputs(self):
        old = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        old["thresholds"]["emergency"] = 4.5
        stale = self.tmp / "stale-manifest.json"
        stale.write_text(json.dumps(old), encoding="utf-8")
        volume = Volume()
        with contextlib.redirect_stderr(self.errors):
            code = cli.main(["upload", "--out", str(self.out), "--manifest", str(stale), "--state-file", str(self.state),
                             "--only-day", str(DAY)], sectors=SUBSET, uploader=volume, log=self.lines.append)
        self.assertEqual(code, 2)
        self.assertIn("the doc 03 thresholds changed", self.errors.getvalue())
        self.assertEqual(volume.calls, [])

    def test_the_state_file_may_not_be_inside_the_repository(self):
        inside = ROOT / "edge" / "upload_state_should_not_exist.json"
        code, volume, text = self.upload("--only-day", str(DAY), "--state-file", str(inside))
        self.assertEqual(code, 2)
        self.assertEqual(volume.calls, [])
        self.assertFalse(inside.exists())

    def test_a_state_file_belonging_to_another_backfill_is_refused(self):
        sys.path.insert(0, str(ROOT / "ingest" / "bridge"))
        import upload as up
        up.pin_prefix(self.state, "2026-09-26", "09", "some-other-run")
        code, volume, text = self.upload("--only-day", str(DAY))
        self.assertEqual(code, 2)
        self.assertEqual(volume.calls, [])
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["run_id"], "some-other-run")

    def test_a_day_that_is_out_of_range_is_refused_before_a_prefix_is_assigned(self):
        code, volume, text = self.upload("--only-day", "-95")
        self.assertEqual(code, 2)
        self.assertEqual(volume.calls, [])
        self.assertFalse(self.state.exists())

    def test_a_directory_that_does_not_hold_the_day_is_refused(self):
        sparse = self.tmp / "sparse"
        (sparse / "scans").mkdir(parents=True, exist_ok=True)
        for name in self.day_names[:3]:
            (sparse / "scans" / name).write_bytes(b"{}")
        with self.assertRaisesRegex(bf.BackfillError, "day -71 should have 96 files.*found 3"):
            bf.select_files(sparse, self.manifest, DAY)


class RestartTests(UploadTestCase):
    def test_the_full_upload_after_a_day_skips_that_day_and_keeps_the_prefix(self):
        volume = Volume()
        code, _, _ = self.upload("--only-day", str(DAY), volume=volume)
        self.assertEqual(code, 0)
        day_files = dict(volume.files)
        self.lines.clear()
        code, _, text = self.upload(volume=volume, now=LATER)              # four hours later, a different hour
        self.assertEqual(code, 0, text)
        self.assertIn("prefix: dt=2026-09-26 hh=17", text, "the pinned prefix, not the hour of the second run")
        self.assertIn(f"{SCANS} selected, {SCANS - SCANS_PER_DAY} uploaded, {SCANS_PER_DAY} already landed (409), 0 failed", text)
        self.assertEqual(len(volume.files), SCANS, "every file exactly once, nothing under a second prefix")
        self.assertTrue(all(r.startswith("dt=2026-09-26/hh=17/") for r in volume.files))
        for relpath, data in day_files.items():
            self.assertEqual(volume.files[relpath], data)

    def test_a_rerun_of_everything_uploads_nothing(self):
        volume = Volume()
        self.upload("--only-day", str(DAY), volume=volume)
        self.lines.clear()
        code, _, text = self.upload("--only-day", str(DAY), volume=volume, now=LATER)
        self.assertEqual(code, 0)
        self.assertIn(f"{SCANS_PER_DAY} selected, 0 uploaded, {SCANS_PER_DAY} already landed (409), 0 failed", text)
        self.assertNotIn("rate:", text)                                    # nothing uploaded: no rate to project
        self.assertEqual(len(volume.files), SCANS_PER_DAY)

    def test_pin_prefix_recreates_a_lost_state_file_so_a_new_prefix_is_never_assigned(self):
        volume = Volume()
        self.upload("--only-day", str(DAY), volume=volume)
        self.state.unlink()                                                # the state file is lost
        code, _, text = self.upload("--only-day", str(DAY), "--pin-prefix", "2026-09-26", "17", volume=volume, now=LATER)
        self.assertEqual(code, 0, text)
        self.assertIn(f"0 uploaded, {SCANS_PER_DAY} already landed (409)", text)
        self.assertEqual(len(volume.files), SCANS_PER_DAY)

    def test_without_the_pin_a_lost_state_file_WOULD_duplicate_the_day(self):
        """What --pin-prefix protects against: the prefix is ingest time, so a fresh state file names a new hour."""
        volume = Volume()
        self.upload("--only-day", str(DAY), volume=volume)
        self.state.unlink()
        self.upload("--only-day", str(DAY), volume=volume, now=LATER)
        self.assertEqual(len(volume.files), 2 * SCANS_PER_DAY)

    def test_pin_prefix_refuses_a_prefix_that_contradicts_the_state_file(self):
        self.upload("--only-day", str(DAY))
        code, volume, text = self.upload("--only-day", str(DAY), "--pin-prefix", "2026-09-26", "18")
        self.assertEqual(code, 2)
        self.assertEqual(volume.calls, [])


class FailureTests(UploadTestCase):
    def test_a_permanent_failure_is_named_counted_and_exits_1_while_the_rest_land(self):
        victim = self.day_names[10]
        code, volume, text = self.upload("--only-day", str(DAY), volume=Volume(fail=[victim]))
        self.assertEqual(code, 1)
        self.assertEqual(len(volume.files), SCANS_PER_DAY - 1)
        self.assertIn("1 failed", text)
        self.assertIn(f"FAILED: dt=2026-09-26/hh=17/{victim}", text)

    def test_a_lost_acknowledgement_is_a_409_not_a_duplicate(self):
        from files_api import TransportError
        volume = Volume(transport_error=TransportError("connection reset"))
        code, volume, text = self.upload("--only-day", str(DAY), volume=volume)
        self.assertEqual(code, 0, text)
        self.assertEqual(len(volume.files), SCANS_PER_DAY)                # the retry of the failed put landed once

    def test_no_credential_or_host_is_ever_printed(self):
        from files_api import TransportError
        volume = Volume(transport_error=TransportError(f"could not reach {HOST}/api/2.0/fs/files as {CLIENT_ID}:{SECRET}"))
        code, volume, text = self.upload("--only-day", str(DAY), volume=volume)
        self.assertIn("<redacted>", text)
        for secret in (HOST, "adb-1234567890", CLIENT_ID, SECRET):
            self.assertNotIn(secret, text)


class RehearsalTests(UploadTestCase):
    def test_local_dir_mode_writes_the_same_layout_without_credentials(self):
        dest = self.tmp / "landing"
        argv = ["upload", "--out", str(self.out), "--manifest", str(self.manifest_path), "--state-file", str(self.state),
                "--only-day", str(DAY), "--local-dir", str(dest), "--env-file", str(self.tmp / "no-such-env")]
        lines = []
        code = cli.main(argv, sectors=SUBSET, sleep=lambda s: None, clock=Ticker(), now=lambda: NOW, log=lines.append)
        self.assertEqual(code, 0, lines)
        landed = sorted(p for p in dest.rglob("*.ndjson"))
        self.assertEqual(len(landed), SCANS_PER_DAY)
        self.assertTrue(all(p.parent == dest / "dt=2026-09-26" / "hh=17" for p in landed))
        for p in landed:
            self.assertEqual(p.read_bytes(), (self.out / "scans" / p.name).read_bytes())
        lines.clear()
        code = cli.main(argv, sectors=SUBSET, sleep=lambda s: None, clock=Ticker(), now=lambda: LATER, log=lines.append)
        self.assertEqual(code, 0)
        self.assertIn(f"0 uploaded, {SCANS_PER_DAY} already landed (409)", chr(10).join(lines))
        self.assertEqual(len(list(dest.rglob("*.ndjson"))), SCANS_PER_DAY)


class SelectionTests(UploadTestCase):
    def test_file_names_carry_their_scan_index(self):
        names = [p.name for p in sorted((self.out / "scans").glob("*.ndjson"))]
        self.assertEqual([bf.scan_index_of(n, WINDOW) for n in (names[0], names[100], names[-1])], [0, 100, SCANS - 1])
        first = decode_ulid_time(bf.ulid_of(names[0]))
        self.assertEqual(first, ts_ms(WINDOW.start))

    def test_foreign_names_are_refused(self):
        for bad in ("probe-02-01KW79ASN04C4M85HJF00S8G7C.ndjson", "probe-01-SHORT.ndjson", "notes.txt",
                    "probe-01-01KW79ASN04C4M85HJF00S8G7C.json"):
            with self.subTest(bad):
                with self.assertRaises(bf.BackfillError):
                    bf.scan_index_of(bad, WINDOW)

    def test_a_ulid_that_is_not_a_scan_time_is_refused(self):
        from forcesim.envelope import encode_ulid
        off_grid = encode_ulid(ts_ms(WINDOW.start) + 1234, 1)
        before = encode_ulid(ts_ms(WINDOW.start) - 900_000, 1)
        for ulid in (off_grid, before):
            with self.assertRaises(bf.BackfillError):
                bf.scan_index_of(f"probe-01-{ulid}.ndjson", WINDOW)

    def test_only_day_must_be_in_range(self):
        for bad in (0, -91, 5):
            with self.assertRaises(bf.BackfillError):
                bf.select_files(self.out, self.manifest, bad)
        self.assertEqual(len(bf.select_files(self.out, self.manifest)), SCANS)


if __name__ == "__main__":
    unittest.main()
