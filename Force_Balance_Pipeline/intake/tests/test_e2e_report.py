"""The decision logic of intake/e2e_report.py, offline (the e2e itself needs Docker): it may only ever touch a throwaway container, the
wrong ACLs it breaks the broker with are the committed one with exactly one thing wrong, and a denied publish is classified so that a
silent drop can never pass."""
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import _support as S
import bridge
import e2e_report as e2e
from _support import core

COMMITTED = (S.ROOT / "infra" / "mosquitto" / "acl").read_text(encoding="utf-8")


def parse(text):
    acl, user = {}, None
    for raw in text.splitlines():
        words = raw.split()
        if not words or words[0].startswith("#"):
            continue
        if words[0] == "user":
            user = words[1]
            acl[user] = []
        else:
            acl[user].append((words[1], words[2]))
    return acl


class ContainerGuardTests(unittest.TestCase):
    def test_only_a_throwaway_name_is_accepted(self):
        for name in ("fbp-e2e-mosquitto", "fbp-e2e-x", "fbp-e2e-report-2"):
            self.assertEqual(e2e.guard_container(name), name)

    def test_the_live_broker_and_anything_else_is_refused(self):
        for name in ("force-mosquitto", "fbp-e2e-", "fbp-e2e-UPPER", "fbp-e2e-a b", "fbp-e2e-x\nforce-mosquitto", "x-fbp-e2e-x", "force-bridge",
                     "", None, "fbp-e2e-" + "a" * 31):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                e2e.guard_container(name)

    def test_every_docker_action_checks_the_name_before_running_anything(self):
        calls = []
        with mock.patch.object(e2e, "docker", lambda *a, **k: calls.append(a)):
            for action in (lambda n: e2e.create_anonymous_broker(n, 1), lambda n: e2e.configure_auth_broker(n, 1, {"a": "b"}, "x", "."),
                           lambda n: e2e.set_acl(n, "x", "."), lambda n: e2e.remove_broker(n)):
                with self.subTest(), self.assertRaises(SystemExit):
                    action("force-mosquitto")
        self.assertEqual(calls, [])

    def test_the_driver_refuses_the_live_broker_before_doing_anything(self):
        with mock.patch.object(e2e, "docker", side_effect=AssertionError("docker was called")):
            with self.assertRaises(SystemExit):
                e2e.main(["--run-all", "--container", "force-mosquitto"])

    def test_the_live_broker_is_named_only_to_refuse_it(self):
        source = (S.ROOT / "intake" / "e2e_report.py").read_text(encoding="utf-8")
        named = [line for line in source.splitlines() if "force-mosquitto" in line]
        self.assertEqual(len(named), 2, named)
        self.assertTrue(any("refuses force-mosquitto" in line for line in named))
        self.assertTrue(any('name == "force-mosquitto"' in line for line in named))

    def test_nothing_is_done_without_run_all(self):
        with self.assertRaises(SystemExit):
            e2e.main([])


class WrongAclTests(unittest.TestCase):
    wrong = e2e.wrong_acls(COMMITTED)

    def test_there_are_two_and_neither_is_the_committed_acl(self):
        self.assertEqual(sorted(self.wrong), ["no_intake_user", "wrong_topic"])
        for text in self.wrong.values():
            self.assertNotEqual(text, COMMITTED)

    def test_the_committed_acl_really_grants_the_topic_the_wrong_ones_take_away(self):
        self.assertEqual(parse(COMMITTED)["web-intake"], [("write", core.TOPIC)])

    def test_without_the_intake_user_everything_else_is_untouched(self):
        broken, good = parse(self.wrong["no_intake_user"]), parse(COMMITTED)
        self.assertNotIn("web-intake", broken)
        self.assertEqual({u: r for u, r in good.items() if u != "web-intake"}, broken)

    def test_with_the_wrong_topic_only_that_line_differs(self):
        broken, good = parse(self.wrong["wrong_topic"]), parse(COMMITTED)
        self.assertEqual(broken["web-intake"], [("write", "force/telemetry/web-intakeX")])
        self.assertEqual({u: r for u, r in good.items() if u != "web-intake"}, {u: r for u, r in broken.items() if u != "web-intake"})

    def test_the_wrong_acls_end_with_a_newline_and_keep_the_comments(self):
        for text in self.wrong.values():
            self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))
            self.assertIn("# Topic permissions", text)


