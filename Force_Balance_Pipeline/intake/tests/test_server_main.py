"""The intake process: its command line (no way to widen the bind address), the single-instance lock, the rotating log, configuration
errors, the broker client's lifecycle, and that the server makes no outbound call of its own."""
import json
import logging
import re
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _support as S
import bridge
import intake_mqtt as mq
import intake_server as srv
from _support import CountingModel, FakePublisher, KEY, MODEL, core
from test_server_http import raw_request

ENVIRON = {"GEMINI_API_KEY": KEY, "INFERENCE_MODEL": MODEL}


def argv(tmp, *extra):
    tmp = Path(tmp)
    return ["--http-port", "0", "--env-file", str(tmp / "absent.env"), "--mqtt-env-file", str(tmp / "absent.mqtt"),
            "--report-log", str(tmp / "reports.ndjson"), "--log-file", str(tmp / "intake.log"), "--lock-file", str(tmp / "intake.lock"),
            *extra]


def main_clean(*args, **kwargs):
    """srv.main, then close the log handlers it installed (an open log file would block removing the temp folder on Windows)."""
    try:
        return srv.main(*args, **kwargs)
    finally:
        logger = logging.getLogger(srv.LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def while_serving(server, work):
    """Serve in a thread for the duration of work(), as serve_forever would, then stop."""
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        return work()
    finally:
        server.shutdown()
        thread.join(5)


def returns(testcase, tmp, *extra, **kwargs):
    """go(), turning an exception where a return code is expected into an assertion FAIL."""
    try:
        return go(tmp, *extra, **kwargs)
    except Exception as e:
        testcase.fail(f"main raised {type(e).__name__} instead of returning an exit code")


def go(tmp, *extra, environ=None, publisher="fake", run=None, **kwargs):
    return main_clean(argv(tmp, *extra), process_wide=False, publisher=FakePublisher() if publisher == "fake" else publisher,
                    http=CountingModel(), run=run or (lambda server: None), environ=ENVIRON if environ is None else environ, **kwargs)


def log_text(tmp):
    path = Path(tmp) / "intake.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


class OwnedPublisher:
    """Replaces intake_mqtt.MqttPublisher so main() builds, starts and closes it without a network."""
    instances = []

    def __init__(self, host, port, username=None, password=None, log=print, **kw):
        self.args, self.events = (host, port, username, password), []
        OwnedPublisher.instances.append(self)

    def start(self):
        self.events.append("start")

    def close(self):
        self.events.append("close")

    def ready(self):
        return True

    def publish(self, topic, payload, timeout=10.0):
        pass


class CommandLineTests(unittest.TestCase):
    def test_the_defaults(self):
        args = srv.parse_args([])
        self.assertEqual((args.http_port, args.per_reporter_cap, args.global_cap, args.publish_timeout, args.thinking_level),
                         (8080, 20, 200, 10.0, "minimal"))
        self.assertEqual((args.mqtt_host, args.mqtt_port), ("127.0.0.1", 1883))
        self.assertEqual(args.report_log, core.ROOT / "logs" / "intake_reports.ndjson")
        self.assertEqual(args.log_file, core.ROOT / "logs" / "intake.log")
        self.assertEqual(args.lock_file, core.ROOT / "logs" / "intake.lock")
        self.assertEqual(args.env_file, core.ROOT / ".env.intake")
        self.assertEqual(args.mqtt_env_file, core.ROOT / ".env.intake.mqtt")

    def test_there_is_no_way_to_change_the_bind_address(self):
        import argparse
        parser_flags = set()
        original = argparse.ArgumentParser.add_argument

        def spy(self, *names, **kw):
            parser_flags.update(n for n in names if n.startswith("--"))
            return original(self, *names, **kw)
        with mock.patch.object(argparse.ArgumentParser, "add_argument", spy):
            srv.parse_args([])
        self.assertFalse({f for f in parser_flags if re.search(r"host|bind|addr|listen|interface", f)} - {"--mqtt-host"}, parser_flags)
        for flag in ("--host", "--bind", "--listen"):
            with self.assertRaises(SystemExit):
                srv.parse_args([flag, "0.0.0.0"])

    def test_the_caps_and_the_bound_are_validated(self):
        for bad in (["--per-reporter-cap", "0"], ["--global-cap", "0"], ["--publish-timeout", "0"], ["--http-port", "70000"],
                    ["--http-port", "-1"], ["--thinking-level", "extreme"]):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                srv.parse_args(bad)

    def test_the_caps_are_configurable(self):
        args = srv.parse_args(["--per-reporter-cap", "5", "--global-cap", "50", "--publish-timeout", "3.5", "--http-port", "9000"])
        self.assertEqual((args.per_reporter_cap, args.global_cap, args.publish_timeout, args.http_port), (5, 50, 3.5, 9000))


class RunTests(unittest.TestCase):
    def test_it_serves_on_127_0_0_1_logs_where_it_listens_and_releases_the_lock(self):
        seen = {}

        def run(server):
            seen["address"] = server.server_address
            seen["sectors"] = json.loads(while_serving(server, lambda: raw_request(server.server_port, "GET", "/api/sectors"))[2])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(go(tmp, run=run), 0)
            self.assertEqual(seen["address"][0], "127.0.0.1")
            self.assertEqual(len(seen["sectors"]), 60)
            text = log_text(tmp)
            self.assertRegex(text, r"intake: listening on 127\.0\.0\.1:\d+; model gemini-3\.1-flash-lite; broker 127\.0\.0\.1:1883")
            self.assertIn("caps 20 per reporter and 200 global per UTC day", text)
            lock = bridge.InstanceLock(Path(tmp) / "intake.lock").acquire()          # free again
            lock.release()
            for secret in (KEY,):
                self.assertNotIn(secret, text)

    def test_a_second_instance_exits_with_code_3_and_the_first_is_unharmed(self):
        outcome = {}

        def run(server):
            outcome["second"] = main_clean(argv(self.tmp, "--http-port", "0"), process_wide=False, publisher=FakePublisher(),
                                         environ=ENVIRON, run=lambda s: outcome.setdefault("second_ran", True))
            outcome["first_still_answers"] = while_serving(server, lambda: raw_request(server.server_port, "GET", "/api/sectors"))[0]
        with tempfile.TemporaryDirectory() as self.tmp:
            self.assertEqual(go(self.tmp, run=run), 0)
            self.assertEqual(outcome["second"], 3)
            self.assertNotIn("second_ran", outcome)
            self.assertEqual(outcome["first_still_answers"], 200)
            self.assertIn("another bridge holds", log_text(self.tmp))                  # the bridge's own lock message
            self.assertIn("this one exits with code 3", log_text(self.tmp))

    def test_ctrl_c_stops_it_cleanly(self):
        def run(server):
            raise KeyboardInterrupt
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(go(tmp, run=run), 0)
            self.assertIn("intake: stopping", log_text(tmp))

    def test_an_unexpected_error_still_releases_the_lock_and_closes_the_server(self):
        def run(server):
            self.port = server.server_port
            raise RuntimeError("boom")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                go(tmp, run=run)
            bridge.InstanceLock(Path(tmp) / "intake.lock").acquire().release()
            probe = socket.socket()
            probe.bind(("127.0.0.1", self.port))                                       # the port was released
            probe.close()

    def test_a_port_already_in_use_exits_with_code_4(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                code = main_clean([a if a != "0" else str(holder.getsockname()[1]) for a in argv(tmp)], process_wide=False,
                                publisher=FakePublisher(), environ=ENVIRON, run=lambda s: None)
                self.assertEqual(code, 4)
                self.assertIn("cannot listen on 127.0.0.1", log_text(tmp))
                bridge.InstanceLock(Path(tmp) / "intake.lock").acquire().release()
        finally:
            holder.close()


class ConfigurationTests(unittest.TestCase):
    def test_a_missing_key_exits_2_naming_the_key_and_the_lock_is_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(returns(self, tmp, environ={"INFERENCE_MODEL": MODEL}), 2)
            self.assertIn("GEMINI_API_KEY", log_text(tmp))
            bridge.InstanceLock(Path(tmp) / "intake.lock").acquire().release()

    def test_an_env_file_with_a_foreign_key_exits_2_without_echoing_a_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "absent.env"
            path.write_text(f"GEMINI_API_KEY={KEY}\nINFERENCE_MODEL={MODEL}\nDATABRICKS_TOKEN=dapi-SECRET-VALUE\n", encoding="utf-8")
            self.assertEqual(returns(self, tmp, environ={}), 2)
            text = log_text(tmp)
            self.assertIn("DATABRICKS_TOKEN is not allowed", text)
            self.assertNotIn("SECRET-VALUE", text)
            self.assertNotIn(KEY, text)

    def test_a_username_without_a_password_exits_2_before_any_broker_client_exists(self):
        OwnedPublisher.instances = []
        with mock.patch.object(mq, "MqttPublisher", OwnedPublisher), tempfile.TemporaryDirectory() as tmp:
            code = returns(self, tmp, publisher=None, environ=dict(ENVIRON, INTAKE_MQTT_USERNAME="web-intake"))
            self.assertEqual((code, OwnedPublisher.instances), (2, []))

    def test_an_unreadable_report_log_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "reports.ndjson").mkdir()
            self.assertEqual(returns(self, tmp), 2)
            self.assertIn("cannot read the report log", log_text(tmp))


class BrokerClientLifecycleTests(unittest.TestCase):
    def setUp(self):
        OwnedPublisher.instances = []
        patcher = mock.patch.object(mq, "MqttPublisher", OwnedPublisher)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.environ = dict(ENVIRON, INTAKE_MQTT_USERNAME="web-intake", INTAKE_MQTT_PASSWORD="pw-NOT-A-REAL-ONE")

    def test_the_client_is_built_with_the_credentials_started_after_the_bind_and_closed_at_the_end(self):
        seen = {}

        def run(server):
            seen["events_while_serving"] = list(OwnedPublisher.instances[0].events)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(go(tmp, "--mqtt-host", "10.1.2.3", "--mqtt-port", "1884", publisher=None, environ=self.environ, run=run), 0)
            (publisher,) = OwnedPublisher.instances
            self.assertEqual(publisher.args, ("10.1.2.3", 1884, "web-intake", "pw-NOT-A-REAL-ONE"))
            self.assertEqual(seen["events_while_serving"], ["start"])
            self.assertEqual(publisher.events, ["start", "close"])
            self.assertNotIn("pw-NOT-A-REAL-ONE", log_text(tmp))

    def test_it_is_closed_even_when_serving_fails(self):
        def run(server):
            raise RuntimeError("boom")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                go(tmp, publisher=None, environ=self.environ, run=run)
            self.assertEqual(OwnedPublisher.instances[0].events, ["start", "close"])

    def test_it_is_closed_and_never_started_when_the_port_is_taken(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                code = main_clean([a if a != "0" else str(holder.getsockname()[1]) for a in argv(tmp)], process_wide=False, publisher=None,
                                environ=self.environ, run=lambda s: None)
                self.assertEqual(code, 4)
                self.assertEqual(OwnedPublisher.instances[0].events, ["close"])
        finally:
            holder.close()

    def test_an_injected_publisher_is_left_alone(self):
        injected = OwnedPublisher("h", 1)
        with tempfile.TemporaryDirectory() as tmp:
            go(tmp, publisher=injected)
        self.assertEqual(injected.events, [])


class LoggingTests(unittest.TestCase):
    def test_the_log_is_the_bridges_rotating_utc_file_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = srv.configure_logging(Path(tmp) / "intake.log")
            try:
                (handler,) = logger.handlers
                self.assertIsInstance(handler, bridge._SafeRotatingFileHandler)
                self.assertEqual((handler.maxBytes, handler.backupCount), (5 * 1024 * 1024, 5))
                self.assertFalse(logger.propagate)
                self.assertEqual(logger.name, "force-intake")
                logger.info("a line")
                text = (Path(tmp) / "intake.log").read_text(encoding="utf-8")
                self.assertRegex(text, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z INFO a line\n$")
            finally:
                for h in list(logger.handlers):
                    logger.removeHandler(h)
                    h.close()

    def test_configuring_twice_does_not_double_a_line_and_a_console_is_added_only_when_there_is_one(self):
        import io
        with tempfile.TemporaryDirectory() as tmp:
            srv.configure_logging(Path(tmp) / "intake.log")
            console = io.StringIO()
            logger = srv.configure_logging(Path(tmp) / "intake.log", stdout=console)
            try:
                self.assertEqual(len(logger.handlers), 2)
                logger.info("once")
                self.assertEqual(console.getvalue(), "once\n")
                self.assertEqual((Path(tmp) / "intake.log").read_text(encoding="utf-8").count("once"), 1)
            finally:
                for h in list(logger.handlers):
                    logger.removeHandler(h)
                    h.close()

    def test_a_real_run_captures_process_output_as_the_bridge_does(self):
        with mock.patch.object(bridge, "capture_process_output") as capture, tempfile.TemporaryDirectory() as tmp:
            main_clean(argv(tmp), process_wide=True, publisher=FakePublisher(), http=CountingModel(), run=lambda s: None, environ=ENVIRON)
            capture.assert_called_once()


class NoOutboundCallsTests(unittest.TestCase):
    sources = {name: (S.ROOT / "intake" / name).read_text(encoding="utf-8") for name in ("intake_server.py", "intake_mqtt.py")}

    def test_neither_module_opens_a_connection_of_its_own(self):
        for name, source in self.sources.items():
            for forbidden in ("urlopen", "urllib.request", "http.client", "requests.get", "requests.post", "socket.create_connection", "import socket"):
                with self.subTest(module=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_the_only_way_to_the_model_is_intake_core_through_files_api(self):
        source = self.sources["intake_server.py"]
        self.assertEqual(source.count("http=files_api.http_request"), 2)               # the defaults of the injectable `http` (service, main)
        self.assertNotIn(".http_request(", source)
        self.assertNotIn("http_request(", self.sources["intake_mqtt.py"])
        self.assertIs(srv.IntakeService.__init__.__kwdefaults__["http"], srv.files_api.http_request)
        self.assertIs(srv.main.__kwdefaults__["http"], srv.files_api.http_request)

    def test_the_model_call_receives_the_services_transport(self):
        model = CountingModel()
        with tempfile.TemporaryDirectory() as tmp:
            service = srv.IntakeService(sectors=core.load_sectors(), api_key=KEY, model=MODEL, publisher=FakePublisher(),
                                        report_log=srv.ReportLog(Path(tmp) / "r.ndjson"), http=model, sleep=lambda _: None, log=lambda m: None)
            self.assertEqual(service.handle_report(S.report_body(1)).status, 200)
        self.assertEqual(model.calls, 1)


if __name__ == "__main__":
    unittest.main()
