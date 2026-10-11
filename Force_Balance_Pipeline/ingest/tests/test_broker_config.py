"""Broker persistence and the bridge session (doc 05, "Broker persistence and the bridge session"): the settings that made
the 2026-10-09/10 recovery work -- about 22 hours of data waited in the broker and arrived intact on a manual restart --
were correct but asserted nowhere. These tests parse infra/mosquitto/mosquitto.lan.conf (the file the live broker reads,
doc 05's `docker run`) and the bridge's own session settings, so a change to either fails here. Offline."""
import contextlib
import io
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402
from probe.runtime import DEFAULT_INTERVAL  # noqa: E402  edge/probe/runtime.py:43, the scan cadence in seconds

ROOT = S.ROOT
CONF = ROOT / "infra" / "mosquitto" / "mosquitto.lan.conf"
LOCAL_CONF = ROOT / "infra" / "mosquitto" / "mosquitto.conf"     # local-dev broker; read by ingest/e2e_local.py's throwaway broker
MOSQUITTO_DEFAULT_INFLIGHT = 20                                   # mosquitto.conf(5): "Defaults to 20"

# The values as committed. persistent_client_expiration must outlast the time the queue takes to fill (below), and
# max_inflight_messages must be a few scans (the bridge acknowledges after landing; see InflightWindowTests).
EXPECTED = {
    "persistence": "true",
    "persistence_location": "/mosquitto/data/",
    "autosave_interval": "10",
    "max_queued_messages": "50000",
    "persistent_client_expiration": "10d",
    "max_inflight_messages": "180",
}
# Left at Mosquitto's defaults on purpose (mosquitto.conf(5)): max_queued_bytes 0 = no byte limit; autosave_on_changes
# false = autosave_interval is a number of seconds. Setting either would change what the numbers above mean.
ABSENT = ("max_queued_bytes", "autosave_on_changes")


