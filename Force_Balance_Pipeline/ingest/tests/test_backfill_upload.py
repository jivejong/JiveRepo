"""Backfill uploads (deterministic file names): any 409 means "already landed", never re-key; a restarted
upload produces zero duplicate files; the dt/hh prefix is fixed across restarts; and the live re-key path is
unchanged. Offline."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402
import upload  # noqa: E402
from files_api import TransportError  # noqa: E402

NO_SLEEP = dict(sleep=lambda s: None, log=lambda *a: None)


class Crash(BaseException):
    """A process dying mid-run (BaseException, so no `except Exception` can swallow it)."""


class CrashAfter:
    """Wraps an uploader and dies on the (n+1)th put, before writing it."""

    def __init__(self, inner, n):
        self.inner, self.n, self.puts = inner, n, 0

    def put(self, relpath, data):
        if self.puts >= self.n:
            raise Crash()
        self.puts += 1
        return self.inner.put(relpath, data)


class LostAck:
    """Writes the file, then loses the response: a network error after the server already has the data."""

    def __init__(self, inner, lose_on):
        self.inner, self.lose_on, self.calls = inner, set(lose_on), 0

    def put(self, relpath, data):
        self.calls += 1
        status = self.inner.put(relpath, data)
        if self.calls in self.lose_on:
            raise TransportError("timeout")
        return status


def items(prefix=("2026-09-26", "03"), n=30, source="probe-01"):
    """n deterministic (relpath, bytes) pairs, as a backfill would produce them."""
    out = []
    for i in range(n):
        ulid = bridge.new_ulid(1_780_000_000_000 + i * 900_000, randomness=i)  # derived from the data, not random
        out.append((upload.backfill_relpath(*prefix, source, ulid), f"scan {i}\n".encode() * 60))
    return out


def landed(root):
    return sorted(p for p in Path(root).rglob("*") if p.is_file())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DeterministicConflictTests(unittest.TestCase):
    def test_409_on_the_first_attempt_means_already_landed_and_never_rekeys(self):
        def rekey():
            raise AssertionError("a deterministic upload must never re-key")
        up = S.ScriptedUploader([409])
        r = upload.upload_file(up, "dt=x/hh=1/f.ndjson", b"d", deterministic=True, rekey=rekey, **NO_SLEEP)
        self.assertEqual((r.outcome, r.rekeys, len(up.calls)), (upload.ALREADY_LANDED, 0, 1))

    def test_409_after_any_kind_of_earlier_attempt_is_still_already_landed(self):
        for first in (TransportError("x"), 503, 429):
            up = S.ScriptedUploader([first, 409])
            r = upload.upload_file(up, "p", b"d", deterministic=True, **NO_SLEEP)
            with self.subTest(first=repr(first)):
                self.assertEqual((r.outcome, r.rekeys), (upload.ALREADY_LANDED, 0))
                self.assertEqual(len({p for p, _ in up.calls}), 1)

    def test_deterministic_uploads_retry_transient_errors_and_fail_permanent_ones(self):
        up = S.ScriptedUploader([503, 204])
        self.assertEqual(upload.upload_file(up, "p", b"d", deterministic=True, **NO_SLEEP).outcome, upload.UPLOADED)
        up = S.ScriptedUploader([403])
        r = upload.upload_file(up, "p", b"d", deterministic=True, **NO_SLEEP)
        self.assertEqual((r.outcome, r.reason, len(up.calls)), (upload.FAILED, "HTTP 403", 1))

    def test_the_live_path_is_unchanged_a_first_attempt_409_rekeys(self):
        """The same script, deterministic=False, still re-keys (this is what the bridge does)."""
        paths = iter(["p2", "p3"])
        up = S.ScriptedUploader([409, 204])
        r = upload.upload_file(up, "p1", b"d", deterministic=False, rekey=lambda: next(paths), **NO_SLEEP)
        self.assertEqual((r.outcome, r.rekeys, r.relpath), (upload.UPLOADED, 1, "p2"))
        self.assertEqual([p for p, _ in up.calls], ["p1", "p2"])
        # ... and the live path with no unknown attempt and no way to re-key fails rather than pretending
        up = S.ScriptedUploader([409])
        r = upload.upload_file(up, "p1", b"d", deterministic=False, rekey=None, **NO_SLEEP)
        self.assertEqual(r.outcome, upload.FAILED)

    def test_the_live_bridge_still_rekeys_on_a_first_attempt_409(self):
        up = S.ScriptedUploader([409, 204])
        b = bridge.Bridge(up, dead_letter_path=Path(tempfile.mkdtemp()) / "d.ndjson", clock=S.Clock().now,
                          sleep=lambda s: None, log=lambda *a: None)
        batch = [x for m in S.scan_messages() for x in b.handle_message(m)][0]
        self.assertTrue(b.upload(batch))
        self.assertNotEqual(up.calls[0][0], up.calls[1][0])
        self.assertEqual((b.stats["rekeyed"], b.stats["landed_before_retry"]), (1, 0))


class RestartedBackfillTests(unittest.TestCase):
    def test_a_restarted_upload_produces_zero_duplicate_files(self):
        with tempfile.TemporaryDirectory() as d:
            data = items(n=30)
            with self.assertRaises(Crash):  # the first run dies after 12 files
                upload.upload_tree(CrashAfter(bridge.LocalDirUploader(d), 12), data, **NO_SLEEP)
            self.assertEqual(len(landed(d)), 12)
            summary = upload.upload_tree(bridge.LocalDirUploader(d), data, **NO_SLEEP)  # the restart
            self.assertEqual((summary.uploaded, summary.already_landed, summary.failed), (18, 12, 0))
            files = landed(d)
            self.assertEqual(len(files), 30)
            self.assertEqual(len({f.name for f in files}), 30, "no file name appears twice")
            self.assertEqual(sorted(sha(f) for f in files), sorted(hashlib.sha256(b).hexdigest() for _, b in data))
            again = upload.upload_tree(bridge.LocalDirUploader(d), data, **NO_SLEEP)  # a rerun of a finished upload
            self.assertEqual((again.uploaded, again.already_landed), (0, 30))
            self.assertEqual(len(landed(d)), 30)

    def test_a_lost_acknowledgement_does_not_duplicate_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            data = items(n=10)
            up = LostAck(bridge.LocalDirUploader(d), lose_on={3, 7})  # the server has the file, the client never heard
            summary = upload.upload_tree(up, data, **NO_SLEEP)
            self.assertEqual((summary.uploaded, summary.already_landed, summary.failed), (8, 2, 0))
            self.assertEqual(len(landed(d)), 10)
            self.assertEqual(len({f.name for f in landed(d)}), 10)

    def test_a_crash_and_a_lost_ack_together(self):
        with tempfile.TemporaryDirectory() as d:
            data = items(n=20)
            with self.assertRaises(Crash):
                upload.upload_tree(CrashAfter(LostAck(bridge.LocalDirUploader(d), {5}), 9), data, **NO_SLEEP)
            summary = upload.upload_tree(bridge.LocalDirUploader(d), data, **NO_SLEEP)
            self.assertEqual(summary.failed, 0)
            self.assertEqual(len(landed(d)), 20)
            self.assertEqual(len({f.name for f in landed(d)}), 20)

    def test_data_can_be_supplied_lazily(self):
        with tempfile.TemporaryDirectory() as d:
            lazy = [(rel, (lambda b=b: b)) for rel, b in items(n=5)]
            self.assertEqual(upload.upload_tree(bridge.LocalDirUploader(d), lazy, **NO_SLEEP).uploaded, 5)

    def test_one_permanent_failure_is_counted_and_the_rest_still_upload(self):
        with tempfile.TemporaryDirectory() as d:
            data = items(n=7)
            denied = data[2][0]

            class Deny(bridge.LocalDirUploader):
                def put(self, relpath, payload):
                    return 403 if relpath == denied else super().put(relpath, payload)
            summary = upload.upload_tree(Deny(d), data, **NO_SLEEP)
            self.assertEqual((summary.uploaded, summary.failed), (6, 1))
            self.assertEqual(len(landed(d)), 6)


class FixedPrefixTests(unittest.TestCase):
    def state(self):
        return Path(tempfile.mkdtemp()) / "backfill_upload_state.json"

    def test_the_prefix_is_assigned_once_and_kept_across_restarts_in_a_later_hour(self):
        path = self.state()
        first = upload.fixed_prefix(path, 1_788_000_000.0, "run-A")          # 2026-08-29 10:40 UTC
        later = upload.fixed_prefix(path, 1_788_000_000.0 + 3600 * 5, "run-A")  # restarted five hours later
        nextday = upload.fixed_prefix(path, 1_788_000_000.0 + 86400 * 2, "run-A")
        self.assertEqual((first, later, nextday), (("2026-08-29", "10"),) * 3)
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((state["run_id"], state["dt"], state["hh"]), ("run-A", "2026-08-29", "10"))

    def test_a_restart_in_a_later_hour_still_produces_zero_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            state = self.state()

            def plan(now):
                dt, hh = upload.fixed_prefix(state, now, "run-A")
                return [(upload.backfill_relpath(dt, hh, "probe-01", bridge.new_ulid(1_780_000_000_000 + i, randomness=i)),
                         f"scan {i}\n".encode()) for i in range(20)]
            with self.assertRaises(Crash):
                upload.upload_tree(CrashAfter(bridge.LocalDirUploader(d), 8), plan(1_788_000_000.0), **NO_SLEEP)
            summary = upload.upload_tree(bridge.LocalDirUploader(d), plan(1_788_000_000.0 + 3 * 3600), **NO_SLEEP)
            self.assertEqual((summary.uploaded, summary.already_landed), (12, 8))
            self.assertEqual(len(landed(d)), 20)
            self.assertEqual(len({f.name for f in landed(d)}), 20)
            self.assertEqual({f.parent.name for f in landed(d)}, {"hh=10"})

    def test_a_naive_prefix_from_the_current_time_WOULD_duplicate(self):
        """Why fixed_prefix exists: the same deterministic file name under a new dt/hh is a different path, so
        the restart uploads it again and Auto Loader would load both."""
        with tempfile.TemporaryDirectory() as d:
            def naive(now):
                m = __import__("datetime").datetime.fromtimestamp(now, __import__("datetime").timezone.utc)
                return [(upload.backfill_relpath(f"{m:%Y-%m-%d}", f"{m:%H}", "probe-01",
                                                 bridge.new_ulid(1_780_000_000_000 + i, randomness=i)), b"x") for i in range(5)]
            upload.upload_tree(bridge.LocalDirUploader(d), naive(1_788_000_000.0), **NO_SLEEP)
            upload.upload_tree(bridge.LocalDirUploader(d), naive(1_788_000_000.0 + 3600), **NO_SLEEP)
            files = landed(d)
            self.assertEqual(len(files), 10)
            self.assertEqual(len({f.name for f in files}), 5, "the same 5 file names, twice")

    def test_a_different_run_may_not_reuse_the_saved_prefix(self):
        path = self.state()
        upload.fixed_prefix(path, 1_788_000_000.0, "run-A")
        with self.assertRaises(upload.PrefixConflict) as cm:
            upload.fixed_prefix(path, 1_788_000_000.0, "run-B")
        self.assertIn("run-A", str(cm.exception))

    def test_the_state_file_is_written_atomically(self):
        path = self.state()
        upload.fixed_prefix(path, 1_788_000_000.0, "run-A")
        self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])


if __name__ == "__main__":
    unittest.main()
