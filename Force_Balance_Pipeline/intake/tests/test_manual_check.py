"""The stub model and the guards that keep the manual browser check (and any stub run) away from the live broker, offline. Also that the
written checklist agrees with the stub, the page's wording and the setup it describes."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support as S
import e2e_report as e2e
import intake_mqtt
import intake_server as srv
import manual_check as manual
import stub_model as stub
from _support import core

SECTOR = core.load_sectors()["coruscant"]
CHECKLIST = (S.ROOT / "intake" / "MANUAL-CHECKLIST.md").read_text(encoding="utf-8")
FLAT = re.sub(r"\s+", " ", CHECKLIST)               # the checklist's lines are wrapped; phrases are compared unwrapped
WEB = S.ROOT / "intake" / "web"


def request_for(text):
    return json.dumps(core.build_inference_request(SECTOR, text)).encode("utf-8")


def raised(testcase, kind, work):
    """The exception `work()` raises, which must be a `kind`: an exception of another kind, or none, is an assertion FAIL."""
    try:
        work()
    except Exception as e:
        testcase.assertIsInstance(e, kind)
        return e
    testcase.fail(f"{kind.__name__} was not raised")


def ask(http, text):
    return http("POST", "https://example.invalid/model:generateContent", headers={}, data=request_for(text), timeout=5)


class StubModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = Path(self.tmp.name) / "calls.txt"

    def call_count(self):
        return len(self.calls.read_text(encoding="utf-8").splitlines()) if self.calls.exists() else 0

    def run_inference(self, text, **kwargs):
        return core.run_inference(SECTOR, text, api_key="k", model="m", http=stub.make_http(self.calls, varied=True, **kwargs), sleep=lambda _: None)

    def test_fixed_mode_gives_the_same_answer_whatever_the_text_says(self):
        http = stub.make_http(self.calls)
        for text in ("Saw Darth Vader", "the weather", "[stub:503]", "[stub:blocked]", "[stub:slow]", "[stub:html]"):
            self.assertEqual(ask(http, text), (200, stub.stub_answer_body()), text)
        self.assertEqual(stub.STUB_ANSWER["relevance_score"], 0.95)
        self.assertIs(e2e.STUB_ANSWER, stub.STUB_ANSWER)

    def test_every_call_is_counted_by_url_without_the_query_or_the_key(self):
        http = stub.make_http(self.calls)
        http("POST", "https://example.invalid/m:generateContent?key=SECRET", data=request_for("x"))
        http("POST", "https://example.invalid/m:generateContent", data=request_for("y"))
        lines = self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []
        self.assertEqual(lines, ["https://example.invalid/m:generateContent"] * 2)

    def test_the_text_is_recovered_from_the_request_the_core_builds(self):
        for text in ("plain", "quotes \" and \\ and \n newlines", "caf" + chr(0xE9) + " " + chr(0x2014) + " " + chr(0x1F311), "[stub:503] inside", ""):
            self.assertEqual(stub.description_of(request_for(text)), text)
        for junk in (b"", b"not json", b"{}", b'{"contents": []}', b'{"contents":[{"parts":[{"text":"not json"}]}]}', None):
            self.assertEqual(stub.description_of(junk), "")

    def test_varied_answers_follow_the_text_and_the_clamp_leaves_them_alone(self):
        cases = {"Saw Darth Vader": 0.95, "a SITH lord": 0.95, "the Emperor was here": 0.95, "A Jedi with a lightsaber": 0.7, "kyber crystals": 0.7,
                 "The weather was mild": 0.04, "": 0.04}
        for text, relevance in cases.items():
            with self.subTest(text=text):
                values = self.run_inference(text)
                self.assertEqual((values.relevance_score, values.clamped), (relevance, ()))
        self.assertGreater(self.run_inference("Vader").dark_side_activity, self.run_inference("Jedi").dark_side_activity)
        self.assertGreater(self.run_inference("Jedi").dark_side_activity, self.run_inference("weather").dark_side_activity)

    def test_the_instructions_to_the_model_do_not_trigger_the_stub(self):
        for trigger in stub.TRIGGERS:
            self.assertNotIn(trigger, core.SYSTEM_PROMPT)
        self.assertEqual(self.run_inference("hello").relevance_score, 0.04)

    def test_503_trigger_is_a_retryable_inference_failure_tried_twice(self):
        error = raised(self, core.InferenceError, lambda: self.run_inference("down [stub:503]"))
        self.assertEqual((error.kind, error.retryable), ("server_error", True))
        self.assertEqual(self.call_count(), core.MAX_ATTEMPTS)

    def test_bad_trigger_is_a_malformed_answer(self):
        error = raised(self, core.InferenceError, lambda: self.run_inference("garbage [stub:bad]"))
        self.assertEqual(error.kind, "bad_output")

    def test_blocked_trigger_is_the_models_refusal_422_and_not_retried(self):
        error = raised(self, core.RequestError, lambda: self.run_inference("refuse [stub:blocked]"))
        self.assertEqual((error.status, error.code), (422, "blocked_by_model"))
        self.assertEqual(self.call_count(), 1)

    def test_slow_trigger_waits_65_seconds_and_then_answers_normally(self):
        waits = []
        values = self.run_inference("slow one [stub:slow]", sleep=waits.append)
        self.assertEqual(waits, [65])
        self.assertEqual(stub.SLOW_SECONDS, 65)
        self.assertEqual(values.relevance_score, 0.04)

    def test_html_trigger_returns_a_rationale_with_markup_intact(self):
        values = self.run_inference("markup [stub:html]")
        self.assertEqual(values.inference_rationale, stub.HTML_RATIONALE)
        self.assertIn("<img", values.inference_rationale)
        self.assertLess(len(stub.HTML_RATIONALE), core.MAX_RATIONALE_CHARS)

    def test_the_triggers_are_the_five_the_checklist_uses(self):
        self.assertEqual(set(stub.TRIGGERS), {"[stub:503]", "[stub:bad]", "[stub:blocked]", "[stub:slow]", "[stub:html]"})

    def test_the_stub_makes_no_network_call_of_its_own(self):
        source = (S.ROOT / "intake" / "stub_model.py").read_text(encoding="utf-8")
        self.assertEqual(sorted(re.findall(r"^\s*(?:import|from)\s+(\S+)", source, re.M)), ["json", "time"])


class StubRunGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def argv(self, **overrides):
        """A clean set of intake arguments; keyword `name=None` leaves that flag out (so the real default applies)."""
        flags = {"--http-port": "18081", "--mqtt-host": "127.0.0.1", "--mqtt-port": "18885", "--env-file": str(self.dir / ".env.intake"),
                 "--mqtt-env-file": str(self.dir / ".env.intake.mqtt"), "--report-log": str(self.dir / "reports.ndjson"),
                 "--log-file": str(self.dir / "intake.log"), "--lock-file": str(self.dir / "intake.lock")}
        for key, value in overrides.items():
            flags["--" + key.replace("_", "-")] = value
        return [part for flag, value in flags.items() if value is not None for part in (flag, value)]

    def parsed(self, **overrides):
        return srv.parse_args(self.argv(**overrides))

    def test_a_clean_throwaway_setup_is_allowed(self):
        self.assertIsNone(e2e.guard_stub_run(self.parsed()))
        for host in ("127.0.0.1", "localhost", "LOCALHOST", "::1"):
            self.assertIsNone(e2e.guard_stub_run(self.parsed(mqtt_host=host)))

    def test_the_live_broker_port_is_refused_whether_named_or_defaulted(self):
        with self.assertRaisesRegex(SystemExit, "live broker"):
            e2e.guard_stub_run(self.parsed(mqtt_port="1883"))
        with self.assertRaisesRegex(SystemExit, "live broker"):
            e2e.guard_stub_run(self.parsed(mqtt_port=None))

    def test_a_broker_that_is_not_on_this_machine_is_refused(self):
        for host in ("203.0.113.10", "broker.example", "0.0.0.0", "127.0.0.2", "localhost.evil.example", ""):
            with self.subTest(host=host), self.assertRaisesRegex(SystemExit, "loopback"):
                e2e.guard_stub_run(self.parsed(mqtt_host=host))

    def test_the_real_intakes_port_and_files_are_refused_whether_named_or_defaulted(self):
        cases = [("http_port", srv.DEFAULT_HTTP_PORT), ("env_file", core.DEFAULT_ENV_FILE), ("mqtt_env_file", intake_mqtt.MQTT_ENV_FILE_DEFAULT),
                 ("report_log", srv.DEFAULT_REPORT_LOG), ("log_file", srv.DEFAULT_LOG_PATH), ("lock_file", srv.DEFAULT_LOCK_PATH)]
        for name, default in cases:
            for given in (str(default), None):
                with self.subTest(flag=name, given=given), self.assertRaises(SystemExit):
                    e2e.guard_stub_run(self.parsed(**{name: given if given is None else str(given)}))

    def test_a_path_spelled_differently_is_still_the_real_file(self):
        roundabout = str(srv.DEFAULT_REPORT_LOG.parent / ".." / srv.DEFAULT_REPORT_LOG.parent.name / srv.DEFAULT_REPORT_LOG.name)
        with self.assertRaises(SystemExit):
            e2e.guard_stub_run(self.parsed(report_log=roundabout))

    def test_every_problem_is_named_in_one_refusal(self):
        with self.assertRaises(SystemExit) as caught:
            e2e.guard_stub_run(self.parsed(mqtt_host="203.0.113.10", mqtt_port="1883", http_port=str(srv.DEFAULT_HTTP_PORT)))
        text = str(caught.exception)
        for part in ("loopback", "live broker", "default"):
            self.assertIn(part, text)

    def test_serve_stub_refuses_before_the_intake_process_starts(self):
        calls = self.dir / "calls.txt"
        with mock.patch.object(srv, "main", side_effect=AssertionError("the intake process was started")):
            with self.assertRaises(SystemExit):
                e2e.serve_stub(str(calls), self.argv(mqtt_port="1883"))
            with self.assertRaises(SystemExit):
                e2e.main(["--serve-stub", str(calls), "--", *self.argv(mqtt_host="203.0.113.5")])
        self.assertFalse(calls.exists())

    def test_serve_stub_starts_the_intake_with_the_stub_model_when_the_arguments_are_clean(self):
        with mock.patch.object(srv, "main", return_value=0) as fake:
            self.assertEqual(e2e.serve_stub(str(self.dir / "c.txt"), self.argv(), varied=True), 0)
        (argv,), kwargs = fake.call_args
        self.assertEqual(argv, self.argv())
        status, _ = kwargs["http"]("POST", "https://example.invalid/m", data=request_for("down [stub:503]"))
        self.assertEqual(status, 503)                           # varied: the trigger works
        with mock.patch.object(srv, "main", return_value=0) as fake:
            e2e.serve_stub(str(self.dir / "c.txt"), self.argv())
        self.assertEqual(fake.call_args.kwargs["http"]("POST", "https://example.invalid/m", data=request_for("down [stub:503]"))[0], 200)

    def test_main_passes_the_varied_flag_through(self):
        with mock.patch.object(e2e, "serve_stub", return_value=0) as fake:
            e2e.main(["--serve-stub", "calls.txt", "--stub-varied", "--", "--http-port", "1"])
            e2e.main(["--serve-stub", "calls.txt", "--", "--http-port", "1"])
        self.assertEqual([c.kwargs["varied"] for c in fake.call_args_list], [True, False])
        self.assertEqual(fake.call_args.args[1], ["--http-port", "1"])

    def test_the_stub_flag_is_the_only_way_to_a_stub_model(self):
        """intake_server never imports the stub, and the e2e's only entry to it is --serve-stub."""
        self.assertNotIn("stub_model", (S.ROOT / "intake" / "intake_server.py").read_text(encoding="utf-8"))
        self.assertNotIn("stub_model", (S.ROOT / "intake" / "intake_core.py").read_text(encoding="utf-8"))


