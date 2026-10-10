"""Lint of the Phase 3 infrastructure files (docs 04 and 05): the LAN broker is authenticated and least-privilege, the unit restarts always and
claims no more about time sync than is true, the env templates hold no values, the deploy script pins a full SHA and starts nothing, no
file has CRs or a credential, and the docs and the ACL say the same thing. Offline: nothing here starts a service."""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"


def text(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def directives(source):
    """{key: [values]} from a Mosquitto config, comments removed."""
    found = {}
    for raw in source.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            key, _, value = line.partition(" ")
            found.setdefault(key, []).append(value.strip())
    return found


def unit_lines(source):
    return [l.strip() for l in source.splitlines() if l.strip() and not l.strip().startswith("#")]


class LanBrokerTests(unittest.TestCase):
    conf = directives(text("infra", "mosquitto", "mosquitto.lan.conf"))

    def test_anonymous_access_is_off_and_credentials_and_acl_files_are_set(self):
        self.assertEqual(self.conf["allow_anonymous"], ["false"])
        self.assertEqual(self.conf["password_file"], ["/mosquitto/secrets/passwd"])
        self.assertEqual(self.conf["acl_file"], ["/mosquitto/secrets/acl"])

    def test_the_password_file_is_outside_the_repository_mount(self):
        self.assertNotIn("/mosquitto/config", self.conf["password_file"][0])
        self.assertFalse((INFRA / "mosquitto" / "passwd").exists())

    def test_the_acl_file_is_deployed_from_a_volume_copy_not_the_bind_mount(self):
        # The ACL's committed source (infra/mosquitto/acl, asserted below to still exist and hold no secrets) is
        # not what Mosquitto actually reads -- acl_file points at the volume copy, mirroring the password file's
        # own split between committed/mounted source and the path Mosquitto is told to open.
        self.assertNotIn("/mosquitto/config", self.conf["acl_file"][0])
        self.assertTrue((INFRA / "mosquitto" / "acl").exists())

    def test_it_declares_one_plain_listener_and_no_tls_yet(self):
        self.assertEqual(self.conf["listener"], ["1883"])
        for key in ("cafile", "certfile", "keyfile", "require_certificate"):
            self.assertNotIn(key, self.conf)

    def test_persistence_and_the_queue_limit_keep_the_doc_04_numbers(self):
        self.assertEqual(self.conf["persistence"], ["true"])
        self.assertEqual(self.conf["max_queued_messages"], ["50000"])

    def test_the_local_config_is_unchanged_in_behaviour_and_names_the_lan_one(self):
        source = text("infra", "mosquitto", "mosquitto.conf")
        self.assertEqual(directives(source)["allow_anonymous"], ["true"])
        self.assertIn("single-machine development", source)
        self.assertIn("mosquitto.lan.conf", source)
        self.assertNotIn("laptop", source.lower())
        self.assertNotIn("e2-micro (Phase 3)", source)


class AclTests(unittest.TestCase):
    source = text("infra", "mosquitto", "acl")

    def parsed(self):
        acl, user = {}, None
        for raw in self.source.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            words = line.split()
            if words[0] == "user":
                user = words[1]
                acl[user] = []
            else:
                self.assertEqual(words[0], "topic", line)
                acl[user].append((words[1], words[2]))
        return acl

    def test_exactly_the_three_users_with_least_privilege(self):
        self.assertEqual(self.parsed(), {
            "probe-01": [("write", "force/telemetry/probe-01"), ("read", "force/control/probe-01")],
            "force-bridge": [("read", "force/telemetry/#")],
            "operator": [("write", "force/control/probe-01"), ("read", "force/#")]})

    def test_no_global_or_pattern_rules_grant_everyone_access(self):
        self.assertNotRegex(self.source, r"(?m)^\s*(pattern|topic)\s+(readwrite|write)\s+#")
        self.assertNotRegex(self.source, r"(?m)^\s*pattern\b")

    def test_only_the_operator_can_write_the_control_topic(self):
        writers = [u for u, rules in self.parsed().items() if ("write", "force/control/probe-01") in rules]
        self.assertEqual(writers, ["operator"])

    def test_only_the_probe_can_write_telemetry_and_the_bridge_cannot_write_at_all(self):
        acl = self.parsed()
        writers = [u for u, rules in acl.items() if any(k == "write" and t.startswith("force/telemetry") for k, t in rules)]
        self.assertEqual(writers, ["probe-01"])
        self.assertFalse([r for r in acl["force-bridge"] if r[0] == "write"])

    def test_the_acl_and_doc_04_state_the_same_permissions(self):
        doc = re.sub(r"\s+", " ", text("docs", "04-edge-simulators.md"))
        for phrase in ("`probe-01` writes `force/telemetry/probe-01` and reads `force/control/probe-01`",
                       "`force-bridge` reads `force/telemetry/#`", "`operator` writes `force/control/probe-01`"):
            self.assertIn(phrase, doc)


class UnitTests(unittest.TestCase):
    source = text("infra", "pi", "force-probe.service")
    lines = unit_lines(source)

    def test_it_restarts_always(self):
        self.assertIn("Restart=always", self.lines)
        self.assertRegex(self.source, r"(?m)^RestartSec=\d+$")

    def test_it_runs_the_live_probe_with_the_venv_python_as_a_dedicated_user(self):
        exec_start = [l for l in self.lines if l.startswith("ExecStart=")]
        self.assertEqual(len(exec_start), 1)
        self.assertTrue(exec_start[0].startswith("ExecStart=/opt/force-probe/venv/bin/python "), exec_start[0])
        self.assertIn("Force_Balance_Pipeline/edge/probe_sim.py --live", exec_start[0])
        self.assertIn("User=force-probe", self.lines)
        self.assertNotIn("User=root", self.lines)

    def test_the_mode_schedule_is_on_now_the_checkpoint_has_passed_and_the_clock_bypass_stays_off(self):
        # doc 07: the checkpoint passed 2026-09-29 (PHASE3-RESULTS.md); the schedule that was held off for it is now on
        exec_start = [l for l in self.lines if l.startswith("ExecStart=")][0]
        self.assertIn("--mode-schedule", exec_start)
        self.assertNotIn("--assume-clock-synced", exec_start)

    def test_fault_injection_is_off_again_now_the_period_has_ended(self):
        # doc 04 / docs/ENGINEERING-LOG.md, "Phase 4 close": the fault period ran 2026-10-07T13:29:35Z to the
        # 2026-10-10 pull of fault_injection.jsonl; the seed built from it is frozen (that period can't recur), so
        # --fault-rate is back to 0 rather than left running at doc 04's default indefinitely.
        exec_start = [l for l in self.lines if l.startswith("ExecStart=")][0]
        self.assertRegex(exec_start, r"--fault-rate\s+0\b")
        self.assertIn("fault_injection.jsonl", self.source)   # the reconciliation source is named in the unit's own comment

    def test_the_state_directory_is_persistent_and_secrets_come_from_the_env_file_only(self):
        self.assertIn("StateDirectory=force-probe", self.lines)
        self.assertIn("EnvironmentFile=/etc/force-probe/probe.env", self.lines)
        self.assertIn("Environment=PROBE_STATE_DIR=/var/lib/force-probe", self.lines)
        self.assertFalse([l for l in self.lines if re.match(r"Environment=.*(PASSWORD|USERNAME|HOST)", l)])

    def test_it_waits_for_the_network_and_the_time_sync_target(self):
        after = [l for l in self.lines if l.startswith("After=")][0]
        self.assertIn("network-online.target", after)
        self.assertIn("time-sync.target", after)

    def test_the_comment_claims_ordering_only_and_says_the_probe_check_is_the_guarantee(self):
        comments = " ".join(l.lstrip("# ") for l in self.source.splitlines() if l.startswith("#"))
        comments = re.sub(r"\s+", " ", comments)
        self.assertIn("systemd-time-wait-sync.service", comments)
        self.assertIn("does not guarantee a synchronised clock", comments)
        self.assertIn("NTPSynchronized", comments)
        self.assertNotRegex(comments, r"(?i)ensures? (that )?the (clock|time)")

    def test_it_is_hardened_without_blocking_its_own_state(self):
        for line in ("NoNewPrivileges=true", "ProtectSystem=full", "PrivateTmp=true"):
            self.assertIn(line, self.lines)
        self.assertNotIn("ProtectSystem=strict", self.lines)          # strict would need ReadWritePaths for the state directory


class EnvTemplateTests(unittest.TestCase):
    def values(self, *parts):
        out = {}
        for raw in text(*parts).splitlines():
            if raw.strip() and not raw.startswith("#"):
                key, _, value = raw.partition("=")
                out[key] = value
        return out

    def test_the_probe_template_has_the_three_keys_and_no_values(self):
        self.assertEqual(self.values("infra", "pi", "probe.env.example"),
                         {"PROBE_MQTT_HOST": "", "PROBE_MQTT_USERNAME": "", "PROBE_MQTT_PASSWORD": ""})

    def test_the_bridge_template_has_the_two_keys_and_no_values_and_no_operator_password(self):
        self.assertEqual(self.values(".env.mqtt.example"), {"BRIDGE_MQTT_USERNAME": "", "BRIDGE_MQTT_PASSWORD": ""})
        self.assertNotIn("OPERATOR", text(".env.mqtt.example").split("\n", 1)[1].replace("operator password", ""))

    def test_the_real_env_files_are_listed_in_gitignore(self):
        ignored = text(".gitignore").splitlines()
        for name in (".env.mqtt", ".env.bridge", ".env"):
            self.assertIn(name, ignored)

    def test_a_real_env_file_that_exists_is_not_tracked_by_git(self):
        # the desktop staging run legitimately creates .env.mqtt; it must never be trackable, whether or not
        # it happens to exist on this machine right now
        for path in (ROOT / ".env.mqtt", ROOT / ".env.bridge", INFRA / "pi" / "probe.env"):
            if path.exists():
                result = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT)
                self.assertEqual(result.returncode, 0, f"{path} exists and is NOT gitignored")


