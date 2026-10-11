"""The bridge's dead-man's-switch pings (doc 05, "Monitoring"): S1 (bridge alive) and S3 (data landed). The rules under test:
a failing ping never reaches the bridge's own paths and never stops later pings; S1 only while connected and S3 only after a
non-empty landing, each at most once per 5 minutes; the only header ever sent is the project User-Agent; a full queue drops a ping
instead of waiting; a ping URL (credential-like) never appears in a log line; missing or invalid URLs disable monitoring with one
warning. Offline: a fake sender, a fake opener, a fake clock."""
import io
import re
import socket
import sys
import threading
import time
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402,F401  (puts ingest/bridge on sys.path)
import files_api  # noqa: E402
import monitor  # noqa: E402
from files_api import UA  # noqa: E402

S1_URL = "https://hc.invalid/11111111-1111-4111-8111-s1-not-a-real-url"
S3_URL = "https://hc.invalid/33333333-3333-4333-8333-s3-not-a-real-url"
BOTH = {"BRIDGE_PING_S1_URL": S1_URL, "BRIDGE_PING_S3_URL": S3_URL}


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Rig:
    """A Monitor over both URLs with a recording sender and a fake clock; `outcomes` scripts what the sender does."""

    def __init__(self, urls=None, outcomes=(), **options):
        self.clock, self.calls, self.logs = Clock(), [], []
        self.outcomes = list(outcomes)

        def sender(url, timeout=None):
            self.calls.append((url, timeout))
            outcome = self.outcomes.pop(0) if self.outcomes else None
            if isinstance(outcome, BaseException):
                raise outcome

        self.monitor = monitor.Monitor(dict(urls if urls is not None else {"S1": S1_URL, "S3": S3_URL}), sender=sender,
                                       clock=self.clock, log=self.logs.append, **options)

    def sent(self):
        assert self.monitor.drain(3), "the sender thread did not go idle (is it dead?)"
        return [url for url, _ in self.calls]


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def read(self):
        return b"OK"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FailureTests(unittest.TestCase):
    def test_a_failing_ping_never_propagates_and_the_next_ping_still_goes_out(self):
        rig = Rig(outcomes=[RuntimeError("boom " + S3_URL)])
        rig.monitor.landed(60)
        self.assertTrue(rig.monitor.drain(3), "the sender thread did not go idle")
        self.assertEqual(rig.monitor.stats["failed"], 1)
        rig.clock.advance(301)
        rig.monitor.landed(60)
        self.assertEqual(rig.sent(), [S3_URL, S3_URL], "the ping after a failure was never sent: the sender thread died")
        self.assertEqual(rig.monitor.stats["sent"], 1)

    def test_every_kind_of_failure_is_absorbed_and_described_without_the_url(self):
        # The real send_ping and the real files_api.http_request, with only the network (the opener) faked.
        def ping_through(opener):
            def sender(url, timeout=None):
                monitor.send_ping(url, timeout, http=lambda method, u, **kw: files_api.http_request(method, u, opener=opener, **kw))
            return sender

        def http_error(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 500, "Internal Server Error", {}, io.BytesIO(b""))

        def refused(request, timeout=None):
            raise urllib.error.URLError(ConnectionRefusedError())

        def timed_out(request, timeout=None):
            raise socket.timeout("timed out " + request.full_url)

        def status_500(request, timeout=None):
            return FakeResponse(500)

        def anything_else(request, timeout=None):
            raise RuntimeError("unexpected " + request.full_url)

        for name, opener, expected in (("HTTPError 500", http_error, "HTTP 500"), ("refused", refused, "network error (URLError)"),
                                       ("timeout", timed_out, "network error (TimeoutError)"), ("status 500", status_500, "HTTP 500"),
                                       ("anything else", anything_else, "RuntimeError")):
            sender = ping_through(opener)
            with self.subTest(name):
                logs, clock = [], Clock()
                m = monitor.Monitor({"S3": S3_URL}, sender=sender, clock=clock, log=logs.append)
                m.landed(60)
                self.assertTrue(m.drain(3))
                self.assertEqual(m.stats["failed"], 1)
                self.assertEqual(len(logs), 1)
                self.assertIn(expected, logs[0])
                self.assertNotIn("hc.invalid", logs[0], "the ping URL appeared in a log line")

    def test_the_sender_is_given_the_ten_second_timeout(self):
        rig = Rig()
        rig.monitor.landed(60)
        rig.sent()
        self.assertEqual(rig.calls[0][1], 10)
        self.assertEqual(monitor.PING_TIMEOUT_S, 10)

    def test_a_failure_is_logged_at_most_once_every_fifteen_minutes_and_counts_the_ones_it_skipped(self):
        rig = Rig(outcomes=[RuntimeError()] * 4)
        for _ in range(3):                                   # failures at t=0, 301, 602
            rig.monitor.landed(60)
            rig.monitor.drain(3)
            rig.clock.advance(301)
        self.assertEqual(len(rig.logs), 1, rig.logs)
        rig.clock.advance(400)                               # t=1303: more than 900 s since the logged one
        rig.monitor.landed(60)
        rig.monitor.drain(3)
        self.assertEqual(len(rig.logs), 2, rig.logs)
        self.assertIn("2 earlier failure(s) not logged", rig.logs[1])
        self.assertTrue(all("hc.invalid" not in line for line in rig.logs))

    def test_describe_never_returns_the_text_of_an_exception(self):
        self.assertEqual(monitor.describe(ValueError("unknown url " + S1_URL)), "ValueError")
        self.assertEqual(monitor.describe(monitor.PingFailed("HTTP 503")), "HTTP 503")
        self.assertEqual(monitor.describe(files_api.TransportError("URLError")), "network error (URLError)")