class ClassificationTests(unittest.TestCase):
    def test_a_denied_report_is_refused_silent_or_delivered(self):
        self.assertEqual(e2e.classify_denied_report(502, 0), "REFUSED")
        self.assertEqual(e2e.classify_denied_report(503, 0), "REFUSED")
        self.assertEqual(e2e.classify_denied_report(200, 0), "SILENT_DROP")
        self.assertEqual(e2e.classify_denied_report(200, 1), "DELIVERED")
        self.assertEqual(e2e.classify_denied_report(502, 1), "DELIVERED")


class RequestTests(unittest.TestCase):
    NOW = datetime(2026, 10, 14, 15, 0, 0, tzinfo=timezone.utc)

    def test_the_request_is_one_the_core_accepts_with_a_plausible_time(self):
        body, report_id, reporter = e2e.make_request(1, self.NOW)
        request = core.parse_request(body, core.load_sectors())
        self.assertEqual((request.report_id, request.source_id, request.description), (report_id, "web-" + reporter, e2e.DESCRIPTION))
        event_time, used_client_time = core.resolve_event_time(request.observed_at, self.NOW)
        self.assertTrue(used_client_time)
        self.assertEqual(event_time, self.NOW - timedelta(minutes=1))

    def test_the_description_exercises_newlines_a_dash_an_emoji_and_an_accent(self):
        self.assertIn("\n", e2e.DESCRIPTION)
        self.assertIn(chr(0x2014), e2e.DESCRIPTION)
        self.assertTrue(any(ord(c) > 0xFFFF for c in e2e.DESCRIPTION))
        self.assertIn(chr(0xE9), e2e.DESCRIPTION)

    def test_each_call_has_its_own_report_id(self):
        self.assertNotEqual(e2e.make_request(1, self.NOW)[1], e2e.make_request(1, self.NOW)[1])

    def test_the_expected_line_is_the_envelope_the_bridge_must_land_unchanged(self):
        body, report_id, reporter = e2e.make_request(1, self.NOW)
        line = e2e.expected_line(body, self.NOW)
        self.assertTrue(line.endswith(b"\n") and line.count(b"\n") == 1)
        envelope = json.loads(line)
        self.assertEqual(bridge.check_structure(envelope), [])
        self.assertEqual((envelope["event_id"], envelope["source_id"], envelope["event_time"]),
                         (core.event_id_for(report_id), "web-" + reporter, "2026-10-14T14:59:00.000Z"))
        self.assertEqual(envelope["payload"]["description"], e2e.DESCRIPTION)
        self.assertEqual(envelope["payload"]["relevance_score"], e2e.STUB_ANSWER["relevance_score"])

    def test_the_landed_file_pattern_is_the_bridges_web_file_name(self):
        good = "dt=2026-10-14/hh=15/web-e2e1-01KX0000000000000000000001.ndjson"
        self.assertTrue(e2e.LANDED_RE.match(good))
        for bad in ("dt=2026-10-14/hh=15/probe-abcd-01KX0000000000000000000001.ndjson",
                    "dt=2026-10-14/hh=15/probe-01-01KX0000000000000000000001.ndjson", "dt=2026-10-14/hh=15/web-E2E1-01KX0000000000000000000001.ndjson",
                    "dt=2026-10-14/hh=15/web-e2e1-01KX0000000000000000000001.ndjson.tmp-1", "web-e2e1-01KX0000000000000000000001.ndjson"):
            self.assertFalse(e2e.LANDED_RE.match(bad), bad)

    def test_the_stub_answer_is_one_the_clamp_leaves_alone(self):
        values = core.validate_inference(e2e.STUB_ANSWER)
        self.assertEqual(values.clamped, ())


class ChecksTests(unittest.TestCase):
    def test_a_failed_check_is_recorded(self):
        check = e2e.Checks()
        with mock.patch("builtins.print"):
            self.assertTrue(check(True, "fine"))
            self.assertFalse(check(False, "broken"))
        self.assertEqual(check.failures, ["broken"])


if __name__ == "__main__":
    unittest.main()