class DeployScriptTests(unittest.TestCase):
    source = text("infra", "pi", "deploy.sh")
    code = "\n".join(l for l in source.splitlines() if not l.strip().startswith("#"))

    def test_it_stops_on_error_and_requires_a_full_40_character_sha(self):
        self.assertIn("set -euo pipefail", self.code)
        self.assertIn(r"^[0-9a-f]{40}$", self.code)

    def test_it_verifies_the_checkout_is_at_the_requested_commit(self):
        self.assertRegex(self.code, r'(?m)^if \[\[ "\$\(git -C "\$BASE/repo" rev-parse HEAD\)" != "\$SHA" \]\]; then\n\s+echo [^\n]*\n\s+exit 1\nfi$')
        self.assertIn("checkout --detach", self.code)

    def test_the_sparse_cone_is_the_edge_the_seeds_and_infra_pi(self):
        self.assertIn("sparse-checkout set --cone Force_Balance_Pipeline/edge Force_Balance_Pipeline/warehouse/dbt/seeds "
                     "Force_Balance_Pipeline/infra/pi", self.code)

    def test_the_header_no_longer_asks_for_an_scp(self):
        self.assertNotIn("scp ", self.source)
        self.assertIn("run from inside that clone", self.source)

    def test_it_never_pipes_a_download_into_a_shell_and_never_unconditionally_starts_or_enables_the_service(self):
        # a never-enabled unit must stay manual (probe.env is still just the template at that point) -- but an
        # ALREADY-RUNNING one is now restarted on redeploy (below), which is the one exception to "never touches
        # the service," so only the unconditional start/enable forms are forbidden here, not restart itself.
        self.assertNotRegex(self.code, r"(curl|wget)[^\n]*\|\s*(ba)?sh")
        commands = chr(10).join(l for l in self.code.splitlines() if not l.strip().startswith("echo"))     # the closing message names the next step
        for word in ("systemctl start", "systemctl enable", "--now"):
            self.assertNotIn(word, commands)

    def test_restart_is_conditional_on_the_service_already_being_active(self):
        # found live, 2026-10-06: installing the new unit and running daemon-reload changed what systemd WOULD
        # run next, but did nothing to the already-forked process still running the old ExecStart -- restart
        # must be guarded by is-active, not unconditional (an unconditional restart would start-from-cold a
        # never-enabled unit before probe.env has real credentials in it).
        self.assertRegex(self.code, r"if systemctl is-active --quiet force-probe; then\n\s+\w+=1\n\s+echo [^\n]*\n\s+\$SUDO systemctl restart force-probe\nfi")

    def test_it_verifies_the_restarted_process_is_newer_than_this_deploy_and_matches_the_requested_sha(self):
        self.assertIn("DEPLOY_START_EPOCH=", self.code)
        self.assertIn("ExecMainStartTimestamp", self.code)
        self.assertRegex(self.code, r'if \[\[ "\$START_EPOCH" -lt "\$DEPLOY_START_EPOCH" \]\]; then\n\s+echo [^\n]*\n\s+exit 1\n\s*fi')
        self.assertRegex(self.code, r'if \[\[ "\$CHECKED_OUT_SHA" != "\$SHA" \]\]; then\n\s+echo [^\n]*\n\s+exit 1\n\s*fi')
        self.assertIn("ps -p \"$MAIN_PID\"", self.code)

    def test_it_creates_the_env_file_root_owned_0600_and_only_if_missing(self):
        self.assertRegex(self.code, r"if \[\[ ! -f /etc/force-probe/probe\.env \]\]")
        self.assertIn("install -m 0600 -o root -g root", self.code)

    def test_it_holds_no_credential_and_no_address(self):
        for pattern in (r"(?i)password\s*=\s*\S", r"\b192\.168\.\d+\.\d+\b", r"(?i)token"):
            self.assertNotRegex(self.code, pattern)

    def test_the_version_file_the_probe_logs_is_written_from_the_sha(self):
        self.assertIn('echo "$SHA" > "$BASE/version"', self.code)
        self.assertIn("PROBE_VERSION_FILE=/opt/force-probe/version", text("infra", "pi", "force-probe.service"))

    def test_it_uses_an_unquoted_variable_only_for_sudo(self):
        self.assertEqual(sorted(set(re.findall(r"(?m)^\s*\$(\w+)\b", self.code))), ["SUDO"])