class SignalRuleTests(unittest.TestCase):
    def test_s1_only_while_connected_and_at_most_every_five_minutes(self):
        rig = Rig()
        for _ in range(5):
            rig.monitor.tick(False)
        self.assertEqual(rig.sent(), [], "S1 was sent while the MQTT client was not connected")
        rig.monitor.tick(True)
        self.assertEqual(rig.sent(), [S1_URL])
        rig.clock.advance(299)
        rig.monitor.tick(True)
        self.assertEqual(rig.sent(), [S1_URL], "S1 was sent again inside five minutes")
        rig.clock.advance(2)
        rig.monitor.tick(True)
        self.assertEqual(rig.sent(), [S1_URL, S1_URL])
        rig.clock.advance(300)
        rig.monitor.tick(False)
        self.assertEqual(rig.sent(), [S1_URL, S1_URL], "S1 was sent after the connection dropped")

    def test_s3_only_after_a_non_empty_landing_and_at_most_every_five_minutes(self):
        rig = Rig()
        rig.monitor.landed(0)
        self.assertEqual(rig.sent(), [], "S3 was sent for a landing of zero lines")
        rig.monitor.landed(60)
        self.assertEqual(rig.sent(), [S3_URL])
        rig.clock.advance(299)
        rig.monitor.landed(60)
        self.assertEqual(rig.sent(), [S3_URL], "S3 was sent again inside five minutes")
        rig.clock.advance(2)
        rig.monitor.landed(1)
        self.assertEqual(rig.sent(), [S3_URL, S3_URL])

    def test_s1_and_s3_are_independent(self):
        rig = Rig()
        rig.monitor.tick(True)
        rig.monitor.landed(60)
        self.assertEqual(sorted(rig.sent()), sorted([S1_URL, S3_URL]))

    def test_a_signal_without_a_url_is_never_sent(self):
        rig = Rig(urls={"S1": S1_URL})
        rig.monitor.landed(60)
        rig.monitor.tick(True)
        self.assertEqual(rig.sent(), [S1_URL])


