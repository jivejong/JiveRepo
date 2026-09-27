"""The MQTT publisher wrapper (doc 04) against a fake paho module: credentials only when given, a QoS 1 publish is acknowledged by its PUBACK
and by nothing else, a dropped connection forgets what was in flight, and the control topic is subscribed on every connect.
This cannot prove behaviour against a real broker; that is the desktop and Pi steps. Offline."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _probe_support  # noqa: E402,F401
from probe.publisher import MqttPublisher  # noqa: E402


class FakeInfo:
    def __init__(self, rc, mid):
        self.rc, self.mid = rc, mid


class FakeClient:
    def __init__(self, version, client_id=None, clean_session=None):
        self.args = SimpleNamespace(version=version, client_id=client_id, clean_session=clean_session)
        self.calls, self.mids = [], 0
        self.publish_rc = 0

    def username_pw_set(self, username, password):
        self.calls.append(("username_pw_set", username, password))

    def reconnect_delay_set(self, min_delay, max_delay):
        self.calls.append(("reconnect_delay_set", min_delay, max_delay))

    def connect_async(self, host, port, keepalive):
        self.calls.append(("connect_async", host, port, keepalive))

    def loop_start(self):
        self.calls.append(("loop_start",))

    def loop_stop(self):
        self.calls.append(("loop_stop",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    def subscribe(self, topic, qos):
        self.calls.append(("subscribe", topic, qos))

    def publish(self, topic, payload, qos):
        self.calls.append(("publish", topic, payload, qos))
        self.mids += 1
        return FakeInfo(self.publish_rc, self.mids)


FAKE_MQTT = SimpleNamespace(Client=FakeClient, CallbackAPIVersion=SimpleNamespace(VERSION2="v2"), MQTT_ERR_SUCCESS=0)


def publisher(**kw):
    kw.setdefault("host", "broker.lan")
    kw.setdefault("port", 1883)
    kw.setdefault("client_id", "probe-01")
    return MqttPublisher(mqtt=FAKE_MQTT, **kw)


def connect(p):
    p._on_connect(p.client, None, SimpleNamespace(session_present=False), SimpleNamespace(is_failure=False))


class SetupTests(unittest.TestCase):
    def test_a_fresh_session_the_fixed_client_id_and_paho_2(self):
        p = publisher()
        self.assertEqual((p.client.args.version, p.client.args.client_id, p.client.args.clean_session), ("v2", "probe-01", True))

    def test_credentials_are_set_only_when_given(self):
        self.assertNotIn("username_pw_set", [c[0] for c in publisher().client.calls])
        with_creds = publisher(username="probe-01", password="pw")
        self.assertIn(("username_pw_set", "probe-01", "pw"), with_creds.client.calls)

    def test_start_connects_without_blocking_and_reconnects_with_a_backoff(self):
        p = publisher(keepalive=60)
        p.start()
        names = [c[0] for c in p.client.calls]
        self.assertIn(("connect_async", "broker.lan", 1883, 60), p.client.calls)
        self.assertIn("loop_start", names)
        self.assertIn(("reconnect_delay_set", 1, 30), p.client.calls)
        self.assertNotIn("connect", names)                       # a blocking connect would stall the scan schedule


class ConnectionTests(unittest.TestCase):
    def test_it_is_not_connected_until_the_broker_accepts(self):
        p = publisher(control_topic="force/control/probe-01")
        self.assertFalse(p.connected)
        connect(p)
        self.assertTrue(p.connected)
        self.assertIsNone(p.disconnect_reason)

    def test_a_refused_connection_stays_down_and_says_why(self):
        p = publisher()
        p._on_connect(p.client, None, None, SimpleNamespace(is_failure=True, __str__=lambda self: "Not authorized"))
        self.assertFalse(p.connected)
        self.assertTrue(p.disconnect_reason.startswith("connect_refused"))

    def test_the_control_topic_is_subscribed_at_qos_1_on_every_connect(self):
        p = publisher(control_topic="force/control/probe-01")
        connect(p)
        connect(p)
        self.assertEqual([c for c in p.client.calls if c[0] == "subscribe"], [("subscribe", "force/control/probe-01", 1)] * 2)

    def test_a_control_message_reaches_the_callback(self):
        got = []
        p = publisher(control_topic="t", on_control=got.append)
        p._on_message(p.client, None, SimpleNamespace(payload=b'{"mode":"STEALTH"}'))
        self.assertEqual(got, [b'{"mode":"STEALTH"}'])

    def test_a_disconnect_marks_it_down_with_a_reason_and_forgets_what_was_in_flight(self):
        p = publisher()
        connect(p)
        p.publish("t", b"x", "EV1")
        p._on_disconnect(p.client, None, None, "unexpected")
        self.assertFalse(p.connected)
        self.assertIn("client_disconnected", p.disconnect_reason)
        p._on_publish(p.client, None, 1)                          # a late PUBACK for a forgotten publish
        self.assertEqual(p.acked(), [])


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.p = publisher()
        connect(self.p)

    def test_qos_1_and_a_key_that_comes_back_with_the_puback(self):
        self.assertTrue(self.p.publish("force/telemetry/probe-01", b"payload", "EV1"))
        self.assertEqual(self.p.client.calls[-1], ("publish", "force/telemetry/probe-01", b"payload", 1))
        self.assertEqual(self.p.acked(), [])                      # published is not acknowledged
        self.p._on_publish(self.p.client, None, 1)
        self.assertEqual(self.p.acked(), ["EV1"])
        self.assertEqual(self.p.acked(), [])                      # and it is handed over once

    def test_acknowledgements_are_matched_by_message_id_in_any_order(self):
        for key in ("A", "B", "C"):
            self.p.publish("t", b"x", key)
        for mid in (3, 1, 2):
            self.p._on_publish(self.p.client, None, mid)
        self.assertEqual(sorted(self.p.acked()), ["A", "B", "C"])

    def test_an_unknown_acknowledgement_is_ignored(self):
        self.p._on_publish(self.p.client, None, 99)
        self.assertEqual(self.p.acked(), [])

    def test_a_publish_the_client_refuses_is_reported_and_not_tracked(self):
        self.p.client.publish_rc = 4
        self.assertFalse(self.p.publish("t", b"x", "EV1"))
        self.p._on_publish(self.p.client, None, 1)
        self.assertEqual(self.p.acked(), [])

    def test_nothing_is_sent_while_it_believes_the_link_is_down(self):
        self.p._on_disconnect(self.p.client, None, None, "gone")
        before = len(self.p.client.calls)
        self.assertFalse(self.p.publish("t", b"x", "EV1"))
        self.assertEqual(len(self.p.client.calls), before)

    def test_reset_drops_the_connection_and_reconnects_without_blocking(self):
        self.p.publish("t", b"x", "EV1")
        self.p.reset()
        self.assertFalse(self.p.connected)
        self.assertEqual(self.p.disconnect_reason, "publish_unacked")
        self.assertEqual([c[0] for c in self.p.client.calls[-2:]], ["disconnect", "connect_async"])
        self.p._on_publish(self.p.client, None, 1)
        self.assertEqual(self.p.acked(), [])

    def test_close_stops_the_network_thread(self):
        self.p.close()
        self.assertEqual([c[0] for c in self.p.client.calls[-2:]], ["loop_stop", "disconnect"])


class PubackRaceTests(unittest.TestCase):
    """paho calls on_publish from its own thread; a PUBACK that lands while publish() is still recording the message id must not be lost
    (a lost confirmation leaves the row in the buffer to be sent again)."""

    def test_a_puback_handled_while_publish_is_still_running_is_not_lost(self):
        import threading

        class RacingClient(FakeClient):
            def publish(self, topic, payload, qos):
                info = super().publish(topic, payload, qos)
                racer = threading.Thread(target=lambda: owner.p._on_publish(self, None, info.mid))
                racer.start()
                racer.join(0.3)                     # the network thread is fast: it gets there before publish() has returned
                return info
        owner = SimpleNamespace(p=None)
        fake = SimpleNamespace(Client=RacingClient, CallbackAPIVersion=FAKE_MQTT.CallbackAPIVersion, MQTT_ERR_SUCCESS=0)
        p = MqttPublisher(host="h", port=1883, client_id="probe-01", mqtt=fake)
        owner.p = p
        connect(p)
        self.assertTrue(p.publish("t", b"x", "E1"))
        deadline = threading.Event()
        for _ in range(50):
            if p.acked() == ["E1"]:
                break
            deadline.wait(0.02)
        else:
            self.fail("the PUBACK that raced publish() was lost")


if __name__ == "__main__":
    unittest.main()
