"""The broker publisher: QoS 1, MQTT 5, return only after the PUBACK, never queue for later, treat a failure reason code as a refusal
(so a wrong ACL is not silent), and the credentials file. A fake paho stands in for the network."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import _support as S
import intake_mqtt as mq
from _support import core

class Reason:
    def __init__(self, failure):
        self.is_failure = failure

    def __str__(self):
        return "Not authorized" if self.is_failure else "Success"


class FakeInfo:
    def __init__(self, rc, mid):
        self.rc, self.mid = rc, mid


class FakeClient:
    """Just enough of paho 2.x. `ack` plays the loop thread delivering a PUBACK."""

    def __init__(self, version, client_id=None, protocol=None):
        self.version, self.client_id, self.protocol = version, client_id, protocol
        self.calls, self.published, self.connected = [], [], False
        self.publish_rc, self.ack_in_publish, self.owner = 0, None, None
        self.cond_held_during_publish = None
        self.next_mid = 0

    def username_pw_set(self, username, password=None):
        self.calls.append(("username_pw_set", username, password))

    def reconnect_delay_set(self, min_delay, max_delay):
        self.calls.append(("reconnect_delay_set", min_delay, max_delay))

    def connect_async(self, host, port, keepalive=60):
        self.calls.append(("connect_async", host, port, keepalive))

    def loop_start(self):
        self.calls.append(("loop_start",))

    def loop_stop(self):
        self.calls.append(("loop_stop",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload, qos=0):
        if self.owner is not None:
            self.cond_held_during_publish = self.owner._cond._is_owned()
        self.next_mid += 1
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "mid": self.next_mid})
        if self.ack_in_publish is not None and self.publish_rc == 0:
            self.on_publish(self, None, self.next_mid, self.ack_in_publish, None)
        return FakeInfo(self.publish_rc, self.next_mid)

    def ack(self, mid, failure=False):
        self.on_publish(self, None, mid, Reason(failure), None)


def fake_paho():
    return SimpleNamespace(Client=FakeClient, CallbackAPIVersion=SimpleNamespace(VERSION2="v2"), MQTTv5=5, MQTT_ERR_SUCCESS=0)


def make(**options):
    logs = []
    publisher = mq.MqttPublisher("127.0.0.1", 18883, options.pop("username", "web-intake"), options.pop("password", "pw-NOT-A-REAL-ONE"),
                                 mqtt=fake_paho(), log=logs.append)
    publisher._client.owner = publisher
    publisher._client.connected = options.pop("connected", True)
    return publisher, publisher._client, logs


class PublishTests(unittest.TestCase):
    def test_it_publishes_qos_1_to_the_given_topic_and_returns_after_the_puback(self):
        publisher, client, _ = make()
        client.ack_in_publish = Reason(False)
        publisher.publish("force/telemetry/web-intake", b"payload", timeout=1)
        self.assertEqual(client.published, [{"topic": "force/telemetry/web-intake", "payload": b"payload", "qos": 1, "mid": 1}])

    def test_it_waits_for_a_puback_that_arrives_later(self):
        publisher, client, _ = make()
        done = []
        thread = threading.Thread(target=lambda: done.append(publisher.publish("t", b"x", timeout=5)))
        thread.start()
        deadline = time.time() + 5
        while not client.published and time.time() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)
        self.assertEqual(done, [])                                            # still waiting: no PUBACK yet
        client.ack(client.published[0]["mid"])
        thread.join(5)
        self.assertEqual(done, [None])

    def test_no_puback_in_time_raises_timeout_and_a_late_one_is_forgotten(self):
        publisher, client, _ = make()
        with self.assertRaises(mq.PublishError) as caught:
            publisher.publish("t", b"x", timeout=0.05)
        self.assertEqual((caught.exception.kind, caught.exception.retry_after_s), ("timeout", 15))
        client.ack(1)                                                         # the PUBACK arrives after the caller gave up
        self.assertEqual((publisher._acks, publisher._abandoned), ({}, set()))

    def test_a_puback_that_arrives_before_the_wait_starts_is_not_lost(self):
        publisher, client, _ = make()
        client.ack_in_publish = Reason(False)
        publisher.publish("t", b"x", timeout=0.5)
        self.assertEqual(publisher._acks, {})

    def test_each_publish_waits_for_its_own_acknowledgement(self):
        publisher, client, _ = make()
        results = {}

        def go(name):
            try:
                publisher.publish("t", name.encode(), timeout=0.6)
                results[name] = "acked"
            except mq.PublishError as e:
                results[name] = e.kind
        threads = [threading.Thread(target=go, args=(n,)) for n in ("a", "b")]
        for t in threads:
            t.start()
        deadline = time.time() + 5
        while len(client.published) < 2 and time.time() < deadline:
            time.sleep(0.005)
        mids = {p["payload"]: p["mid"] for p in client.published}
        client.ack(mids[b"b"])                                                # only b is acknowledged
        for t in threads:
            t.join(5)
        self.assertEqual(results, {"a": "timeout", "b": "acked"})

    def test_a_failure_reason_code_is_a_refusal_so_a_wrong_acl_is_not_silent(self):
        publisher, client, _ = make()
        client.ack_in_publish = Reason(True)
        with self.assertRaises(mq.PublishError) as caught:
            publisher.publish("t", b"x", timeout=1)
        self.assertEqual((caught.exception.kind, caught.exception.retry_after_s), ("refused", 300))
        self.assertIn("Not authorized", str(caught.exception))

    def test_when_not_connected_it_refuses_at_once_and_queues_nothing(self):
        publisher, client, _ = make(connected=False)
        self.assertFalse(publisher.ready())
        with self.assertRaises(mq.PublishError) as caught:
            publisher.publish("t", b"x", timeout=5)
        self.assertEqual(caught.exception.kind, "unavailable")
        self.assertEqual(client.published, [])

    def test_a_client_that_will_not_take_the_message_is_unavailable(self):
        publisher, client, _ = make()
        client.publish_rc = 4
        with self.assertRaises(mq.PublishError) as caught:
            publisher.publish("t", b"x", timeout=1)
        self.assertEqual(caught.exception.kind, "unavailable")

    def test_the_lock_is_not_held_while_the_client_publishes_so_paho_cannot_deadlock_against_it(self):
        publisher, client, _ = make()
        client.ack_in_publish = Reason(False)
        publisher.publish("t", b"x", timeout=1)
        self.assertIs(client.cond_held_during_publish, False)


class SetupTests(unittest.TestCase):
    def test_the_client_is_mqtt_5_with_a_fixed_id_credentials_and_backoff(self):
        publisher, client, _ = make()
        self.assertEqual((client.version, client.client_id, client.protocol), ("v2", "force-intake", 5))
        self.assertIn(("username_pw_set", "web-intake", "pw-NOT-A-REAL-ONE"), client.calls)
        self.assertIn(("reconnect_delay_set", 1, 30), client.calls)
        self.assertEqual(mq.CLIENT_ID, "force-intake")

    def test_start_connects_in_the_background_and_close_disconnects_then_stops_the_loop(self):
        publisher, client, _ = make()
        publisher.start()
        self.assertEqual([c for c in client.calls if c[0] in ("connect_async", "loop_start")], [("connect_async", "127.0.0.1", 18883, 30), ("loop_start",)])
        publisher.close()
        self.assertEqual([c[0] for c in client.calls][-2:], ["disconnect", "loop_stop"])

    def test_no_username_means_an_anonymous_client(self):
        publisher, client, _ = make(username=None, password=None)
        self.assertNotIn("username_pw_set", [c[0] for c in client.calls])

    def test_the_password_is_never_logged(self):
        publisher, client, logs = make()
        publisher._on_connect(client, None, None, Reason(True))
        publisher._on_connect(client, None, None, Reason(False))
        publisher._on_disconnect(client, None, None, Reason(True))
        publisher._on_connect_fail(client, None)
        self.assertTrue(logs)
        self.assertFalse([l for l in logs if "pw-NOT-A-REAL-ONE" in l or "web-intake" in l])

    def test_connection_failures_are_logged_at_most_once_a_minute(self):
        logs, now = [], [100.0]
        publisher = mq.MqttPublisher("h", 1, None, None, mqtt=fake_paho(), clock=lambda: now[0], log=logs.append)
        for _ in range(5):
            publisher._on_connect_fail(None, None)
        self.assertEqual(len(logs), 1)
        now[0] += 61
        publisher._on_connect_fail(None, None)
        self.assertEqual(len(logs), 2)
        self.assertIn("1 failed attempt(s)", logs[0])
        self.assertIn("5 failed attempt(s)", logs[1])


class CredentialsTests(unittest.TestCase):
    def write(self, tmp, text):
        path = Path(tmp) / ".env.intake.mqtt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_two_keys_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "# c\nINTAKE_MQTT_USERNAME=web-intake\nINTAKE_MQTT_PASSWORD=\"pw-1\"\n")
            self.assertEqual(mq.mqtt_credentials({}, path), ("web-intake", "pw-1"))

    def test_any_other_key_is_refused_without_echoing_its_value(self):
        for line in ("GEMINI_API_KEY=SECRET-VALUE", "DATABRICKS_TOKEN=SECRET-VALUE", "BRIDGE_MQTT_PASSWORD=SECRET-VALUE",
                     "OPERATOR_MQTT_PASSWORD=SECRET-VALUE", "INFERENCE_MODEL=SECRET-VALUE"):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(core.ConfigError) as caught:
                    mq.read_mqtt_env_file(self.write(tmp, "INTAKE_MQTT_USERNAME=web-intake\n" + line + "\n"))
                self.assertIn("line 2", str(caught.exception))
                self.assertNotIn("SECRET-VALUE", str(caught.exception))

    def test_a_malformed_or_repeated_line_is_refused(self):
        for text in ("INTAKE_MQTT_USERNAME\n", "INTAKE_MQTT_USERNAME=a\nINTAKE_MQTT_USERNAME=b\n"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(core.ConfigError):
                    mq.read_mqtt_env_file(self.write(tmp, text))

    def test_the_environment_wins_over_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "INTAKE_MQTT_USERNAME=from-file\nINTAKE_MQTT_PASSWORD=pw-file\n")
            self.assertEqual(mq.mqtt_credentials({"INTAKE_MQTT_USERNAME": "from-env", "INTAKE_MQTT_PASSWORD": "pw-env"}, path), ("from-env", "pw-env"))
            self.assertEqual(mq.mqtt_credentials({"INTAKE_MQTT_PASSWORD": "pw-env"}, path), ("from-file", "pw-env"))

    def test_a_username_without_a_password_or_the_reverse_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for environ in ({"INTAKE_MQTT_USERNAME": "web-intake"}, {"INTAKE_MQTT_PASSWORD": "pw"}):
                with self.assertRaises(core.ConfigError) as caught:
                    mq.mqtt_credentials(environ, Path(tmp) / "absent")
                self.assertNotIn("pw", str(caught.exception).replace("password", ""))

    def test_no_credentials_at_all_means_an_anonymous_broker(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(mq.mqtt_credentials({}, Path(tmp) / "absent"), (None, None))

    def test_the_template_exists_holds_only_the_two_keys_and_no_password_and_the_real_file_is_ignored(self):
        template = S.ROOT / ".env.intake.mqtt.example"
        values = mq.read_mqtt_env_file(template)
        self.assertEqual(set(values), set(mq.MQTT_ENV_KEYS))
        self.assertEqual(values["INTAKE_MQTT_USERNAME"], "web-intake")
        self.assertEqual(values["INTAKE_MQTT_PASSWORD"], "")
        self.assertIn(".env.intake.mqtt", (S.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertEqual(mq.MQTT_ENV_FILE_DEFAULT, S.ROOT / ".env.intake.mqtt")


if __name__ == "__main__":
    unittest.main()
