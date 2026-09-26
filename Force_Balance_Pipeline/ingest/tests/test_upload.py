"""Bridge upload semantics: one path per batch reused on retry, no overwrite, 409 after an unknown-outcome
attempt counts as landed, backoff, permanent failure, and the local-directory stand-in. Offline."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402
from files_api import TransportError  # noqa: E402


def make(script=(), **kw):
    up = S.ScriptedUploader(script)
    sleeps = []
    d = tempfile.mkdtemp()
    b = bridge.Bridge(up, dead_letter_path=Path(d) / "dead.ndjson", clock=S.Clock().now, sleep=sleeps.append,
                      log=lambda *a: None, **kw)
    return b, up, sleeps, Path(d) / "dead.ndjson"


def one_batch(b):
    return [x for m in S.scan_messages() for x in b.handle_message(m)][0]


def dead_records(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class SuccessTests(unittest.TestCase):
    def test_success_uploads_the_file_bytes_once_and_counts_it(self):
        b, up, sleeps, dead = make()
        batch = one_batch(b)
        self.assertTrue(b.upload(batch))
        self.assertEqual(len(up.calls), 1)
        self.assertEqual(up.calls[0], (batch.relpath, b"".join(batch.lines)))
        self.assertEqual((b.stats["files_uploaded"], b.stats["events_written"]), (1, 60))
        self.assertEqual(sleeps, [])
        self.assertIsNotNone(b.stats["last_flush_utc"])

    def test_every_success_status_is_success(self):
        for status in (200, 201, 204):
            b, up, _, _ = make([status])
            self.assertTrue(b.upload(one_batch(b)), status)


class RetryTests(unittest.TestCase):
    def test_a_network_error_is_retried_with_the_same_path_and_backoff(self):
        b, up, sleeps, _ = make([TransportError("timeout"), TransportError("reset"), 204])
        batch = one_batch(b)
        self.assertTrue(b.upload(batch))
        self.assertEqual(len(up.calls), 3)
        self.assertEqual(len({path for path, _ in up.calls}), 1, "the path (and its ULID) is chosen once")
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(b.stats["retries"], 2)

    def test_server_errors_are_retried(self):
        b, up, sleeps, _ = make([503, 500, 204])
        self.assertTrue(b.upload(one_batch(b)))
        self.assertEqual(len({p for p, _ in up.calls}), 1)
        self.assertEqual(sleeps, [1, 2])

    def test_rate_limiting_is_retried(self):
        b, up, sleeps, _ = make([429, 204])
        self.assertTrue(b.upload(one_batch(b)))
        self.assertEqual(sleeps, [1])

    def test_gives_up_after_max_attempts_and_dead_letters_the_events(self):
        b, up, sleeps, dead = make([503] * 10, max_attempts=3)
        batch = one_batch(b)
        self.assertFalse(b.upload(batch))
        self.assertEqual(len(up.calls), 3)
        self.assertEqual(sleeps, [1, 2])  # no sleep after the last attempt
        records = dead_records(dead)
        self.assertEqual(len(records), 60)
        self.assertTrue(all(r["reason"].startswith("upload_failed: gave up after 3 attempts") for r in records))
        self.assertEqual([r["raw"] for r in records], [l.decode().rstrip("\n") for l in batch.lines])  # one event per record
        self.assertEqual(b.stats["failed_batches"], 1)
        self.assertEqual(b.stats["files_uploaded"], 0)

    def test_permanent_errors_are_not_retried(self):
        for status in (400, 401, 403, 404):
            b, up, sleeps, dead = make([status])
            self.assertFalse(b.upload(one_batch(b)))
            self.assertEqual((len(up.calls), sleeps), (1, []), status)
            self.assertEqual(len(dead_records(dead)), 60)
            self.assertIn(f"HTTP {status}", dead_records(dead)[0]["reason"])

    def test_backoff_grows_and_is_capped(self):
        b, up, sleeps, _ = make([TransportError("x")] * 7, max_attempts=8, backoff=(1, 2, 4))
        b.upload(one_batch(b))
        self.assertEqual(sleeps, [1, 2, 4, 4, 4, 4, 4])


class ConflictTests(unittest.TestCase):
    def test_409_after_a_network_error_means_the_earlier_attempt_landed(self):
        b, up, sleeps, dead = make([TransportError("timeout"), 409])
        self.assertTrue(b.upload(one_batch(b)))
        self.assertEqual(len({p for p, _ in up.calls}), 1)
        self.assertEqual(b.stats["landed_before_retry"], 1)
        self.assertEqual(b.stats["rekeyed"], 0)
        self.assertEqual((b.stats["files_uploaded"], b.stats["events_written"]), (1, 60))
        self.assertFalse(dead.exists(), "nothing is lost and nothing is dead-lettered")

    def test_409_after_a_server_error_means_the_earlier_attempt_may_have_landed(self):
        b, up, _, _ = make([502, 409])
        self.assertTrue(b.upload(one_batch(b)))
        self.assertEqual(b.stats["landed_before_retry"], 1)

    def test_409_on_the_first_attempt_is_a_real_collision_and_the_batch_is_rekeyed(self):
        b, up, sleeps, _ = make([409, 204])
        batch = one_batch(b)
        first_path = batch.relpath
        self.assertTrue(b.upload(batch))
        self.assertEqual(len(up.calls), 2)
        self.assertNotEqual(up.calls[0][0], up.calls[1][0])
        self.assertEqual(up.calls[0][0].rsplit("/", 1)[0], up.calls[1][0].rsplit("/", 1)[0], "same dt/hh directory")
        self.assertEqual(b.stats["rekeyed"], 1)
        self.assertEqual(b.stats["landed_before_retry"], 0)
        self.assertNotEqual(batch.relpath, first_path)
        self.assertEqual(sleeps, [])

    def test_409_after_rate_limiting_is_a_collision_not_a_landing(self):
        """429 is rejected before processing, so the file was not written by that attempt."""
        b, up, _, _ = make([429, 409, 204])
        self.assertTrue(b.upload(one_batch(b)))
        self.assertEqual((b.stats["rekeyed"], b.stats["landed_before_retry"]), (1, 0))

    def test_a_persistent_collision_gives_up_rather_than_looping(self):
        b, up, _, dead = make([409] * 10)
        self.assertFalse(b.upload(one_batch(b)))
        self.assertEqual(len(up.calls), 4)  # the first ULID and three new ones
        self.assertEqual(len(dead_records(dead)), 60)

    def test_the_path_is_never_overwritten_by_design(self):
        """The uploader contract has no overwrite flag: put() is create-only. FilesApiUploader sends
        overwrite=false (test_files_api); LocalDirUploader returns 409 (below)."""
        import inspect
        self.assertEqual(list(inspect.signature(bridge.LocalDirUploader.put).parameters), ["self", "relpath", "data"])


class LocalDirTests(unittest.TestCase):
    def test_writes_the_path_atomically_and_creates_directories(self):
        with tempfile.TemporaryDirectory() as d:
            up = bridge.LocalDirUploader(d)
            self.assertEqual(up.put("dt=2026-08-29/hh=10/probe-01-X.ndjson", b"a\nb\n"), 201)
            path = Path(d) / "dt=2026-08-29" / "hh=10" / "probe-01-X.ndjson"
            self.assertEqual(path.read_bytes(), b"a\nb\n")
            self.assertEqual([p.name for p in path.parent.iterdir()], ["probe-01-X.ndjson"], "no temp file is left")

    def test_an_existing_path_is_409_and_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            up = bridge.LocalDirUploader(d)
            up.put("dt=2026-08-29/hh=10/f.ndjson", b"first\n")
            self.assertEqual(up.put("dt=2026-08-29/hh=10/f.ndjson", b"second\n"), 409)
            self.assertEqual((Path(d) / "dt=2026-08-29" / "hh=10" / "f.ndjson").read_bytes(), b"first\n")

    def test_bridge_end_to_end_into_a_directory(self):
        with tempfile.TemporaryDirectory() as d:
            b = bridge.Bridge(bridge.LocalDirUploader(d), dead_letter_path=Path(d) / "dead.ndjson",
                              clock=S.Clock().now, sleep=lambda s: None, log=lambda *a: None)
            msgs = S.scan_messages()
            batches = [x for m in msgs for x in b.handle_message(m)]
            self.assertTrue(all(b.upload(x) for x in batches))
            files = sorted(Path(d).rglob("*.ndjson"))
            files = [f for f in files if f.name != "dead.ndjson"]
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), b"".join(m + b"\n" for m in msgs))


if __name__ == "__main__":
    unittest.main()
