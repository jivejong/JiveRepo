"""The broker side of transport C, asserted in the repo: the committed ACL gives the web-intake user write access to exactly one topic
and nothing else, that topic is the one the intake process publishes to, the bridge's subscription already covers it, and the docs say
so. (The live broker only changes when the volume copy and the restart in doc 05 are run; nothing here touches it.)"""
import re
import unittest

import _support as S
import bridge
import intake_mqtt as mq
from _support import core

ACL = (S.ROOT / "infra" / "mosquitto" / "acl").read_text(encoding="utf-8")
DOC_04 = re.sub(r"\s+", " ", (S.ROOT / "docs" / "04-edge-simulators.md").read_text(encoding="utf-8"))
DOC_05 = re.sub(r"\s+", " ", (S.ROOT / "docs" / "05-platform-setup.md").read_text(encoding="utf-8"))


def parsed():
    acl, user = {}, None
    for raw in ACL.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if words[0] == "user":
            user = words[1]
            acl[user] = []
        else:
            assert words[0] == "topic", line
            acl[user].append((words[1], words[2]))
    return acl


def covers(pattern, topic):
    """MQTT topic filter matching, as far as these filters need: exact levels, and a trailing # for the rest."""
    p, t = pattern.split("/"), topic.split("/")
    for i, level in enumerate(p):
        if level == "#":
            return True
        if i >= len(t) or level != t[i]:
            return False
    return len(p) == len(t)


class AclTests(unittest.TestCase):
    def test_web_intake_may_write_exactly_one_topic_and_read_nothing(self):
        self.assertEqual(parsed()["web-intake"], [("write", "force/telemetry/web-intake")])

    def test_that_topic_is_the_one_the_intake_process_publishes_to(self):
        self.assertEqual(core.TOPIC, "force/telemetry/web-intake")
        self.assertIn(("write", core.TOPIC), parsed()["web-intake"])

    def test_the_bridges_subscription_and_acl_already_cover_it(self):
        self.assertTrue(covers(bridge.DEFAULT_TOPIC, core.TOPIC))
        self.assertTrue(any(kind == "read" and covers(pattern, core.TOPIC) for kind, pattern in parsed()["force-bridge"]))

    def test_the_intake_user_cannot_touch_the_probes_topics(self):
        for kind, pattern in parsed()["web-intake"]:
            self.assertFalse(covers(pattern, "force/telemetry/probe-01"), pattern)
            self.assertFalse(covers(pattern, "force/control/probe-01"), pattern)
            self.assertNotIn("#", pattern)
            self.assertNotIn("+", pattern)

    def test_no_other_user_gained_anything(self):
        acl = parsed()
        self.assertEqual(acl["probe-01"], [("write", "force/telemetry/probe-01"), ("read", "force/control/probe-01")])
        self.assertEqual(acl["force-bridge"], [("read", "force/telemetry/#")])
        self.assertEqual(acl["operator"], [("write", "force/control/probe-01"), ("read", "force/#")])
        self.assertEqual(sorted(acl), ["force-bridge", "operator", "probe-01", "web-intake"])

    def test_only_web_intake_writes_the_report_topic(self):
        writers = [u for u, rules in parsed().items() if any(k == "write" and covers(p, core.TOPIC) for k, p in rules)]
        self.assertEqual(writers, ["web-intake"])

    def test_the_login_template_names_the_same_user(self):
        self.assertEqual(mq.read_mqtt_env_file(S.ROOT / ".env.intake.mqtt.example")["INTAKE_MQTT_USERNAME"], "web-intake")


class DocTests(unittest.TestCase):
    def test_doc_04_states_the_permission(self):
        self.assertIn("`web-intake` (the report intake process, Phase 5) writes `force/telemetry/web-intake` and reads nothing", DOC_04)

    def test_doc_05_records_the_password_its_home_and_the_restart_rule(self):
        self.assertIn("mosquitto_passwd /work/passwd web-intake", DOC_05)
        self.assertIn("`.env.intake.mqtt` (`INTAKE_MQTT_PASSWORD`", DOC_05)
        self.assertIn("Adding `web-intake` to a running broker", DOC_05)
        self.assertIn("re-run the volume copy command", DOC_05)
        self.assertIn("`docker restart force-mosquitto`", DOC_05)
        self.assertIn("outside the minutes :58 to :05 of each quarter hour", DOC_05)
        self.assertIn("Not authorized", DOC_05)


if __name__ == "__main__":
    unittest.main()