class RunningIntakeCommandTests(unittest.TestCase):
    def launch(self, **kwargs):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work, mock.patch.object(e2e.subprocess, "Popen") as popen:
            popen.return_value = mock.Mock(stdout=iter([]))
            e2e.RunningIntake(work, 18885, "pw", **kwargs)
            files = sorted(p.name for p in Path(work).iterdir())
        return popen.call_args.args[0], files

    def test_the_varied_flag_goes_before_the_separator_and_only_when_asked(self):
        plain, _ = self.launch()
        varied, files = self.launch(varied=True)
        self.assertNotIn("--stub-varied", plain)
        self.assertIn("--stub-varied", varied)
        self.assertLess(varied.index("--stub-varied"), varied.index("--"))
        self.assertEqual(varied.index("--serve-stub") + 2, varied.index("--stub-varied"))
        self.assertEqual(files, [".env.intake", ".env.intake.mqtt"])

    def test_a_given_http_port_is_used_and_extra_arguments_follow(self):
        cmd, _ = self.launch(http_port=18081, extra=("--per-reporter-cap", "4"))
        self.assertEqual(cmd[cmd.index("--http-port") + 1], "18081")
        self.assertEqual(cmd[-2:], ["--per-reporter-cap", "4"])
        self.assertEqual(cmd[cmd.index("--mqtt-port") + 1], "18885")

    def test_the_command_passes_the_stub_guard(self):
        cmd, _ = self.launch(http_port=18081)
        e2e.guard_stub_run(srv.parse_args(cmd[cmd.index("--") + 1:]))


