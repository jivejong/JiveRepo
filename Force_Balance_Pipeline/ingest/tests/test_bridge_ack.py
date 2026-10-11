"""Acknowledge after landing (docs 02, 04, 05): the bridge sends a message's PUBACK only once its batch has landed or its line
is in the dead-letter file, in the order the PUBLISHes arrived (MQTT-4.6.0-2), from one thread, and never for a message
received on an earlier connection. The broker's persistent session therefore holds every event until it is safe, and a bridge
killed at any moment loses nothing; the price is duplicates, which are accepted and counted. Offline: a fake MQTT client."""
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402


def event_ids(payloads):
    return [json.loads(p)["event_id"] for p in payloads]


class Rig:
    """A Bridge over a local landing directory, with MQTT message ids handed out like a broker does."""

    def __init__(self, test, uploader=None, **bridge_options):
        self.dir = Path(tempfile.mkdtemp())
        self.landing = self.dir / "landing"
        self.dead = self.dir / "dead.ndjson"
        self.log_lines, self.order = [], []
        options = dict(dead_letter_path=self.dead, sleep=lambda s: self.order.append(("sleep", s)), log=self.log_lines.append,
                       max_attempts=2, backoff=(0, 0))
        options.update(bridge_options)
        self.bridge = bridge.Bridge(uploader or bridge.LocalDirUploader(self.landing), **options)
        self.bridge.acks.on_ready = lambda: self.order.append(("ready",))
        self.next_mid = 1
        self.released = []

    def feed(self, payloads, dup=False, qos=1):
        """Deliver payloads as one message each; returns the batches that became ready."""
        batches = []
        for payload in payloads:
            token = self.bridge.acks.register(self.next_mid, qos)
            self.next_mid += 1
            batches += self.bridge.handle_message(payload, token=token, dup=dup)
        return batches

    def acked(self):
        """Message ids released so far, in release order."""
        self.released += [mid for mid, qos in self.bridge.acks.pop_ready()]
        return list(self.released)

    def landed_lines(self):
        files = sorted(self.landing.rglob("*.ndjson")) if self.landing.exists() else []
        return [[json.loads(l)["event_id"] for l in f.read_text(encoding="utf-8").splitlines()] for f in files]

    def dead_records(self):
        return [json.loads(l) for l in self.dead.read_text(encoding="utf-8").splitlines()] if self.dead.exists() else []