class NoCredentialsAnywhereTests(unittest.TestCase):
    FILES = [("infra", "mosquitto", "mosquitto.lan.conf"), ("infra", "mosquitto", "mosquitto.conf"), ("infra", "mosquitto", "acl"),
             ("infra", "pi", "force-probe.service"), ("infra", "pi", "probe.env.example"), ("infra", "pi", "deploy.sh"), (".env.mqtt.example",),
             ("edge", "probe_ctl.py"), ("edge", "mqtt_check.py"), ("ingest", "phase3_checkpoint.sql")]

    def test_no_file_has_carriage_returns(self):
        for parts in self.FILES + [("edge", "probe", n) for n in ("main.py", "runtime.py", "buffer.py", "modes.py", "clock.py", "faults.py",
                                                                  "control.py", "publisher.py")]:
            with self.subTest("/".join(parts)):
                self.assertNotIn(b"\r", ROOT.joinpath(*parts).read_bytes())

    def test_no_file_holds_a_lan_address_or_a_password_value(self):
        for parts in self.FILES:
            body = text(*parts)
            with self.subTest("/".join(parts)):
                self.assertNotRegex(body, r"\b192\.168\.\d+\.\d+\b")
                self.assertNotRegex(body, r"(?im)^(PROBE|BRIDGE|OPERATOR)_MQTT_PASSWORD=\S")

    def test_the_docs_use_placeholders_for_addresses(self):
        for name in ("04-edge-simulators.md", "05-platform-setup.md", "07-implementation-plan.md", "00-session-handoff.md"):
            with self.subTest(name):
                self.assertNotRegex(text("docs", name), r"192\.168\.0\.(101|110)")


