#!/usr/bin/env python3
"""Check the web-intake user's permissions on a RUNNING broker (the live one, after the ACL change in doc 05), with the real
publisher and the real login from .env.intake.mqtt. MQTT 5 makes a denied publish visible: the broker's PUBACK carries "Not authorized".

    edge\\.venv\\Scripts\\python.exe intake\\acl_check.py                 # the login works; publishes outside force/telemetry/web-intake are refused
    edge\\.venv\\Scripts\\python.exe intake\\acl_check.py --positive      # ALSO publishes one non-JSON line to force/telemetry/web-intake

The negative checks publish to topics nobody consumes (force/acl-check/denied) or that only the bridge reads (force/telemetry/acl-check);
never to the probe's topics and never to the control topic. If the ACL were too wide, the second one would reach the bridge, which
dead-letters a line that is not JSON: harmless. --positive does the same on the allowed topic, on purpose: the dead-letter line
(`not JSON`, in logs/bridge_dead_letter.ndjson) is the proof that the live bridge receives what web-intake publishes. Without it, the
first real report is that proof. Exit code 0 all checks passed, 1 a check failed, 2 configuration. The password is never printed.
"""
import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import intake_core as core  # noqa: E402
import intake_mqtt  # noqa: E402

DENIED_TOPICS = ("force/acl-check/denied", "force/telemetry/acl-check")
MARKER = b"acl-check: not an event, please ignore"


def main(argv=None, *, publisher_factory=intake_mqtt.MqttPublisher, environ=None, out=None, sleep=time.sleep, clock=time.monotonic):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--mqtt-host", default="127.0.0.1")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-env-file", type=Path, default=intake_mqtt.MQTT_ENV_FILE_DEFAULT)
    p.add_argument("--positive", action="store_true", help="also publish one non-JSON line to the allowed topic")
    p.add_argument("--connect-timeout", type=float, default=15.0)
    args = p.parse_args(argv)
    out = out if out is not None else sys.stdout
    environ = os.environ if environ is None else environ

    def say(text):
        print(text, file=out, flush=True)

    try:
        username, password = intake_mqtt.mqtt_credentials(environ, args.mqtt_env_file)
    except core.ConfigError as e:
        say(f"configuration problem: {e}")
        return 2
    if username != "web-intake":
        say("INTAKE_MQTT_USERNAME must be web-intake for this check (it tests that user's permissions)")
        return 2
    failures = []

    def check(ok, message):
        say(f"[{'PASS' if ok else 'FAIL'}] {message}")
        if not ok:
            failures.append(message)

    publisher = publisher_factory(args.mqtt_host, args.mqtt_port, username, password, log=lambda text: None)
    publisher.start()
    try:
        deadline = clock() + args.connect_timeout
        while not publisher.ready() and clock() < deadline:
            sleep(0.1)
        check(publisher.ready(), f"web-intake logs in to the broker at {args.mqtt_host}:{args.mqtt_port} (a wrong password or an unreachable broker fails here)")
        if publisher.ready():
            for topic in DENIED_TOPICS:
                try:
                    publisher.publish(topic, MARKER, timeout=10)
                except intake_mqtt.PublishError as e:
                    check(e.kind == "refused", f"a publish to {topic} is refused by the broker ({e.kind})")
                else:
                    check(False, f"a publish to {topic} was ACCEPTED: the web-intake ACL is wider than one topic")
            if args.positive:
                try:
                    publisher.publish(core.TOPIC, MARKER, timeout=10)
                except intake_mqtt.PublishError as e:
                    check(False, f"a publish to {core.TOPIC} failed ({e.kind}): the ACL does not allow the one topic it must")
                else:
                    check(True, f"a publish to {core.TOPIC} is acknowledged; the bridge should now dead-letter one 'not JSON' line")
            else:
                say("(positive check skipped; add --positive to publish one non-JSON line to the allowed topic)")
    finally:
        publisher.close()
    say("ALL CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
