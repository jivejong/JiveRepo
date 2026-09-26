"""Bridge command line and configuration (docs 02, 04, 05): defaults match the docs, workspace mode uses only the
bridge's own credentials (from the environment or a gitignored .env.bridge that holds only those keys) and never
the dbt PAT, the MQTT session is persistent, and the broker is configured to queue. Offline."""
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402
from files_api import FilesApiUploader  # noqa: E402

ROOT = S.ROOT
NO_ENV_FILE = Path(tempfile.gettempdir()) / "no-such-env-bridge-file"   # tests never read a real .env.bridge
CLIENT_SECRET = "s3cr3t-value-XYZ"


def doc04():
    return (ROOT / "docs" / "04-edge-simulators.md").read_text(encoding="utf-8")


def doc04_flag_defaults():
    """{flag: default text} from the flag table in doc 04's Configuration section."""
    rows = re.findall(r"^\| `(--[a-z-]+)` \| (`[^`]*`|none) \|", doc04(), re.M)
    return {flag: default.strip("`") for flag, default in rows}


def write_env(text):
    path = Path(tempfile.mkdtemp()) / ".env.bridge"
    path.write_text(text, encoding="utf-8")
    return path


class DocParityTests(unittest.TestCase):
    def test_flag_defaults_equal_the_doc_04_table(self):
        table, args = doc04_flag_defaults(), bridge.parse_args([])
        self.assertEqual(int(table["--max-bytes"]), args.max_bytes)
        self.assertEqual(float(table["--max-seconds"]), args.max_seconds)
        self.assertEqual(int(table["--scan-size"]), args.scan_size)
        self.assertEqual(table["--topic"], args.topic)
        self.assertEqual(table["--mqtt-host"], args.mqtt_host)
        self.assertEqual(int(table["--mqtt-port"]), args.mqtt_port)
        self.assertEqual(table["--client-id"], args.client_id)
        self.assertEqual(table["--volume-path"], args.volume_path)
        self.assertEqual(table["--oauth-scope"], args.oauth_scope)
        self.assertEqual(args.oauth_scope, "files")
        self.assertEqual(table["--local-dir"], "none")
        self.assertIsNone(args.local_dir)
        self.assertEqual(table["--dead-letter"], args.dead_letter.relative_to(ROOT).as_posix())
        self.assertEqual(table["--env-file"], bridge.ENV_FILE_DEFAULT.name)
        self.assertEqual(bridge.ENV_FILE_DEFAULT.parent, ROOT)

    def test_every_flag_in_the_doc_table_exists_and_every_config_flag_is_in_the_doc(self):
        table = set(doc04_flag_defaults())
        parser_flags = set(re.findall(r"^\s+p\.add_argument\(\"(--[a-z-]+)\"", (ROOT / "ingest/bridge/bridge.py").read_text(encoding="utf-8"), re.M))
        operational = {"--stats-interval", "--exit-after-files", "--idle-exit", "--max-attempts"}  # not configuration
        self.assertEqual(table, parser_flags - operational)

    def test_doc_02_flush_policy_matches_the_defaults(self):
        doc02 = (ROOT / "docs" / "02-event-contract.md").read_text(encoding="utf-8")
        self.assertEqual(int(re.search(r"(\d+) MB accumulated", doc02).group(1)) * 1024 * 1024, bridge.DEFAULT_MAX_BYTES)
        self.assertEqual(int(re.search(r"(\d+) seconds elapsed", doc02).group(1)), bridge.DEFAULT_MAX_SECONDS)
        self.assertIn("all 60 planets", doc02)
        self.assertEqual(bridge.DEFAULT_SCAN_SIZE, 60)

    def test_there_is_no_yaml_config_and_no_yaml_dependency(self):
        section = doc04().split("### Configuration", 1)[1]
        self.assertNotIn("```yaml", section)
        self.assertNotIn("yaml", (ROOT / "edge" / "requirements.txt").read_text(encoding="utf-8").lower().replace("no yaml", ""))
        for base in (ROOT / "ingest", ROOT / "edge"):
            for path in base.rglob("*.py"):
                if ".venv" in path.parts or "tests" in path.parts:
                    continue
                self.assertIsNone(re.search(r"^\s*(import|from)\s+yaml\b", path.read_text(encoding="utf-8"), re.M), path)