class DocParityTests(unittest.TestCase):
    doc5 = re.sub(r"[ \t]+", " ", text("docs", "05-platform-setup.md"))

    def test_the_pi_setup_installs_sqlite3_and_nftables(self):
        self.assertRegex(self.doc5, r"apt install -y [^\n]*\bsqlite3\b[^\n]*\bnftables\b")

    def test_doc_05_places_each_credential_in_one_home(self):
        for phrase in ("`probe-01` goes in the Pi's `/etc/force-probe/probe.env`", "`force-bridge` goes in the desktop's `.env.mqtt`",
                       "`operator` is never stored", "OPERATOR_MQTT_PASSWORD"):
            self.assertIn(phrase, self.doc5)

    def test_doc_05_binds_the_broker_to_the_lan_address_and_loopback_never_all_interfaces(self):
        self.assertIn("-p <DESKTOP_IP>:1883:1883 -p 127.0.0.1:1883:1883", self.doc5)
        self.assertNotRegex(self.doc5, r"-p 0\.0\.0\.0:1883")
        self.assertNotRegex(self.doc5, r"-p 1883:1883")

    def test_doc_05_gives_the_cut_with_its_own_removal_scheduled_first(self):
        self.assertLess(self.doc5.index("systemd-run --on-active=45m"), self.doc5.index("nft add rule inet forcecut"))

    def test_doc_05_reads_the_pi_state_with_sudo(self):
        self.assertIn("sudo sqlite3 -readonly", self.doc5)

    def test_doc_05_makes_the_journal_persistent_before_anything_else_needs_it(self):
        # a real Pi power loss (Phase 3) lost the boot's own logs; this Pi OS image ships its own drop-in forcing
        # Storage=volatile outright, so `mkdir /var/log/journal` alone (Storage=auto's usual rule) has no effect --
        # an overriding drop-in of our own is required
        self.assertLess(self.doc5.index("journald.conf.d/90-force-persistent.conf"), self.doc5.index("sudo mkdir -p /opt/force-probe"))
        self.assertIn("Storage=persistent", self.doc5)
        self.assertIn("SystemMaxUse=100M", self.doc5)
        self.assertIn("sudo systemctl restart systemd-journald", self.doc5)
        self.assertIn("journalctl --flush", self.doc5)

    def test_doc_05_copies_the_password_file_into_a_docker_volume_not_a_windows_bind_mount(self):
        # tested on the desktop (Step 3): Mosquitto 2.1.2 cannot open a password file mounted straight from Windows
        self.assertIn("docker volume create force-mosquitto-secrets", self.doc5)
        self.assertIn("chown mosquitto:mosquitto", self.doc5)
        self.assertIn("chmod 0600", self.doc5)
        self.assertIn("-v force-mosquitto-secrets:/mosquitto/secrets:ro", self.doc5)
        self.assertNotIn("may warn that the password file", self.doc5)                # the old, unverified hedge

    def test_doc_05_copies_the_acl_file_into_the_same_docker_volume_as_the_password_file(self):
        # Resolved, Phase 4 Stage 3b: the acl group-ownership warning got the password file's own volume-copy
        # fix, not left open -- mirrors test_doc_05_copies_the_password_file_into_a_docker_volume_not_a_windows_
        # bind_mount above, for the acl file instead of the password file.
        self.assertIn("cp /fromrepo/acl /to/acl", self.doc5)
        self.assertIn("chown mosquitto:mosquitto /to/passwd /to/acl", self.doc5)
        self.assertIn("chmod 0600 /to/passwd /to/acl", self.doc5)
        self.assertIn("future versions will refuse to load this file", self.doc5)      # why -- the warning's own text, kept
        self.assertNotIn("**OPEN:**", self.doc5)                                       # no longer open

    def test_doc_05_names_docker_desktop_backend_as_the_lan_exposure_and_says_never_block(self):
        # tested on the desktop (Step 3): two enabled Private-profile Allow rules for Docker Desktop Backend overrode the
        # Pi-only scoping, because Windows combines Allow rules rather than picking the most specific one
        self.assertIn("Docker Desktop Backend", self.doc5)
        self.assertIn("never Block", self.doc5)
        self.assertIn("172.17.0.1", self.doc5)                                        # why the broker log can't be the check
        self.assertNotIn("Docker Desktop may already have added its own inbound allow rules", self.doc5)

    def test_doc_05s_third_device_check_is_a_tcp_probe_not_the_broker_log(self):
        self.assertIn("TcpTestSucceeded", self.doc5)
        self.assertIn("not an MQTT client and", self.doc5)

    def test_doc_05_clones_by_hand_with_infra_pi_in_the_cone_and_no_scp(self):
        self.assertIn("git sparse-checkout set --cone Force_Balance_Pipeline/edge Force_Balance_Pipeline/warehouse/dbt/seeds "
                     "Force_Balance_Pipeline/infra/pi", self.doc5)
        self.assertNotIn("scp ", self.doc5)
        self.assertLess(self.doc5.index("git sparse-checkout set"), self.doc5.index("./deploy.sh"))

    def test_doc_05_starts_the_checkpoint_cut_a_few_minutes_past_the_hour(self):
        self.assertIn(":16 or :31 past the hour", self.doc5)

    def test_doc_05_creates_and_hands_over_opt_force_probe_before_the_manual_clone(self):
        # /opt is root-owned; a plain `git clone` into a not-yet-created /opt/force-probe as an unprivileged user fails
        self.assertLess(self.doc5.index('sudo chown "$(id -un)":"$(id -gn)" /opt/force-probe'),
                        self.doc5.index("git clone --filter=blob:none"))
        self.assertIn("as yourself, not with `sudo`", self.doc5)

    def test_doc_04_and_doc_05_name_the_same_sparse_cone(self):
        doc4 = re.sub(r"[ \t]+", " ", text("docs", "04-edge-simulators.md"))
        self.assertIn("`Force_Balance_Pipeline/edge`, `Force_Balance_Pipeline/warehouse/dbt/seeds`" + chr(10) +
                     "and `Force_Balance_Pipeline/infra/pi`", doc4)


if __name__ == "__main__":
    unittest.main()