class AckAfterSafeTests(unittest.TestCase):
    def test_nothing_is_acknowledged_until_the_batch_lands(self):
        rig = Rig(self)
        batches = rig.feed(S.scan_messages(0))
        self.assertEqual(len(batches), 1, "a complete scan flushes as one batch")
        self.assertEqual(rig.acked(), [], "acknowledged before anything landed: a kill now would lose the scan")
        self.assertTrue(rig.bridge.upload(batches[0]))
        self.assertEqual(rig.acked(), list(range(1, 61)))
        self.assertEqual(len(rig.landed_lines()), 1)

    def test_a_failed_upload_is_acknowledged_only_after_the_dead_letter_write(self):
        rig = Rig(self, uploader=S.ScriptedUploader([500] * 10))
        real_dead_letter = rig.bridge._dead_letter

        def recording(raw, reason, now):
            real_dead_letter(raw, reason, now)
            rig.order.append(("dead-letter",))
        rig.bridge._dead_letter = recording
        (batch,) = rig.feed(S.scan_messages(0))
        self.assertFalse(rig.bridge.upload(batch))
        kinds = [o[0] for o in rig.order if o[0] in ("dead-letter", "ready")]
        self.assertEqual(kinds[:60], ["dead-letter"] * 60, "acknowledged before the dead-letter file held the events")
        self.assertIn("ready", kinds)
        self.assertEqual(kinds.index("ready"), 60, "the first acknowledgement must follow the last dead-letter write")
        self.assertEqual(len(rig.dead_records()), 60)
        self.assertEqual(rig.acked(), list(range(1, 61)))

    def test_a_dead_letter_write_that_fails_is_never_acknowledged(self):
        blocker = Path(tempfile.mkdtemp()) / "a-file"
        blocker.write_text("not a directory", encoding="utf-8")
        rig = Rig(self, uploader=S.ScriptedUploader([500] * 10), dead_letter_path=blocker / "dead.ndjson")
        (batch,) = rig.feed(S.scan_messages(0))
        with self.assertRaises(bridge.DeadLetterFailure):
            rig.bridge.upload(batch)
        self.assertEqual(rig.acked(), [], "acknowledged although the events are neither landed nor dead-lettered")
        waits = [o[1] for o in rig.order if o[0] == "sleep" and o[1] > 0]      # the upload's own retries wait 0 here
        self.assertEqual(waits, [1, 2], "retried a few times with a short backoff before giving up")

    def test_an_unreadable_message_is_acknowledged_after_its_dead_letter_write_not_before(self):
        blocker = Path(tempfile.mkdtemp()) / "a-file"
        blocker.write_text("not a directory", encoding="utf-8")
        failing = Rig(self, dead_letter_path=blocker / "dead.ndjson")
        token = failing.bridge.acks.register(1, 1)
        with self.assertRaises(bridge.DeadLetterFailure):
            failing.bridge.handle_message(b"not json", token=token)
        self.assertEqual(failing.acked(), [])
        rig = Rig(self)
        rig.feed([b"not json"])
        self.assertEqual(len(rig.dead_records()), 1)
        self.assertEqual(rig.acked(), [1])

    def test_acknowledgements_go_out_in_arrival_order(self):
        rig = Rig(self)
        first_half_of_a = S.scan_messages(0, seed=1)[:30]
        rest_of_a = S.scan_messages(0, seed=1)[30:]
        b = S.scan_messages(1, seed=2)
        self.assertEqual(rig.feed(first_half_of_a), [])
        (batch_b,) = rig.feed(b)                               # scan B completes first
        rig.bridge.upload(batch_b)
        self.assertEqual(rig.acked(), [], "B was acknowledged ahead of the earlier, unfinished A (MQTT-4.6.0-2)")
        (batch_a,) = rig.feed(rest_of_a)
        rig.bridge.upload(batch_a)
        self.assertEqual(rig.acked(), list(range(1, 121)))


class ConnectionTests(unittest.TestCase):
    def test_acknowledgements_for_an_earlier_connection_are_never_sent(self):
        rig = Rig(self)
        msgs = S.scan_messages(0)
        rig.feed(msgs[:10])                                   # received on connection 1, still buffered
        rig.bridge.new_connection(session_present=False)       # the broker lost the session: it will not redeliver
        (batch,) = rig.feed(msgs[10:])
        rig.bridge.upload(batch)
        self.assertEqual(rig.acked(), list(range(11, 61)), "a stale message id was acknowledged on the new connection")
        self.assertEqual([len(f) for f in rig.landed_lines()], [60], "the buffered events were kept and landed")

    def test_a_kept_session_drops_the_buffer_because_the_broker_redelivers_it(self):
        rig = Rig(self)
        msgs = S.scan_messages(0)
        rig.feed(msgs[:40])
        rig.bridge.new_connection(session_present=True)
        self.assertEqual(rig.bridge.depth(), 0)
        self.assertEqual(rig.bridge.stats["dropped_for_redelivery"], 40)
        (batch,) = rig.feed(msgs, dup=True)                    # the broker redelivers everything unacknowledged, in order
        rig.bridge.upload(batch)
        self.assertEqual([len(f) for f in rig.landed_lines()], [60], "the redelivered scan landed once, not twice")
        self.assertEqual(rig.bridge.stats["redelivered"], 60)
        self.assertEqual(rig.acked(), list(range(41, 101)))

    def test_an_upload_in_flight_across_a_reconnect_lands_its_events_and_they_land_again_when_redelivered(self):
        entered, release = threading.Event(), threading.Event()

        class SlowUploader(bridge.LocalDirUploader):
            def put(self, relpath, data):
                entered.set()
                release.wait(10)
                return super().put(relpath, data)

        rig = Rig(self)
        rig.bridge.uploader = SlowUploader(rig.landing)
        msgs = S.scan_messages(0)
        (batch,) = rig.feed(msgs)
        uploading = threading.Thread(target=rig.bridge.upload, args=(batch,))
        uploading.start()
        self.assertTrue(entered.wait(10), "the upload never started")
        rig.bridge.new_connection(session_present=True)        # the connection drops and comes back mid-upload
        release.set()
        uploading.join(10)
        self.assertEqual([len(f) for f in rig.landed_lines()], [60], "the in-flight upload lands its events")
        self.assertEqual(rig.acked(), [], "its acknowledgements belong to the old connection and must be suppressed")
        (again,) = rig.feed(msgs, dup=True)                    # the broker still holds them unacknowledged: redelivery
        rig.bridge.upload(again)
        landed = rig.landed_lines()
        self.assertEqual(len(landed), 2, "accepted at-least-once duplicate: a second file with the same events")
        self.assertEqual(landed[0], landed[1])
        self.assertEqual(landed[0], event_ids(msgs))
        self.assertEqual(rig.bridge.stats["redelivered"], 60, "duplicates are counted")
        self.assertEqual(rig.acked(), list(range(61, 121)), "the redelivered copies are acknowledged on the new connection")