class ManualCheckGuardTests(unittest.TestCase):
    def test_the_defaults_are_acceptable(self):
        manual.guard_manual_args(manual.DEFAULT_CONTAINER, manual.DEFAULT_BROKER_PORT, manual.DEFAULT_HTTP_PORT)
        self.assertNotEqual(manual.DEFAULT_BROKER_PORT, e2e.LIVE_BROKER_PORT)
        self.assertNotEqual(manual.DEFAULT_HTTP_PORT, srv.DEFAULT_HTTP_PORT)
        self.assertLess(manual.REPORTER_CAP, srv.DEFAULT_PER_REPORTER_CAP)

    def test_the_live_broker_the_real_intake_and_other_containers_are_refused(self):
        for container, broker, http in (("force-mosquitto", 18885, 8081), ("fbp-e2e-", 18885, 8081), ("fbp-e2e-x", 1883, 8081), ("fbp-e2e-x", 18885, 8080),
                                        ("fbp-e2e-x", 18885, 18885), ("fbp-e2e-x", 80, 8081), ("fbp-e2e-x", 18885, 70000), ("fbp-e2e-x", 70000, 8081),
                                        ("fbp-e2e-x", 18885, 0)):
            with self.subTest(args=(container, broker, http)), self.assertRaises(SystemExit):
                manual.guard_manual_args(container, broker, http)

    def test_a_refused_run_touches_neither_docker_nor_a_process(self):
        with mock.patch.object(e2e, "docker", side_effect=AssertionError("docker was called")), \
                mock.patch.object(e2e.subprocess, "Popen", side_effect=AssertionError("a process was started")):
            for argv in (["--broker-port", "1883"], ["--http-port", "8080"], ["--container", "force-mosquitto"], ["--container", "other"]):
                with self.subTest(argv=argv), self.assertRaises(SystemExit):
                    manual.main(argv)

    def test_the_setup_it_builds_is_the_authenticated_throwaway_broker_with_the_stub(self):
        source = (S.ROOT / "intake" / "manual_check.py").read_text(encoding="utf-8")
        for needle in ("create_anonymous_broker", "configure_auth_broker", "varied=True", "remove_broker", "guard_manual_args(args.container"):
            self.assertIn(needle, source)
        self.assertNotIn("force-mosquitto", source.split('"""', 2)[2])           # past the module docstring the live broker is never named


