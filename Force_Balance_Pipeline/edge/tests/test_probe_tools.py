"""The two operator tools, against the in-memory broker (_fake_mqtt.py) loaded with the REAL infra/mosquitto/acl:
  * edge/probe_ctl.py validates locally before sending, asks for the operator password (prompt or OPERATOR_MQTT_PASSWORD), never prints or stores it;
  * edge/mqtt_check.py passes on a correctly configured broker and FAILS on an anonymous-friendly broker, a permissive ACL or a wrong password.
Offline: the real proof is the desktop and Pi steps (doc 05)."""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _fake_mqtt as F  # noqa: E402
import mqtt_check  # noqa: E402
import probe_ctl  # noqa: E402
from forcesim.sectors import DEFAULT_SEED, load_sectors  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ACL = F.acl_from_file((ROOT / "infra" / "mosquitto" / "acl").read_text(encoding="utf-8"))
PASSWORDS = {"probe-01": "pw-probe-AAA111", "force-bridge": "pw-bridge-BBB222", "operator": "pw-operator-CCC333"}
ENVIRON = {"PROBE_MQTT_PASSWORD": PASSWORDS["probe-01"], "BRIDGE_MQTT_PASSWORD": PASSWORDS["force-bridge"],
           "OPERATOR_MQTT_PASSWORD": PASSWORDS["operator"]}
SECTORS = load_sectors(DEFAULT_SEED)
CONTROL = "force/control/probe-01"


def broker(**kw):
    return F.Broker(PASSWORDS, kw.pop("acl", ACL), **kw)


def collect():
    lines = []
    return lines, lines.append


class FakeBrokerTests(unittest.TestCase):
    """The rig must model the ACL file, or the tool tests prove nothing."""

    def test_the_acl_file_parses_to_the_documented_permissions(self):
        self.assertEqual(ACL["probe-01"], {"read": [CONTROL], "write": ["force/telemetry/probe-01"]})
        self.assertEqual(ACL["force-bridge"], {"read": ["force/telemetry/#"], "write": []})
        self.assertEqual(ACL["operator"], {"read": ["force/#"], "write": [CONTROL]})

    def test_a_denied_publish_is_acknowledged_but_not_delivered(self):
        b = broker()
        mqtt = F.module(b)
        listener = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="l")
        listener.username_pw_set("operator", PASSWORDS["operator"])
        got, acks = [], []
        listener.on_message = lambda c, u, m: got.append(m.payload)
        listener.loop_start()
        listener.subscribe("force/#")
        sender = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="s")
        sender.username_pw_set("probe-01", PASSWORDS["probe-01"])
        sender.on_publish = lambda *a: acks.append(a)
        sender.loop_start()
        sender.publish(CONTROL, b"x", qos=1)
        sender.publish("force/telemetry/probe-01", b"y", qos=1)
        self.assertEqual((len(acks), got), (2, [b"y"]))


class CtlBase(unittest.TestCase):
    def run_ctl(self, argv, b=None, environ=None, prompt=None):
        lines, out = collect()
        mqtt = F.module(b or broker())
        code = probe_ctl.main(["--host", "192.0.2.10", *argv], sectors=SECTORS, mqtt=mqtt, environ=ENVIRON if environ is None else environ,
                              prompt=prompt or (lambda text: PASSWORDS["operator"]), out=out)
        return code, "\n".join(lines)

    def probe_listener(self, b):
        mqtt = F.module(b)
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="probe-01")
        c.username_pw_set("probe-01", PASSWORDS["probe-01"])
        got = []
        c.on_message = lambda cl, u, m: got.append((m.topic, m.payload))
        c.loop_start()
        c.subscribe(CONTROL)
        return got


