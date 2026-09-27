"""upload_tree_parallel and pin_prefix (docs 02 and 04): several files in flight, the same per-file semantics as the
sequential upload (409 anywhere means already landed, transient errors retried, permanent ones counted), lazy data,
and a state file that can be recreated but never contradicted. Offline."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402,F401
import upload as up  # noqa: E402
from files_api import TransportError  # noqa: E402

NO_WAIT = dict(sleep=lambda seconds: None, log=lambda text: None)


class MemoryVolume:
    """A volume that never overwrites: 409 if the path exists. Thread-safe. `script` maps a substring of a path to
    the statuses to return for it before behaving normally."""

    def __init__(self, script=None):
        self.files, self.calls, self.script = {}, [], dict(script or {})
        self.lock = threading.Lock()

    def put(self, relpath, data):
        with self.lock:
            self.calls.append(relpath)
            for key, steps in self.script.items():
                if key in relpath and steps:
                    step = steps.pop(0)
                    if isinstance(step, Exception):
                        raise step
                    return step
            if relpath in self.files:
                return 409
            self.files[relpath] = data
            return 204


def items(n, prefix="dt=2026-09-26/hh=10/probe-01-"):
    return [(f"{prefix}{i:04d}.ndjson", f"data-{i}".encode()) for i in range(n)]


class ParallelUploadTests(unittest.TestCase):
    def test_every_file_lands_exactly_once_with_the_same_bytes(self):
        volume = MemoryVolume()
        summary = up.upload_tree_parallel(volume, items(200), workers=8, **NO_WAIT)
        self.assertEqual((summary.uploaded, summary.already_landed, summary.failed), (200, 0, 0))
        self.assertEqual(volume.files, dict(items(200)))
        self.assertEqual(len(volume.calls), 200)

    def test_it_gives_the_same_result_as_the_sequential_upload(self):
        sequential, parallel = MemoryVolume(), MemoryVolume()
        a = up.upload_tree(sequential, items(40), **NO_WAIT)
        b = up.upload_tree_parallel(parallel, items(40), workers=4, **NO_WAIT)
        self.assertEqual((a.uploaded, a.already_landed, a.failed), (b.uploaded, b.already_landed, b.failed))
        self.assertEqual(sequential.files, parallel.files)

    def test_files_really_are_in_flight_together(self):
        barrier = threading.Barrier(4, timeout=5)
        seen = []

        class Blocking:
            def put(self, relpath, data):
                seen.append(threading.current_thread().name)
                barrier.wait()          # four uploads must be in progress at once for anyone to pass
                return 204
        summary = up.upload_tree_parallel(Blocking(), items(4), workers=4, **NO_WAIT)
        self.assertEqual(summary.uploaded, 4)
        self.assertEqual(len(set(seen)), 4)

    def test_one_worker_is_sequential_and_zero_is_refused(self):
        volume = MemoryVolume()
        up.upload_tree_parallel(volume, items(10), workers=1, **NO_WAIT)
        self.assertEqual(volume.calls, [r for r, _ in items(10)])
        with self.assertRaises(ValueError):
            up.upload_tree_parallel(volume, items(1), workers=0, **NO_WAIT)

    def test_a_409_anywhere_means_already_landed_and_nothing_is_rewritten(self):
        volume = MemoryVolume()
        volume.files.update(dict(items(30)[:10]))            # an earlier run landed the first ten
        summary = up.upload_tree_parallel(volume, items(30), workers=4, **NO_WAIT)
        self.assertEqual((summary.uploaded, summary.already_landed, summary.failed), (20, 10, 0))
        self.assertEqual(volume.files, dict(items(30)))

    def test_a_lost_acknowledgement_is_not_a_duplicate(self):
        """The server stored the file but the response was lost: the retry gets a 409, which counts as landed."""
        volume = MemoryVolume()
        original_put = volume.put
        lost = {"once": True}

        def put(relpath, data):
            status = original_put(relpath, data)
            if relpath.endswith("0003.ndjson") and lost["once"]:
                lost["once"] = False
                raise TransportError("connection reset")
            return status
        volume.put = put
        summary = up.upload_tree_parallel(volume, items(8), workers=4, **NO_WAIT)
        self.assertEqual((summary.uploaded, summary.already_landed, summary.failed), (7, 1, 0))
        self.assertEqual(len(volume.files), 8)

    def test_transient_errors_are_retried_and_counted(self):
        volume = MemoryVolume(script={"0002": [503, 429]})
        summary = up.upload_tree_parallel(volume, items(5), workers=3, **NO_WAIT)
        self.assertEqual((summary.uploaded, summary.failed, summary.retries), (5, 0, 2))

    def test_a_permanent_failure_is_counted_named_and_the_rest_still_upload(self):
        volume = MemoryVolume(script={"0004": [403]})
        logged = []
        summary = up.upload_tree_parallel(volume, items(9), workers=4, sleep=lambda s: None, log=logged.append)
        self.assertEqual((summary.uploaded, summary.failed), (8, 1))
        self.assertEqual(summary.failed_paths, [items(9)[4][0]])
        self.assertTrue(any("gave up" in line and "0004" in line for line in logged))

    def test_data_is_read_lazily_once_per_file(self):
        reads = []

        def reader(i):
            def read():
                reads.append(i)
                return f"data-{i}".encode()
            return read
        volume = MemoryVolume()
        up.upload_tree_parallel(volume, [(r, reader(i)) for i, (r, _) in enumerate(items(12))], workers=3, **NO_WAIT)
        self.assertEqual(sorted(reads), list(range(12)))
        self.assertEqual(volume.files, dict(items(12)))

    def test_progress_is_reported_in_the_calling_thread_once_per_file(self):
        ticks, main = [], threading.current_thread()

        def progress(relpath, result, summary):
            ticks.append((threading.current_thread() is main, summary.uploaded + summary.already_landed))
        up.upload_tree_parallel(MemoryVolume(), items(25), workers=5, progress=progress, **NO_WAIT)
        self.assertEqual([done for _, done in ticks], list(range(1, 26)))
        self.assertTrue(all(in_main for in_main, _ in ticks))

    def test_an_unexpected_exception_stops_the_run_and_propagates(self):
        started, gate = [], threading.Event()

        def boom():
            raise OSError("the disk went away")

        class Held(MemoryVolume):
            def put(self, relpath, data):
                started.append(relpath)
                gate.wait(5)                      # the two workers stay busy until the gate opens
                return super().put(relpath, data)
        many = [(f"dt=2026-09-26/hh=10/probe-01-{i:04d}.ndjson", boom if i == 0 else b"x") for i in range(2000)]
        opener = threading.Timer(0.5, gate.set)
        opener.start()
        try:
            with self.assertRaises(OSError):
                up.upload_tree_parallel(Held(), many, workers=2, **NO_WAIT)
            self.assertLessEqual(len(started), 4, "the files not yet started were cancelled, not waited for")
        finally:
            gate.set()
            opener.cancel()


class PinPrefixTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp()) / "sub" / "state.json"

    def test_it_recreates_a_lost_state_file_which_fixed_prefix_then_honours(self):
        self.assertEqual(up.pin_prefix(self.state, "2026-09-26", "17", "run-a"), ("2026-09-26", "17"))
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual((state["run_id"], state["dt"], state["hh"], state["pinned"]), ("run-a", "2026-09-26", "17", True))
        self.assertEqual(up.fixed_prefix(self.state, 1_900_000_000.0, "run-a"), ("2026-09-26", "17"))  # not "now"

    def test_pinning_the_same_prefix_again_is_fine_and_a_different_one_is_refused(self):
        up.pin_prefix(self.state, "2026-09-26", "17", "run-a")
        self.assertEqual(up.pin_prefix(self.state, "2026-09-26", "17", "run-a"), ("2026-09-26", "17"))
        with self.assertRaisesRegex(up.PrefixConflict, "already pins dt=2026-09-26 hh=17"):
            up.pin_prefix(self.state, "2026-09-26", "18", "run-a")
        with self.assertRaises(up.PrefixConflict):
            up.pin_prefix(self.state, "2026-09-26", "17", "run-b")                   # another backfill
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["hh"], "17")

    def test_it_refuses_anything_that_is_not_a_real_prefix(self):
        for dt, hh in (("2026-9-26", "17"), ("20260926", "17"), ("2026-09-26", "7"), ("2026-09-26", "24"),
                       ("2026-13-01", "10"), ("2026-02-30", "10"), ("", "10"), (None, None), ("dt=2026-09-26", "hh=17")):
            with self.subTest(dt=dt, hh=hh):
                with self.assertRaises(up.PrefixConflict):
                    up.pin_prefix(self.state, dt, hh, "run-a")
        self.assertFalse(self.state.exists())

    def test_the_state_file_is_written_atomically_and_leaves_nothing_behind(self):
        up.pin_prefix(self.state, "2026-09-26", "17", "run-a")
        self.assertEqual([p.name for p in self.state.parent.iterdir()], ["state.json"])


if __name__ == "__main__":
    unittest.main()
