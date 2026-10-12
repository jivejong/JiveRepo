"""intake/acl_check.py against a fake broker that applies the COMMITTED ACL the way Mosquitto does under MQTT 5: a publish outside the
user's write list is answered with a failure reason code."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import _support as S
import acl_check
import intake_mqtt as mq
from _support import KEY
from test_mqtt_publisher import FakeClient, Reason

sys.path.insert(0, str(S.ROOT / "edge" / "tests"))
from _fake_mqtt import acl_from_file, matches  # noqa: E402  (the offline broker model the probe's tests use)

COMMITTED = acl_from_file((S.ROOT / "infra" / "mosquitto" / "acl").read_text(encoding="utf-8"))


class AclClient(FakeClient):
    """Connects if the password is right; acknowledges every publish, with a failure code when the ACL does not allow it."""
    broker_users = {"web-intake": "pw-NOT-A-REAL-ONE"}
    acl = COMMITTED
    log = []

    def username_pw_set(self, username, password=None):
        super().username_pw_set(username, password)
        self.user, self.pw = username, password

    def loop_start(self):
        super().loop_start()
        self.connected = self.broker_users.get(self.user) == self.pw

    def publish(self, topic, payload, qos=0):
        info = super().publish(topic, payload, qos)
        allowed = any(matches(p, topic) for p in self.acl.get(self.user, {}).get("write", []))
        AclClient.log.append((topic, allowed))
        self.on_publish(self, None, info.mid, Reason(not allowed), None)
        return info


def factory(acl=None, users=None):
    attrs = {}
    if acl is not None:
        attrs["acl"] = acl
    if users is not None:
        attrs["broker_users"] = users
    client_cls = type("C", (AclClient,), attrs)
    module = SimpleNamespace(Client=client_cls, CallbackAPIVersion=SimpleNamespace(VERSION2="v2"), MQTTv5=5, MQTT_ERR_SUCCESS=0)

    def make(host, port, username, password, log=print):
        return mq.MqttPublisher(host, port, username, password, mqtt=module, log=log)
    return make


def run(*argv, acl=None, users=None, environ=None):
    AclClient.log = []
    out = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp:
        code = acl_check.main(list(argv) + ["--mqtt-env-file", str(Path(tmp) / "absent"), "--connect-timeout", "0.3"],
                              publisher_factory=factory(acl, users), out=out, sleep=lambda s: None,
                              environ=environ if environ is not None else {"INTAKE_MQTT_USERNAME": "web-intake", "INTAKE_MQTT_PASSWORD": "pw-NOT-A-REAL-ONE"})
    return code, out.getvalue(), list(AclClient.log)


class AclCheckTests(unittest.TestCase):
    def test_against_the_committed_acl_the_negative_checks_pass_and_nothing_is_published_to_the_allowed_topic(self):
        code, output, log = run()
        self.assertEqual(code, 0, output)
        self.assertEqual(log, [("force/acl-check/denied", False), ("force/telemetry/acl-check", False)])
        self.assertIn("ALL CHECKS PASSED", output)
        self.assertIn("positive check skipped", output)

    def test_positive_publishes_one_marker_to_the_allowed_topic(self):
        code, output, log = run("--positive")
        self.assertEqual(code, 0, output)
        self.assertEqual(log[-1], ("force/telemetry/web-intake", True))
        self.assertEqual(len(log), 3)

    def test_it_never_publishes_to_the_probes_topics_or_the_control_topic(self):
        _, _, log = run("--positive")
        for topic, _ in log:
            self.assertNotIn("probe-01", topic)
            self.assertFalse(topic.startswith("force/control"))

    def test_an_acl_that_is_too_wide_fails_the_check(self):
        wide = {"web-intake": {"read": [], "write": ["force/telemetry/#"]}}
        code, output, log = run(acl=wide)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] a publish to force/telemetry/acl-check was ACCEPTED", output)
        self.assertIn("[PASS] a publish to force/acl-check/denied is refused", output)

    def test_an_acl_that_forgot_the_user_fails_the_positive_check(self):
        code, output, _ = run("--positive", acl={})
        self.assertEqual(code, 1)
        self.assertIn("the ACL does not allow the one topic it must", output)

    def test_a_denied_publish_that_times_out_is_not_a_refusal_and_fails(self):
        class Silent:
            def __init__(self, *a, **k):
                pass

            def start(self):
                pass

            def close(self):
                pass

            def ready(self):
                return True

            def publish(self, topic, payload, timeout=10):
                raise mq.PublishError("timeout", "no PUBACK")
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            code = acl_check.main(["--mqtt-env-file", str(Path(tmp) / "absent")], publisher_factory=Silent, out=out, sleep=lambda s: None,
                                  environ={"INTAKE_MQTT_USERNAME": "web-intake", "INTAKE_MQTT_PASSWORD": "pw-NOT-A-REAL-ONE"})
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] a publish to force/acl-check/denied is refused by the broker (timeout)", out.getvalue())

    def test_a_wrong_password_fails_at_the_login(self):
        code, output, log = run(users={"web-intake": "another"})
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] web-intake logs in", output)
        self.assertEqual(log, [])

    def test_the_password_is_never_printed(self):
        for argv in ((), ("--positive",)):
            _, output, _ = run(*argv)
            self.assertNotIn("pw-NOT-A-REAL-ONE", output)

    def test_it_only_runs_as_the_web_intake_user_and_needs_credentials(self):
        for environ in ({"INTAKE_MQTT_USERNAME": "force-bridge", "INTAKE_MQTT_PASSWORD": "x"}, {}, {"INTAKE_MQTT_USERNAME": "web-intake"}):
            with self.subTest(environ=environ):
                code, output, log = run(environ=environ)
                self.assertEqual((code, log), (2, []))


if __name__ == "__main__":
    unittest.main()