class SenderTests(unittest.TestCase):
    """send_ping is files_api.http_request called with no headers; the network is the only thing faked here."""

    def capture(self, status=200):
        seen = []

        def opener(request, timeout=None):
            seen.append((request, timeout))
            return FakeResponse(status)

        def http(method, url, **kw):
            return files_api.http_request(method, url, opener=opener, **kw)
        return seen, http

    def test_the_project_user_agent_is_the_only_header_sent(self):
        seen, http = self.capture()
        monitor.send_ping(S1_URL, 10, http=http)
        (request, timeout), = seen
        self.assertEqual(request.full_url, S1_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(timeout, 10)
        self.assertEqual(request.header_items(), [("User-agent", UA)],
                         "a ping must carry the project User-Agent and nothing else (no credentials, no extra headers)")

    def test_no_authorization_header_is_ever_sent_even_with_credentials_in_the_environment(self):
        import os
        from unittest import mock
        seen, http = self.capture()
        env = {"DATABRICKS_TOKEN": "t", "BRIDGE_DATABRICKS_CLIENT_SECRET": "s", "HTTP_AUTHORIZATION": "x"}
        with mock.patch.dict(os.environ, env):
            monitor.send_ping(S3_URL, 10, http=http)
        names = {name.lower() for name, _ in seen[0][0].header_items()}
        self.assertNotIn("authorization", names)
        self.assertEqual(names, {"user-agent"})

    def test_the_default_sender_is_the_repos_one_outbound_http_path(self):
        self.assertIs(monitor.send_ping.__defaults__[1], files_api.http_request)

    def test_the_module_has_no_way_to_attach_credentials_or_to_open_its_own_connection(self):
        source = Path(monitor.__file__).read_text(encoding="utf-8")
        for forbidden in ("Authorization", "TokenProvider", "FilesApiUploader", "headers=", "urllib.request", "urlopen"):
            self.assertNotIn(forbidden, source)
        imported = re.findall(r"^from files_api import (.+)$", source, re.M)
        self.assertEqual(imported, ["TransportError, http_request"],
                         "monitor.py may import only http_request and TransportError from files_api")

    def test_a_non_2xx_answer_is_a_failure(self):
        seen, http = self.capture(status=500)
        with self.assertRaises(monitor.PingFailed):
            monitor.send_ping(S1_URL, 10, http=http)
        seen, http = self.capture(status=204)
        monitor.send_ping(S1_URL, 10, http=http)                    # 2xx is success


class QueueTests(unittest.TestCase):
    def test_a_full_queue_drops_the_ping_instead_of_waiting(self):
        clock, calls = Clock(), []
        m = monitor.Monitor({"S1": S1_URL}, sender=lambda url, timeout=None: calls.append(url), clock=clock,
                            log=lambda m: None, queue_size=2, s1_interval=0, start=False)   # no sender thread: nothing drains

        def hammer():
            for _ in range(5):
                clock.advance(1)
                m.tick(True)
        worker = threading.Thread(target=hammer, daemon=True)     # daemon: a blocked tick() must fail the test, not hang the run
        worker.start()
        worker.join(3)
        self.assertFalse(worker.is_alive(), "tick() blocked on a full queue")
        self.assertEqual(m.stats["dropped"], 3)

    def test_a_sender_that_hangs_never_holds_up_the_caller(self):
        release = threading.Event()
        m = monitor.Monitor({"S1": S1_URL, "S3": S3_URL}, sender=lambda url, timeout=None: release.wait(10), clock=Clock(),
                            log=lambda m: None, queue_size=2, s1_interval=0, s3_min_interval=0)
        def hammer():
            for _ in range(50):
                m.tick(True)
                m.landed(60)
        started = time.time()
        caller = threading.Thread(target=hammer, daemon=True)      # daemon: if the monitor blocks, the test fails instead of hanging
        caller.start()
        caller.join(3)
        self.assertFalse(caller.is_alive(), "the bridge's own thread waited on the monitor")
        self.assertLess(time.time() - started, 2.5)
        release.set()
        m.close()


class DisabledTests(unittest.TestCase):
    def test_missing_keys_disable_monitoring_with_one_warning_and_no_thread(self):
        logs = []
        m = monitor.build_monitor({}, log=logs.append)
        self.assertFalse(m.enabled)
        self.assertEqual(len(logs), 1, logs)
        self.assertIn("BRIDGE_PING_S1_URL", logs[0])
        self.assertIn("BRIDGE_PING_S3_URL", logs[0])
        self.assertFalse(m._thread.is_alive(), "a disabled monitor must not start a sender thread")
        m.tick(True)
        m.landed(60)
        self.assertEqual(m.stats, {"sent": 0, "failed": 0, "dropped": 0})

    def test_one_missing_key_disables_only_that_signal_and_names_the_setting(self):
        logs = []
        m = monitor.build_monitor({"BRIDGE_PING_S1_URL": S1_URL}, log=logs.append)
        self.assertEqual(sorted(m.urls), ["S1"])
        self.assertEqual(len(logs), 1)
        self.assertIn("BRIDGE_PING_S3_URL", logs[0])
        self.assertNotIn(S1_URL, logs[0])

    def test_an_invalid_value_disables_that_signal_without_echoing_it(self):
        secret = "http://hc.invalid/not-https-secret-value"
        logs = []
        m = monitor.build_monitor({"BRIDGE_PING_S1_URL": secret, "BRIDGE_PING_S3_URL": S3_URL}, log=logs.append)
        self.assertEqual(sorted(m.urls), ["S3"])
        self.assertEqual(len(logs), 1)
        self.assertIn("BRIDGE_PING_S1_URL", logs[0])
        self.assertNotIn("not-https-secret-value", logs[0])
        for bad in ("not a url", "ftp://hc.invalid/x", "https://", "  "):
            with self.subTest(bad):
                urls, warnings = monitor.load_ping_urls({"BRIDGE_PING_S1_URL": bad, "BRIDGE_PING_S3_URL": S3_URL})
                self.assertNotIn("S1", urls)
                self.assertTrue(all(bad.strip() not in w for w in warnings if bad.strip()))

    def test_both_valid_keys_enable_both_signals(self):
        logs = []
        m = monitor.build_monitor(BOTH, log=logs.append, start=False)
        self.assertEqual(logs, [])
        self.assertEqual(m.urls, {"S1": S1_URL, "S3": S3_URL})


if __name__ == "__main__":
    unittest.main()
