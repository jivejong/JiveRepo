"""serving/logrotate.py: rotate_if_large (doc 05 "Serving (Phase 7)", finding 23)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pg_support  # noqa: F401  (puts the repo root on the path, like the other serving tests)
from serving import deploy, logrotate, server


class Rotate(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "serve.log"

    def names(self):
        return sorted(p.name for p in self.dir.iterdir())

    def rotate(self, path, **kwargs):
        """rotate_if_large, failing the test (an assertion, not an error) if it raises."""
        try:
            return logrotate.rotate_if_large(path, **kwargs)
        except Exception as e:
            self.fail(f"rotate_if_large raised {e!r}")

    def test_the_defaults(self):
        self.assertEqual((logrotate.MAX_BYTES, logrotate.KEEP), (5_000_000, 3))

    def test_a_file_under_the_size_does_not_move(self):
        self.path.write_bytes(b"x" * 100)
        self.assertFalse(logrotate.rotate_if_large(self.path, max_bytes=100))             # exactly max_bytes is not "exceeds"
        self.assertEqual(self.names(), ["serve.log"])
        self.assertEqual(self.path.read_bytes(), b"x" * 100)

    def test_a_file_over_the_size_becomes_dot_1(self):
        self.path.write_bytes(b"a" * 101)
        self.assertTrue(logrotate.rotate_if_large(self.path, max_bytes=100))
        self.assertEqual(self.names(), ["serve.log.1"])
        self.assertEqual((self.dir / "serve.log.1").read_bytes(), b"a" * 101)

    def test_the_chain_shifts_and_keep_is_honoured(self):
        for name, body in (("serve.log", b"new" * 50), ("serve.log.1", b"one"), ("serve.log.2", b"two"), ("serve.log.3", b"three")):
            (self.dir / name).write_bytes(body)
        self.assertTrue(logrotate.rotate_if_large(self.path, max_bytes=100))
        self.assertEqual(self.names(), ["serve.log.1", "serve.log.2", "serve.log.3"])      # no serve.log.4, and the old .3 is gone
        self.assertEqual((self.dir / "serve.log.1").read_bytes(), b"new" * 50)
        self.assertEqual((self.dir / "serve.log.2").read_bytes(), b"one")
        self.assertEqual((self.dir / "serve.log.3").read_bytes(), b"two")

    def test_a_gap_in_the_chain_is_fine(self):
        (self.dir / "serve.log").write_bytes(b"n" * 200)
        (self.dir / "serve.log.2").write_bytes(b"two")
        self.assertTrue(logrotate.rotate_if_large(self.path, max_bytes=100))
        self.assertEqual(self.names(), ["serve.log.1", "serve.log.3"])
        self.assertEqual((self.dir / "serve.log.3").read_bytes(), b"two")

    def test_a_different_keep(self):
        for name in ("serve.log", "serve.log.1", "serve.log.2"):
            (self.dir / name).write_bytes(b"z" * 200)
        logrotate.rotate_if_large(self.path, max_bytes=100, keep=2)
        self.assertEqual(self.names(), ["serve.log.1", "serve.log.2"])

    def test_a_missing_file_is_a_no_op(self):
        self.assertFalse(self.rotate(self.dir / "nothing.log"))
        self.assertEqual(self.names(), [])

    def test_it_never_raises_when_a_replace_fails(self):
        self.path.write_bytes(b"x" * 200)
        with mock.patch("serving.logrotate.os.replace", side_effect=PermissionError("in use")):
            self.assertFalse(self.rotate(self.path, max_bytes=100))
        self.assertEqual(self.path.read_bytes(), b"x" * 200)


class Callers(unittest.TestCase):
    # serving.publish rotates serve.log only after it holds serve.lock and the debounce has passed: test_publish.py, class Rotation.

    def test_the_server_rotates_dashboard_log_and_deploy_log_at_start(self):
        with mock.patch("serving.server.logrotate.rotate_if_large") as rotate, mock.patch("serving.server.configure_logging"), \
                mock.patch("serving.server.make_server", side_effect=OSError("port")):
            self.assertEqual(server.main(["--port", "1"]), 1)
        self.assertEqual([c.args[0] for c in rotate.call_args_list], [server.LOG_PATH, deploy.DEPLOY_LOG])


if __name__ == "__main__":
    unittest.main()