class ValveTests(unittest.TestCase):
    def events_from_four_scans(self):
        """30 events from each of four scans: 120 events and no scan complete."""
        out = []
        for scan in range(4):
            out += S.scan_messages(scan, seed=scan + 1)[:30]
        return out

    def test_the_valve_flushes_when_this_many_events_wait_without_a_complete_scan(self):
        self.assertEqual(bridge.DEFAULT_MAX_PENDING, 120)
        rig = Rig(self)
        events = self.events_from_four_scans()
        self.assertEqual(rig.feed(events[:119]), [])
        batches = rig.feed(events[119:])
        self.assertEqual([len(b.lines) for b in batches], [120], "the 120th pending event should flush all of them")

    def test_without_the_valve_nothing_flushes_until_the_timer(self):
        rig = Rig(self, max_pending=0)
        self.assertEqual(rig.feed(self.events_from_four_scans()), [])


class FakeClient:
    """Plays paho's part for run(): a 'loop thread' that connects and delivers messages, and a record of ack/disconnect order."""

    def __init__(self, script):
        self.script, self.events = script, []
        self.loop_thread = None

    def username_pw_set(self, *a):
        pass

    def reconnect_delay_set(self, *a, **k):
        pass

    def connect_async(self, *a, **k):
        pass

    def loop_start(self):
        self.loop_thread = threading.Thread(target=self._loop, name="fake-paho-loop")
        self.loop_thread.start()

    def _loop(self):
        flags = types.SimpleNamespace(session_present=self.script.get("session_present", False))
        reason = types.SimpleNamespace(is_failure=False, __str__=lambda s: "Success")
        self.on_connect(self, None, flags, reason, None)
        for mid, payload in self.script["messages"]:
            self.on_message(self, None, types.SimpleNamespace(mid=mid, qos=1, payload=payload, dup=False))

    def subscribe(self, *a, **k):
        pass

    def ack(self, mid, qos):
        self.events.append(("ack", mid, threading.get_ident()))

    def disconnect(self):
        self.events.append(("disconnect",))

    def loop_stop(self):
        if self.loop_thread:
            self.loop_thread.join(10)
        self.events.append(("loop_stop",))


class FakeMqtt:
    CallbackAPIVersion = types.SimpleNamespace(VERSION2="v2")

    def __init__(self, script):
        self.script, self.client = script, None

    def Client(self, *args, **kwargs):
        self.client = FakeClient(self.script)
        return self.client


class RunTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(bridge.logging.getLogger(bridge.LOGGER_NAME).handlers.clear)

    def args(self, *extra):
        return bridge.parse_args(["--local-dir", str(self.dir / "landing"), "--dead-letter", str(self.dir / "dead.ndjson"),
                                  "--stats-interval", "1000", *extra])

    def run_bridge(self, script, *extra, **bridge_options):
        args = self.args(*extra)
        b = bridge.Bridge(bridge.build_uploader(args), dead_letter_path=args.dead_letter, sleep=lambda s: None,
                          log=lambda m: None, max_attempts=2, backoff=(0, 0), **bridge_options)
        mqtt = FakeMqtt(script)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rc = bridge.run(b, args, mqtt=mqtt)
        return rc, mqtt.client, b

    def test_acknowledgements_come_from_the_calling_thread_after_landing_and_before_disconnect(self):
        msgs = [(i + 1, p) for i, p in enumerate(S.scan_messages(0))]
        rc, client, _ = self.run_bridge({"messages": msgs}, "--exit-after-files", "1")
        self.assertEqual(rc, 0)
        acks = [e for e in client.events if e[0] == "ack"]
        self.assertEqual([e[1] for e in acks], list(range(1, 61)))
        self.assertEqual({e[2] for e in acks}, {threading.get_ident()},
                         "acknowledgements must be sent by one thread, the one running run(), not paho's or the uploader's "
                         "(paho 2.1.0 ack() appends to an unguarded deque, client.py:3776)")
        self.assertNotEqual(threading.get_ident(), client.loop_thread.ident)
        kinds = [e[0] for e in client.events]
        self.assertEqual(kinds, ["ack"] * 60 + ["disconnect", "loop_stop"],
                         "the last PUBACKs must be queued before DISCONNECT, and loop_stop must come last")
        self.assertEqual(len(list((self.dir / "landing").rglob("*.ndjson"))), 1)

    def test_events_still_buffered_at_shutdown_are_landed_then_acknowledged_before_disconnect(self):
        """Half a scan never completes, so it is still in the buffer when the bridge stops: the shutdown drain must land it
        and acknowledge it while the connection is up, with DISCONNECT queued behind those PUBACKs."""
        msgs = [(i + 1, p) for i, p in enumerate(S.scan_messages(0)[:30])]
        rc, client, _ = self.run_bridge({"messages": msgs}, "--idle-exit", "0.5")
        self.assertEqual(rc, 0)
        landed = list((self.dir / "landing").rglob("*.ndjson"))
        self.assertEqual([len(f.read_text(encoding="utf-8").splitlines()) for f in landed], [30])
        kinds = [e[0] for e in client.events]
        self.assertEqual(kinds, ["ack"] * 30 + ["disconnect", "loop_stop"],
                         "events landed during the shutdown drain must be acknowledged before DISCONNECT is queued")
        self.assertEqual({e[2] for e in client.events if e[0] == "ack"}, {threading.get_ident()})

    def test_a_dead_letter_failure_stops_the_bridge_with_its_own_exit_code_and_never_acknowledges(self):
        blocker = self.dir / "a-file"
        blocker.write_text("not a directory", encoding="utf-8")
        args_extra = ("--idle-exit", "30", "--dead-letter", str(blocker / "dead.ndjson"))
        script = {"messages": [(1, b"not json at all")]}
        args = self.args(*args_extra)
        b = bridge.Bridge(bridge.build_uploader(args), dead_letter_path=args.dead_letter, sleep=lambda s: None,
                          log=lambda m: None)
        mqtt = FakeMqtt(script)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rc = bridge.run(b, args, mqtt=mqtt)
        self.assertEqual(rc, bridge.EXIT_DEAD_LETTER_FAILED)
        self.assertEqual(bridge.EXIT_DEAD_LETTER_FAILED, 4)
        self.assertEqual([e for e in mqtt.client.events if e[0] == "ack"], [], "the unsafe message was acknowledged")


if __name__ == "__main__":
    unittest.main()