class ProbeCtlValidationTests(CtlBase):
    def test_an_injection_reaches_the_probe_as_the_documented_message(self):
        b = broker()
        got = self.probe_listener(b)
        code, text = self.run_ctl(["inject", "tatooine", "sith_presence", "--ramp", "3"], b=b)
        self.assertEqual(code, 0, text)
        self.assertEqual(got, [(CONTROL, b'{"inject":"spike","sector_id":"tatooine","signature":"sith_presence","ramp":3}')])

    def test_a_mode_message_carries_for_seconds(self):
        b = broker()
        got = self.probe_listener(b)
        self.assertEqual(self.run_ctl(["mode", "DISCONNECTED", "--for-seconds", "2700"], b=b)[0], 0)
        self.assertEqual(got, [(CONTROL, b'{"mode":"DISCONNECTED","for_seconds":2700}')])

    def test_a_bad_message_is_refused_locally_and_nothing_is_sent(self):
        for argv, why in ((["inject", "not_a_planet", "sith_presence"], "sector"),
                          (["inject", "tatooine", "not_a_signature"], "signature"),
                          (["inject", "tatooine", "veiled_presence"], "veiled"),
                          (["mode", "TURBO"], "mode")):
            with self.subTest(why):
                b = broker()
                got = self.probe_listener(b)
                code, text = self.run_ctl(argv, b=b)
                self.assertEqual(code, 2, text)
                self.assertIn("refused, nothing sent", text)
                self.assertEqual(got, [])

    def test_a_mode_with_no_for_seconds_is_refused_unless_indefinite_is_given(self):
        # doc 04: a mode override with no for_seconds has no expiry and suppresses --mode-schedule entirely until
        # another control message changes it, and there is no "resume the schedule" message -- found live,
        # 2026-10-06, when exactly this silently overrode the schedule for 12 minutes during a C4 run.
        b = broker()
        got = self.probe_listener(b)
        code, text = self.run_ctl(["mode", "CONNECTED"], b=b)
        self.assertEqual(code, 2, text)
        self.assertIn("refused, nothing sent", text)
        self.assertIn("--indefinite", text)
        self.assertEqual(got, [])

    def test_indefinite_sends_a_mode_message_with_no_for_seconds_key(self):
        b = broker()
        got = self.probe_listener(b)
        code, text = self.run_ctl(["mode", "CONNECTED", "--indefinite"], b=b)
        self.assertEqual(code, 0, text)
        self.assertEqual(got, [(CONTROL, b'{"mode":"CONNECTED"}')])

    def test_dry_run_prints_the_topic_and_message_and_never_connects(self):
        lines, out = collect()
        exploding = mock.Mock()
        exploding.Client.side_effect = AssertionError("dry-run must not connect")
        code = probe_ctl.main(["--host", "192.0.2.10", "--dry-run", "mode", "STEALTH", "--for-seconds", "3600"], sectors=SECTORS,
                              mqtt=exploding, environ={}, prompt=lambda t: self.fail("dry-run must not ask for a password"), out=out)
        self.assertEqual(code, 0)
        self.assertEqual(lines, [f'would publish to {CONTROL}: {{"mode":"STEALTH","for_seconds":3600}}'])


class ProbeCtlPasswordTests(CtlBase):
    def test_the_environment_password_is_used_without_a_prompt(self):
        code, _ = self.run_ctl(["mode", "STEALTH", "--indefinite"], prompt=lambda t: self.fail("must not prompt when the environment has it"))
        self.assertEqual(code, 0)

    def test_without_the_environment_it_prompts_and_names_the_user_not_the_value(self):
        asked = []
        code, text = self.run_ctl(["mode", "STEALTH", "--indefinite"], environ={}, prompt=lambda t: asked.append(t) or PASSWORDS["operator"])
        self.assertEqual(code, 0, text)
        self.assertEqual(asked, ["password for MQTT user operator: "])

    def test_a_wrong_password_is_reported_as_a_refused_login_and_is_never_printed(self):
        code, text = self.run_ctl(["mode", "STEALTH", "--indefinite"], environ={"OPERATOR_MQTT_PASSWORD": "guess-DDD444"})
        self.assertEqual(code, 1)
        self.assertIn("refused the operator login", text)
        self.assertNotIn("guess-DDD444", text)

    def test_no_password_appears_in_any_output(self):
        for argv in (["mode", "STEALTH", "--indefinite"], ["inject", "tatooine", "sith_presence"], ["inject", "nowhere", "sith_presence"]):
            _, text = self.run_ctl(argv)
            for pw in PASSWORDS.values():
                self.assertNotIn(pw, text)

    def test_an_unreachable_broker_is_reported_without_a_traceback(self):
        b = broker(reachable=False)
        code, text = self.run_ctl(["mode", "STEALTH", "--indefinite"], b=b)
        self.assertEqual(code, 1)
        self.assertIn("cannot reach the broker", text)

    def test_the_tool_never_reads_the_bridge_env_file_or_the_bridge_credentials(self):
        source = (ROOT / "edge" / "probe_ctl.py").read_text(encoding="utf-8")
        for needle in ("read_mqtt_env_file", "BRIDGE_MQTT", "open(", "PROBE_MQTT_PASSWORD"):
            self.assertNotIn(needle, source)

    def test_it_connects_as_the_operator_user(self):
        seen = []
        b = broker()
        original = F.Client.username_pw_set
        with mock.patch.object(F.Client, "username_pw_set", lambda self, u, p=None: (seen.append(u), original(self, u, p))[1]):
            self.run_ctl(["mode", "STEALTH", "--indefinite"], b=b)
        self.assertEqual(seen, ["operator"])