class ModeTests(unittest.TestCase):
    def test_local_dir_mode_needs_no_credentials_and_never_reads_an_env_file(self):
        poisoned = write_env("DATABRICKS_TOKEN=would-be-refused\n")
        args = bridge.parse_args(["--local-dir", "somewhere", "--env-file", str(poisoned)])
        self.assertIsInstance(bridge.build_uploader(args, environ={}, default_env_file=NO_ENV_FILE), bridge.LocalDirUploader)

    def test_workspace_mode_needs_the_bridge_credentials(self):
        with self.assertRaises(SystemExit) as cm:
            bridge.build_uploader(bridge.parse_args([]), environ={}, default_env_file=NO_ENV_FILE)
        for name in bridge.BRIDGE_ENV_KEYS:
            self.assertIn(name, str(cm.exception))

    def test_the_dbt_pat_is_not_a_substitute(self):
        """Doc 05: the bridge uses its own files-scoped service principal, never the personal access token."""
        env = {"DATABRICKS_HOST": "h.invalid", "DATABRICKS_TOKEN": "dbt-pat", "DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/x"}
        with self.assertRaises(SystemExit) as cm:
            bridge.build_uploader(bridge.parse_args([]), environ=env, default_env_file=NO_ENV_FILE)
        self.assertIn("BRIDGE_DATABRICKS_CLIENT_ID", str(cm.exception))
        self.assertNotIn("dbt-pat", str(cm.exception))
        source = (ROOT / "ingest" / "bridge" / "bridge.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"""environ(\[|\.get\()\s*["']DATABRICKS_TOKEN""", source))
        self.assertIsNone(re.search(r"""env(\[|\.get\()\s*["']DATABRICKS_TOKEN""", source))

    def test_workspace_mode_builds_a_files_api_uploader_from_the_environment(self):
        env = {"DATABRICKS_HOST": "h.invalid", "BRIDGE_DATABRICKS_CLIENT_ID": "id", "BRIDGE_DATABRICKS_CLIENT_SECRET": "sec"}
        up = bridge.build_uploader(bridge.parse_args([]), environ=env, default_env_file=NO_ENV_FILE)
        self.assertIsInstance(up, FilesApiUploader)
        self.assertEqual(up.url("a.ndjson"), "https://h.invalid/api/2.0/fs/files/Volumes/force/raw/telemetry/a.ndjson?overwrite=false")

    def test_argument_defaults(self):
        args = bridge.parse_args([])
        self.assertEqual((args.mqtt_host, args.mqtt_port, args.exit_after_files, args.idle_exit), ("localhost", 1883, 0, 0.0))


class OAuthScopeTests(unittest.TestCase):
    ENV = {"DATABRICKS_HOST": "h.invalid", "BRIDGE_DATABRICKS_CLIENT_ID": "id", "BRIDGE_DATABRICKS_CLIENT_SECRET": "sec"}

    def test_the_default_token_scope_is_files(self):
        up = bridge.build_uploader(bridge.parse_args([]), environ=self.ENV, default_env_file=NO_ENV_FILE)
        self.assertEqual(up.tokens.scope, "files")

    def test_the_scope_flag_reaches_the_token_provider(self):
        up = bridge.build_uploader(bridge.parse_args(["--oauth-scope", "all-apis"]), environ=self.ENV, default_env_file=NO_ENV_FILE)
        self.assertEqual(up.tokens.scope, "all-apis")
        up = bridge.build_uploader(bridge.parse_args(["--oauth-scope", "files  sql"]), environ=self.ENV, default_env_file=NO_ENV_FILE)
        self.assertEqual(up.tokens.scope, "files sql")

    def test_an_empty_scope_is_a_clear_error(self):
        with self.assertRaises(SystemExit) as cm:
            bridge.build_uploader(bridge.parse_args(["--oauth-scope", " "]), environ=self.ENV, default_env_file=NO_ENV_FILE)
        self.assertIn("--oauth-scope", str(cm.exception))

    def test_doc_05_names_the_files_scope(self):
        doc05 = (ROOT / "docs" / "05-platform-setup.md").read_text(encoding="utf-8")
        self.assertIn("**`files`** scope", doc05)
        self.assertIn("--oauth-scope", doc05)
        self.assertNotIn("scope `all-apis`, sent with", doc05)


class EnvFileTests(unittest.TestCase):
    def test_reads_the_three_keys_with_comments_blank_lines_and_quotes(self):
        path = write_env('# comment\n\nDATABRICKS_HOST=h.invalid\nBRIDGE_DATABRICKS_CLIENT_ID="abc-123"\n'
                         f"BRIDGE_DATABRICKS_CLIENT_SECRET='{CLIENT_SECRET}'\n")
        self.assertEqual(bridge.read_env_file(path), {"DATABRICKS_HOST": "h.invalid", "BRIDGE_DATABRICKS_CLIENT_ID": "abc-123",
                                                      "BRIDGE_DATABRICKS_CLIENT_SECRET": CLIENT_SECRET})

    def test_the_file_fills_workspace_mode(self):
        path = write_env(f"DATABRICKS_HOST=h.invalid\nBRIDGE_DATABRICKS_CLIENT_ID=id\nBRIDGE_DATABRICKS_CLIENT_SECRET={CLIENT_SECRET}\n")
        up = bridge.build_uploader(bridge.parse_args(["--env-file", str(path)]), environ={}, default_env_file=NO_ENV_FILE)
        self.assertIsInstance(up, FilesApiUploader)
        self.assertEqual(up.tokens._client_id, "id")

    def test_the_default_file_is_used_when_no_flag_is_given(self):
        path = write_env(f"DATABRICKS_HOST=h.invalid\nBRIDGE_DATABRICKS_CLIENT_ID=id\nBRIDGE_DATABRICKS_CLIENT_SECRET={CLIENT_SECRET}\n")
        up = bridge.build_uploader(bridge.parse_args([]), environ={}, default_env_file=path)
        self.assertIsInstance(up, FilesApiUploader)

    def test_the_process_environment_wins_over_the_file(self):
        path = write_env("DATABRICKS_HOST=file.invalid\nBRIDGE_DATABRICKS_CLIENT_ID=file-id\nBRIDGE_DATABRICKS_CLIENT_SECRET=file-secret\n")
        env = {"BRIDGE_DATABRICKS_CLIENT_ID": "env-id"}
        up = bridge.build_uploader(bridge.parse_args(["--env-file", str(path)]), environ=env, default_env_file=NO_ENV_FILE)
        self.assertEqual(up.tokens._client_id, "env-id")
        self.assertEqual(up.host, "https://file.invalid")  # the rest still comes from the file

    def test_DATABRICKS_TOKEN_in_the_file_is_refused_without_echoing_it(self):
        path = write_env(f"DATABRICKS_HOST=h.invalid\nDATABRICKS_TOKEN=dapi-real-looking-{CLIENT_SECRET}\n")
        with self.assertRaises(SystemExit) as cm:
            bridge.read_env_file(path)
        self.assertIn("DATABRICKS_TOKEN", str(cm.exception))
        self.assertIn("personal access token", str(cm.exception))  # the dedicated refusal, not the generic "not allowed"
        self.assertNotIn("is not allowed", str(cm.exception))
        self.assertIn("line 2", str(cm.exception))
        self.assertNotIn(CLIENT_SECRET, str(cm.exception))
        with self.assertRaises(SystemExit):
            bridge.build_uploader(bridge.parse_args(["--env-file", str(path)]), environ={}, default_env_file=NO_ENV_FILE)

    def test_any_other_key_is_refused_the_file_holds_only_the_three(self):
        for key in ("GEMINI_API_KEY", "POSTGRES_PASSWORD", "DATABRICKS_HTTP_PATH", "INFERENCE_MODEL"):
            path = write_env(f"DATABRICKS_HOST=h.invalid\n{key}={CLIENT_SECRET}\n")
            with self.subTest(key=key), self.assertRaises(SystemExit) as cm:
                bridge.read_env_file(path)
            self.assertIn("is not allowed", str(cm.exception))
            self.assertNotIn(CLIENT_SECRET, str(cm.exception))

    def test_malformed_and_duplicate_lines_are_refused(self):
        with self.assertRaises(SystemExit) as cm:
            bridge.read_env_file(write_env("DATABRICKS_HOST h.invalid\n"))
        self.assertIn("expected KEY=VALUE", str(cm.exception))
        with self.assertRaises(SystemExit) as cm:
            bridge.read_env_file(write_env("DATABRICKS_HOST=a\nDATABRICKS_HOST=b\n"))
        self.assertIn("set twice", str(cm.exception))

    def test_an_explicit_env_file_that_does_not_exist_is_an_error(self):
        with self.assertRaises(SystemExit) as cm:
            bridge.build_uploader(bridge.parse_args(["--env-file", str(NO_ENV_FILE)]), environ={}, default_env_file=NO_ENV_FILE)
        self.assertIn("does not exist", str(cm.exception))

    def test_a_missing_default_file_falls_back_to_the_environment_only(self):
        with self.assertRaises(SystemExit) as cm:
            bridge.build_uploader(bridge.parse_args([]), environ={"DATABRICKS_HOST": "h.invalid"}, default_env_file=NO_ENV_FILE)
        self.assertIn("BRIDGE_DATABRICKS_CLIENT_SECRET", str(cm.exception))

    def test_the_file_is_gitignored_and_its_template_is_empty_and_has_only_the_three_keys(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env.bridge", [l.strip() for l in ignored])
        template = (ROOT / ".env.bridge.example").read_text(encoding="utf-8")
        assignments = re.findall(r"(?m)^([A-Z_]+)=(.*)$", template)
        self.assertEqual([k for k, _ in assignments], list(bridge.BRIDGE_ENV_KEYS))
        self.assertTrue(all(v == "" for _, v in assignments), "the template holds no values")
        bridge.read_env_file(ROOT / ".env.bridge.example")  # and the bridge accepts it as written
        self.assertNotIn("BRIDGE_DATABRICKS", (ROOT / ".env.example").read_text(encoding="utf-8").replace("BRIDGE_DATABRICKS_CLIENT", "").replace("bridge's own", ""))

    def test_the_repos_default_env_file_is_not_read_by_these_tests(self):
        """A developer's real .env.bridge must never leak into a test: build_uploader takes the default path
        as a parameter and every test above passes a nonexistent one."""
        self.assertFalse(NO_ENV_FILE.exists())


class PersistentSessionTests(unittest.TestCase):
    class FakeMqtt:
        class CallbackAPIVersion:
            VERSION2 = "v2"

        class Client:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs

    class Reason:
        def __init__(self, failure=False):
            self.is_failure = failure

        def __str__(self):
            return "refused"

    class Flags:
        session_present = True

    class FakeClient:
        def __init__(self):
            self.subscriptions = []

        def subscribe(self, topic, qos=0):
            self.subscriptions.append((topic, qos))

    def test_a_fixed_client_id_and_a_persistent_session(self):
        client = bridge.build_client(self.FakeMqtt, bridge.parse_args([]))
        self.assertEqual(client.kwargs["client_id"], "force-bridge")
        self.assertIs(client.kwargs["clean_session"], False)
        self.assertEqual(client.args[0], "v2")
        client = bridge.build_client(self.FakeMqtt, bridge.parse_args(["--client-id", "other"]))
        self.assertEqual(client.kwargs["client_id"], "other")

    def test_subscribes_at_qos_1_on_every_connect_including_reconnects(self):
        client, on_connect = self.FakeClient(), bridge.make_on_connect("force/telemetry/#")
        on_connect(client, None, self.Flags(), self.Reason(), None)
        on_connect(client, None, self.Flags(), self.Reason(), None)  # a reconnect
        self.assertEqual(client.subscriptions, [("force/telemetry/#", 1)] * 2)

    def test_a_refused_connect_does_not_subscribe(self):
        client = self.FakeClient()
        bridge.make_on_connect("t")(client, None, self.Flags(), self.Reason(failure=True), None)
        self.assertEqual(client.subscriptions, [])

    def test_the_real_paho_client_is_built_with_a_persistent_session(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.skipTest("paho-mqtt is not installed (run the suite with the edge venv)")
        client = bridge.build_client(mqtt, bridge.parse_args([]))
        self.assertEqual(client._client_id, b"force-bridge")
        self.assertIs(client._clean_session, False)


class BrokerConfigTests(unittest.TestCase):
    def conf(self):
        return (ROOT / "infra" / "mosquitto" / "mosquitto.conf").read_text(encoding="utf-8")

    def value(self, key):
        return re.search(rf"(?m)^{key}\s+(\S+)", self.conf()).group(1)

    def test_persistence_and_a_queue_deep_enough_for_a_restart(self):
        self.assertEqual(self.value("persistence"), "true")
        self.assertEqual(self.value("persistence_location"), "/mosquitto/data/")
        queued = int(self.value("max_queued_messages"))
        self.assertGreater(queued, 1000, "the Mosquitto default of 1000 is about 16 scans")
        self.assertGreaterEqual(queued, 60 * 96 * 5, "at least five days of scans (60 events, 96 scans a day)")
        self.assertIn(self.value("persistent_client_expiration"), ("7d", "1w"))

    def test_doc_04_states_the_queue_depth_the_broker_has(self):
        self.assertIn(f"max_queued_messages {self.value('max_queued_messages')}", doc04())

    def test_the_config_is_still_local_only(self):
        self.assertIn("allow_anonymous true", self.conf())
        self.assertIn("127.0.0.1", self.conf())
        self.assertIn("OPEN:", doc04())


if __name__ == "__main__":
    unittest.main()