class ChecklistTests(unittest.TestCase):
    def test_it_names_every_stub_trigger_and_the_setup_it_describes(self):
        for trigger in stub.TRIGGERS:
            self.assertIn(trigger, CHECKLIST)
        for needle in (manual.DEFAULT_CONTAINER, str(manual.DEFAULT_BROKER_PORT), f"127.0.0.1:{manual.DEFAULT_HTTP_PORT}", "manual_check.py",
                       f"**{manual.REPORTER_CAP} accepted reports**"):
            self.assertIn(needle, CHECKLIST)

    def test_every_numbered_step_has_a_row_in_the_results_table(self):
        steps = [int(n) for n in re.findall(r"^\*\*(\d+)\.", CHECKLIST, re.M)]
        rows = [int(n) for n in re.findall(r"^\| (\d+) ", CHECKLIST, re.M)]
        self.assertEqual(steps, list(range(1, len(steps) + 1)))
        self.assertEqual(sorted(rows), steps)

    def test_the_values_it_expects_are_the_ones_the_stub_gives(self):
        dark, force, plain = stub.answer_for("vader"), stub.answer_for("jedi"), stub.answer_for("weather")
        for answer, relevance in ((dark, "0.95"), (force, "0.70"), (plain, "0.04")):
            self.assertIn(relevance, CHECKLIST)
            self.assertEqual(f"{answer['relevance_score']:.2f}", relevance)
        for needle in (f"{dark['midichlorian_ppm']:.0f} ppm", f"{dark['kyber_resonance']:.1f}", f"{dark['dark_side_activity']:.1f}", dark["inference_rationale"],
                       f"{force['midichlorian_ppm']:.0f} ppm", f"{force['kyber_resonance']:.1f}", f"{force['dark_side_activity']:.1f}",
                       f"{plain['midichlorian_ppm']:.0f} ppm", f"{plain['kyber_resonance']:.1f}", f"{plain['dark_side_activity']:.1f}"):
            self.assertIn(needle, CHECKLIST)

    def test_the_messages_it_expects_are_the_ones_the_page_and_server_show(self):
        sources = "\n".join(p.read_text(encoding="utf-8") for p in WEB.glob("*.js")) + (S.ROOT / "intake" / "intake_server.py").read_text(encoding="utf-8")
        for phrase in ("Choose a planet first.", "Describe what you saw first.", "The report could not be sent yet.", "This report could not be assessed.",
                       "Report filed", "Nothing is waiting to be sent.", "Choose a planet to see its recent reports.", "No reports for this planet yet.",
                       "You have filed as many reports as are allowed today. Try again tomorrow.", "Choose a planet",
                       "This browser cannot keep reports between visits: closing this tab would lose a waiting report."):
            self.assertIn(phrase, FLAT)
            self.assertIn(phrase, sources, f"{phrase!r} is in the checklist but not in the page or server")
        self.assertIn(core.BLOCKED_MESSAGE, FLAT)

    def test_the_timings_it_expects_are_the_ones_in_the_code(self):
        queue = (WEB / "queue.js").read_text(encoding="utf-8")
        self.assertIn("FETCH_TIMEOUT_MS = 60 * 1000", queue)
        self.assertIn("[5 * 1000, 30 * 1000, 2 * 60 * 1000, 10 * 60 * 1000]", queue)
        self.assertIn("60 second timeout", FLAT)
        self.assertIn("in 5 seconds (attempt 1 of 5)", FLAT)
        self.assertEqual(core.RETRY_AFTER_TRANSIENT_S, 30)
        self.assertIn("in 30 seconds", FLAT)
        self.assertEqual(intake_mqtt.RETRY_AFTER_S["unavailable"], 15)
        self.assertIn("in 15 seconds", FLAT)
        self.assertEqual(stub.SLOW_SECONDS, 65)
        self.assertIn("65 seconds", FLAT)

    def test_it_only_ever_touches_the_throwaway_container(self):
        for command in re.findall(r"docker [^\n`]*", CHECKLIST):
            self.assertTrue(re.fullmatch(r"docker (stop|start) fbp-e2e-manual|docker ps( -a)?|docker ps -a` shows[^\n]*", command.strip()) or "fbp-e2e-manual" in command
                            or command.startswith("docker ps"), command)
        self.assertNotRegex(CHECKLIST, r"docker (stop|rm|restart|kill|exec|start)[^\n]*force-mosquitto")
        self.assertNotRegex(CHECKLIST, r"1883")

    def test_the_storage_key_it_names_is_the_one_the_page_uses(self):
        key = re.search(r'REPORTER_KEY = "([^"]+)"', (WEB / "identity.js").read_text(encoding="utf-8")).group(1)
        self.assertEqual(key, "fbp.reporter_id")
        self.assertIn(f'localStorage.removeItem("{key}")', CHECKLIST)
        self.assertIn(f"`{key}`", CHECKLIST)

    def test_it_holds_no_secret_or_address(self):
        for pattern in (r"AIza", r"hooks\.", r"https?://(?!127\.0\.0\.1)", r"\b192\.168\.", r"password\s*=", r"dbc-"):
            self.assertNotRegex(CHECKLIST, pattern)


if __name__ == "__main__":
    unittest.main()