def parse_conf(path=None):
    """{option: value} from a mosquitto.conf; comments and blank lines skipped, a repeated option is an error."""
    values = {}
    for number, raw in enumerate(Path(path or CONF).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        if key in values:
            raise AssertionError(f"{key} is set twice (line {number})")
        values[key] = value.strip()
    return values


def duration_hours(text):
    """Hours in a Mosquitto duration such as 7d: an integer and one of h, d, w (the others are not used here)."""
    match = re.fullmatch(r"(\d+)([hdw])", text)
    if not match:
        raise AssertionError(f"unsupported duration {text!r}; extend duration_hours if the conf now uses another unit")
    return int(match.group(1)) * {"h": 1, "d": 24, "w": 168}[match.group(2)]


def events_per_hour():
    """60 events per scan (one per planet, dim_sector) x 4 scans an hour (a 900 s cadence) = 240."""
    return len(S.SECTORS) * 3600 // DEFAULT_INTERVAL


def queue_fill_hours(max_queued_messages):
    return int(max_queued_messages) / events_per_hour()


class BrokerConfTests(unittest.TestCase):
    def setUp(self):
        self.conf = parse_conf()

    def test_persistence_is_on(self):
        self.assertEqual(self.conf.get("persistence"), EXPECTED["persistence"])

    def test_persistence_location_is_the_data_volume(self):
        self.assertEqual(self.conf.get("persistence_location"), EXPECTED["persistence_location"])

    def test_autosave_interval(self):
        self.assertEqual(self.conf.get("autosave_interval"), EXPECTED["autosave_interval"])

    def test_max_queued_messages(self):
        self.assertEqual(self.conf.get("max_queued_messages"), EXPECTED["max_queued_messages"])

    def test_persistent_client_expiration(self):
        self.assertEqual(self.conf.get("persistent_client_expiration"), EXPECTED["persistent_client_expiration"])

    def test_the_byte_limit_and_save_on_changes_stay_at_their_defaults(self):
        for key in ABSENT:
            self.assertNotIn(key, self.conf, f"{key} is set; doc 05 describes the broker with it at the default")

    def test_the_conf_is_the_one_doc_05_mounts(self):
        doc = (ROOT / "docs" / "05-platform-setup.md").read_text(encoding="utf-8")
        # Only the `docker run` block counts: the prose around it quotes the same strings.
        block = re.search(r"docker run -d --name force-mosquitto.*?```", doc, re.S)
        self.assertIsNotNone(block, "doc 05 no longer has the force-mosquitto `docker run` command")
        run = block.group(0)
        self.assertIn("mosquitto -c /mosquitto/config/mosquitto.lan.conf", run)
        self.assertIn(":/mosquitto/config:ro", run)
        self.assertIn("force-mosquitto-data:/mosquitto/data", run)
        self.assertEqual(self.conf.get("persistence_location"), "/mosquitto/data/")


class InflightWindowTests(unittest.TestCase):
    """The bridge acknowledges a message only after its batch lands, so the broker must be allowed to have more messages in
    flight than the bridge waits for: Mosquitto's default window is 20 and a scan is 60 events, which would stall delivery
    until the bridge's 90 s timer (doc 05). The window is 180 (three scans); the bridge flushes at 120 pending (--max-pending)."""

    def window(self, path):
        return int(parse_conf(path).get("max_inflight_messages", MOSQUITTO_DEFAULT_INFLIGHT))

    def test_the_local_dev_conf_sets_the_window(self):
        self.assertEqual(parse_conf(LOCAL_CONF).get("max_inflight_messages"), "180")

    def test_the_pending_valve_leaves_room_for_a_scan_in_the_local_window(self):
        self.assertLessEqual(bridge.DEFAULT_MAX_PENDING + bridge.DEFAULT_SCAN_SIZE, self.window(LOCAL_CONF))

    def test_the_live_conf_sets_the_window(self):
        self.assertEqual(parse_conf(CONF).get("max_inflight_messages"), EXPECTED["max_inflight_messages"])

    def test_both_confs_set_the_same_window(self):
        self.assertEqual(parse_conf(CONF).get("max_inflight_messages"), parse_conf(LOCAL_CONF).get("max_inflight_messages"))

    def test_the_pending_valve_leaves_room_for_a_scan_in_the_live_window(self):
        self.assertLessEqual(bridge.DEFAULT_MAX_PENDING + bridge.DEFAULT_SCAN_SIZE, self.window(CONF),
                             "with the default window of 20 the broker never sends a whole scan to a bridge that holds its acks")


class QueueHeadroomTests(unittest.TestCase):
    def test_the_arrival_rate_is_240_events_an_hour(self):
        self.assertEqual(len(S.SECTORS), 60)
        self.assertEqual(DEFAULT_INTERVAL, 900)
        self.assertEqual(events_per_hour(), 240)

    def test_the_queue_fills_in_about_8_7_days(self):
        self.assertAlmostEqual(queue_fill_hours(parse_conf()["max_queued_messages"]) / 24, 8.68, places=2)

    # The expiry used to be 7 d (168 h), shorter than the queue's fill time (208.3 h): the session -- and every message
    # queued for it -- would have been discarded before the queue could overflow, which made the expiry, not
    # max_queued_messages, the binding limit (doc 05). 10d (240 h) puts the queue limit first.
    def test_session_expiry_outlasts_the_time_the_queue_takes_to_fill(self):
        conf = parse_conf()
        self.assertGreaterEqual(duration_hours(conf["persistent_client_expiration"]),
                                queue_fill_hours(conf["max_queued_messages"]),
                                "the session expires before the queue can fill: messages are lost without any queue limit being hit")

    def test_the_expiry_arithmetic_itself_is_right(self):
        # Independent of the conf: 7 d is under the fill time, 10 d is over it, for the committed 50000.
        fill = queue_fill_hours(50000)
        self.assertLess(duration_hours("7d"), fill)
        self.assertGreater(duration_hours("10d"), fill)


class FakeMqtt:
    CallbackAPIVersion = type("V", (), {"VERSION2": "v2"})

    class Client:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs


class BridgeSessionTests(unittest.TestCase):
    def test_the_client_id_is_the_fixed_force_bridge(self):
        self.assertEqual(bridge.DEFAULT_CLIENT_ID, "force-bridge")
        self.assertEqual(bridge.parse_args([]).client_id, "force-bridge")

    def test_the_session_is_persistent(self):
        client = bridge.build_client(FakeMqtt, bridge.parse_args([]))
        self.assertEqual(client.kwargs["client_id"], "force-bridge")
        self.assertIs(client.kwargs["clean_session"], False)

    def test_acknowledgement_is_manual(self):
        client = bridge.build_client(FakeMqtt, bridge.parse_args([]))
        self.assertIs(client.kwargs.get("manual_ack"), True, "paho would acknowledge on return from on_message, before the file lands")

    def test_the_subscription_is_qos_1(self):
        subscribed = []

        class Client:
            def subscribe(self, topic, qos=0):
                subscribed.append((topic, qos))
        flags = type("F", (), {"session_present": True})()
        reason = type("R", (), {"is_failure": False, "__str__": lambda s: "Success"})()
        with contextlib.redirect_stdout(io.StringIO()):
            bridge.make_on_connect(bridge.DEFAULT_TOPIC)(Client(), None, flags, reason, None)
        self.assertEqual(subscribed, [("force/telemetry/#", 1)])


if __name__ == "__main__":
    unittest.main()