class MqttCheckTests(unittest.TestCase):
    def check(self, b=None, environ=None, only=None, prompt=None):
        lines, out = collect()
        failures = mqtt_check.run("192.0.2.10", 1883, only=only, mqtt=F.module(b or broker()), environ=ENVIRON if environ is None else environ,
                                  prompt=prompt, wait=0, out=out, timeout=0.2)
        return failures, "\n".join(lines)

    def test_a_correctly_configured_broker_passes_every_check(self):
        failures, text = self.check()
        self.assertEqual(failures, [], text)
        self.assertEqual(text.count("[PASS]"), 12, text)
        self.assertNotIn("[FAIL]", text)

    def test_only_probe_01_runs_the_first_three_checks_and_needs_one_password(self):
        failures, text = self.check(environ={"PROBE_MQTT_PASSWORD": PASSWORDS["probe-01"]}, only="probe-01")
        self.assertEqual(failures, [], text)
        self.assertEqual(text.count("[PASS]"), 5)
        self.assertIn("only probe-01 was checked", text)

    def test_an_anonymous_friendly_broker_fails_the_anonymous_check(self):
        failures, _ = self.check(broker(allow_anonymous=True))
        self.assertEqual(failures, ["an anonymous connection is not accepted (connected)"])

    def test_a_wrong_probe_password_fails_the_connect_check(self):
        failures, text = self.check(environ=dict(ENVIRON, PROBE_MQTT_PASSWORD="not-it-EEE555"))
        self.assertTrue(any("probe-01 connects" in f for f in failures), failures)
        self.assertNotIn("not-it-EEE555", text)

    def test_a_permissive_acl_fails_the_delivery_checks(self):
        everything = {u: {"read": ["force/#"], "write": ["force/#"]} for u in PASSWORDS}
        failures, _ = self.check(broker(acl=everything))
        self.assertEqual(sorted(f.split(" publishing")[0].split("'s")[0] for f in failures), ["force-bridge", "operator", "probe-01"])
        self.assertEqual(len(failures), 3)

    def test_a_probe_that_may_not_publish_telemetry_fails_the_first_delivery_check(self):
        acl = dict(ACL, **{"probe-01": {"read": [CONTROL], "write": []}})
        failures, _ = self.check(broker(acl=acl))
        self.assertIn("probe-01's telemetry reaches force-bridge", failures)

    def test_an_operator_who_may_not_write_control_fails_the_control_check(self):
        acl = dict(ACL, **{"operator": {"read": ["force/#"], "write": []}})
        failures, _ = self.check(broker(acl=acl))
        self.assertIn("operator's control message reaches probe-01", failures)

    def test_an_unreachable_broker_fails_and_is_reported_not_raised(self):
        failures, text = self.check(broker(reachable=False))
        self.assertTrue(any("probe-01 connects" in f for f in failures), failures)
        self.assertIn("unreachable", text)

    def test_no_password_is_ever_printed(self):
        for kwargs in ({}, {"environ": dict(ENVIRON, PROBE_MQTT_PASSWORD="not-it-EEE555")}, {"b": broker(allow_anonymous=True)}):
            _, text = self.check(**kwargs)
            for pw in list(PASSWORDS.values()) + ["not-it-EEE555"]:
                self.assertNotIn(pw, text)

    def test_a_missing_password_is_asked_for_by_user_name(self):
        asked = []
        env = {k: v for k, v in ENVIRON.items() if k != "BRIDGE_MQTT_PASSWORD"}
        failures, _ = self.check(environ=env, prompt=lambda text: asked.append(text) or PASSWORDS["force-bridge"])
        self.assertEqual(failures, [])
        self.assertEqual(asked, ["password for MQTT user force-bridge: "])

    def test_no_prompt_with_a_missing_password_fails_rather_than_hangs(self):
        failures, _ = self.check(environ={}, prompt=None)
        self.assertTrue(failures)

    def test_main_returns_zero_and_says_so_only_when_everything_passed(self):
        for b, code, phrase in ((broker(), 0, "BROKER CHECKS PASSED"), (broker(allow_anonymous=True), 1, "1 CHECK(S) FAILED")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                got = mqtt_check.main(["--host", "192.0.2.10", "--no-prompt"], mqtt=F.module(b), environ=ENVIRON, wait=0, timeout=0.2,
                                      out=lambda line: None)
            self.assertEqual(got, code)
            self.assertIn(phrase, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
