"""The bridge's MQTT credentials (doc 04, Phase 3 staging): from the environment or a gitignored .env.mqtt that holds only the two bridge keys,
the environment wins, half a credential is refused, the operator password is never accepted from a file, and the password is never printed. Offline."""
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402

ROOT = S.ROOT
SECRET = "s3cr3t-mqtt-pw-QQ"


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mqtt-cred-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / ".env.mqtt"

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return self.path


class ReadTests(Files):
    def test_the_two_keys_with_comments_blank_lines_and_quotes(self):
        self.write(f"# comment{chr(10)}{chr(10)}BRIDGE_MQTT_USERNAME=force-bridge{chr(10)}BRIDGE_MQTT_PASSWORD='{SECRET}'{chr(10)}")
        self.assertEqual(bridge.read_mqtt_env_file(self.path), {"BRIDGE_MQTT_USERNAME": "force-bridge", "BRIDGE_MQTT_PASSWORD": SECRET})

    def test_any_other_key_is_refused_naming_the_line_and_never_the_value(self):
        for key in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "BRIDGE_DATABRICKS_CLIENT_SECRET", "MQTT_PASSWORD", "PROBE_MQTT_PASSWORD"):
            with self.subTest(key):
                self.write(f"BRIDGE_MQTT_USERNAME=u{chr(10)}{key}={SECRET}{chr(10)}")
                with self.assertRaises(SystemExit) as caught:
                    bridge.read_mqtt_env_file(self.path)
                message = str(caught.exception)
                self.assertIn("line 2", message)
                self.assertIn(key, message)
                self.assertNotIn(SECRET, message)

    def test_the_operator_password_is_never_accepted_from_a_file(self):
        self.write(f"OPERATOR_MQTT_PASSWORD={SECRET}{chr(10)}")
        with self.assertRaisesRegex(SystemExit, "never stored in a file"):
            bridge.read_mqtt_env_file(self.path)

    def test_a_repeated_key_and_a_line_without_equals_are_refused(self):
        self.write(f"BRIDGE_MQTT_USERNAME=a{chr(10)}BRIDGE_MQTT_USERNAME=b{chr(10)}")
        with self.assertRaisesRegex(SystemExit, "set twice"):
            bridge.read_mqtt_env_file(self.path)
        self.write("just words" + chr(10))
        with self.assertRaisesRegex(SystemExit, "expected KEY=VALUE"):
            bridge.read_mqtt_env_file(self.path)


class CredentialTests(Files):
    def test_no_file_and_no_environment_means_an_anonymous_broker(self):
        self.assertEqual(bridge.mqtt_credentials({}, self.tmp / "missing"), (None, None))

    def test_the_environment_alone(self):
        env = {"BRIDGE_MQTT_USERNAME": "force-bridge", "BRIDGE_MQTT_PASSWORD": SECRET}
        self.assertEqual(bridge.mqtt_credentials(env, self.tmp / "missing"), ("force-bridge", SECRET))

    def test_the_file_alone(self):
        self.write(f"BRIDGE_MQTT_USERNAME=force-bridge{chr(10)}BRIDGE_MQTT_PASSWORD={SECRET}{chr(10)}")
        self.assertEqual(bridge.mqtt_credentials({}, self.path), ("force-bridge", SECRET))

    def test_the_environment_wins_over_the_file(self):
        self.write(f"BRIDGE_MQTT_USERNAME=from-file{chr(10)}BRIDGE_MQTT_PASSWORD=file-pw{chr(10)}")
        env = {"BRIDGE_MQTT_USERNAME": "from-env", "BRIDGE_MQTT_PASSWORD": "env-pw"}
        self.assertEqual(bridge.mqtt_credentials(env, self.path), ("from-env", "env-pw"))
        self.assertEqual(bridge.mqtt_credentials({"BRIDGE_MQTT_PASSWORD": "env-pw"}, self.path), ("from-file", "env-pw"))

    def test_half_a_credential_is_refused(self):
        for env in ({"BRIDGE_MQTT_USERNAME": "force-bridge"}, {"BRIDGE_MQTT_PASSWORD": SECRET}):
            with self.subTest(env):
                with self.assertRaisesRegex(SystemExit, "go together"):
                    bridge.mqtt_credentials(env, self.tmp / "missing")

    def test_empty_values_in_the_template_shaped_file_mean_anonymous(self):
        self.write(f"BRIDGE_MQTT_USERNAME={chr(10)}BRIDGE_MQTT_PASSWORD={chr(10)}")
        self.assertEqual(bridge.mqtt_credentials({}, self.path), (None, None))


class ClientTests(Files):
    class FakeClient:
        def __init__(self):
            self.logins = []

        def username_pw_set(self, username, password):
            self.logins.append((username, password))

    def test_the_client_logs_in_when_credentials_exist_and_the_password_is_never_printed(self):
        client, out = self.FakeClient(), io.StringIO()
        env = {"BRIDGE_MQTT_USERNAME": "force-bridge", "BRIDGE_MQTT_PASSWORD": SECRET}
        with contextlib.redirect_stdout(out):
            self.assertTrue(bridge.apply_mqtt_credentials(client, env, self.tmp / "missing"))
        self.assertEqual(client.logins, [("force-bridge", SECRET)])
        self.assertIn("force-bridge", out.getvalue())
        self.assertNotIn(SECRET, out.getvalue())

    def test_an_anonymous_broker_gets_no_login(self):
        client = self.FakeClient()
        self.assertFalse(bridge.apply_mqtt_credentials(client, {}, self.tmp / "missing"))
        self.assertEqual(client.logins, [])


class RepositoryTests(unittest.TestCase):
    def test_the_template_has_exactly_the_two_keys_with_empty_values(self):
        lines = [l for l in (ROOT / ".env.mqtt.example").read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        self.assertEqual(lines, ["BRIDGE_MQTT_USERNAME=", "BRIDGE_MQTT_PASSWORD="])

    def test_the_real_file_is_gitignored_and_the_default_path_is_the_repo_root(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env.mqtt", ignored)
        self.assertEqual(bridge.MQTT_ENV_FILE_DEFAULT, ROOT / ".env.mqtt")

    def test_no_committed_file_holds_a_broker_password(self):
        for path in (ROOT / "infra" / "mosquitto" / "acl", ROOT / "infra" / "mosquitto" / "mosquitto.lan.conf"):
            text = path.read_text(encoding="utf-8").lower()
            for word in ("password=", "passwd:", "$7$", "$6$"):
                self.assertNotIn(word, text, path.name)

    def test_the_bridge_still_never_uses_the_dbt_token(self):
        self.assertNotIn("DATABRICKS_TOKEN", bridge.MQTT_ENV_KEYS + bridge.BRIDGE_ENV_KEYS)


if __name__ == "__main__":
    unittest.main()
