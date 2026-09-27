#!/usr/bin/env python3
"""Operator control of the probe over MQTT (doc 04, control topic): inject a named signature, or force a mode for a period.

    python edge/probe_ctl.py --host <DESKTOP_IP> inject tatooine sith_presence [--ramp 2 --hold 4 --decay 3]
    python edge/probe_ctl.py --host <DESKTOP_IP> mode STEALTH --for-seconds 3600
    python edge/probe_ctl.py --host <DESKTOP_IP> mode DISCONNECTED --for-seconds 2700 --dry-run

The message is validated locally with the probe's own parser before it is sent, so a mistake is refused here with the same reason the
probe would give. It connects as the `operator` broker user; the password is asked for at a prompt (getpass), or read from
OPERATOR_MQTT_PASSWORD if that is set, and is never stored or printed, and never read from .env.mqtt. Publishes at QoS 1 and waits for the
acknowledgement. Only the operator user may write the control topic (infra/mosquitto/acl).
"""
import argparse
import getpass
import json
import os
import sys
import threading
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forcesim.sectors import DEFAULT_SEED, load_sectors  # noqa: E402
from probe import control  # noqa: E402

CONTROL_PREFIX = "force/control"
OPERATOR = "operator"


def build_message(args):
    if args.command == "inject":
        msg = {"inject": "spike", "sector_id": args.sector_id, "signature": args.signature}
        for key in ("ramp", "hold", "decay"):
            if getattr(args, key) is not None:
                msg[key] = getattr(args, key)
    else:
        msg = {"mode": args.mode}
        if args.for_seconds is not None:
            msg["for_seconds"] = args.for_seconds
    return msg


def send(host, port, topic, payload, password, mqtt=None, timeout=5.0):
    """Publish one message as the operator; returns None on success or the reason it failed."""
    if mqtt is None:
        import paho.mqtt.client as mqtt
    connected, acked = threading.Event(), threading.Event()
    state = {"refused": False}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        state["refused"] = bool(getattr(reason_code, "is_failure", False))
        connected.set()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"probe-ctl-{uuid.uuid4().hex[:8]}", clean_session=True)
    client.username_pw_set(OPERATOR, password)
    client.on_connect = on_connect
    client.on_publish = lambda c, u, mid, rc=None, props=None: acked.set()
    try:
        client.connect(host, port, keepalive=30)
    except OSError as e:
        return f"cannot reach the broker at {host}:{port} ({type(e).__name__})"
    client.loop_start()
    try:
        if not connected.wait(timeout):
            return "the broker did not answer"
        if state["refused"]:
            return "the broker refused the operator login (wrong password?)"
        client.publish(topic, payload, qos=1)
        if not acked.wait(timeout):
            return "the broker did not acknowledge the message"
        return None
    finally:
        client.loop_stop()
        client.disconnect()


def main(argv=None, sectors=None, mqtt=None, prompt=getpass.getpass, environ=None, out=print):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--source-id", default="probe-01")
    p.add_argument("--dry-run", action="store_true", help="validate and print the message; do not connect")
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("inject", help="inject a named signature on one planet")
    i.add_argument("sector_id")
    i.add_argument("signature")
    for key in ("ramp", "hold", "decay"):
        i.add_argument(f"--{key}", type=int)
    m = sub.add_parser("mode", help="force a mode for a period")
    m.add_argument("mode")
    m.add_argument("--for-seconds", type=int)
    args = p.parse_args(argv)
    environ = os.environ if environ is None else environ
    message = build_message(args)
    payload = json.dumps(message, separators=(",", ":"))
    try:
        control.parse(payload.encode(), sectors if sectors is not None else load_sectors(DEFAULT_SEED))
    except control.ControlError as e:
        out(f"refused, nothing sent: {e}")
        return 2
    topic = f"{CONTROL_PREFIX}/{args.source_id}"
    if args.dry_run:
        out(f"would publish to {topic}: {payload}")
        return 0
    password = environ.get("OPERATOR_MQTT_PASSWORD") or prompt("password for MQTT user operator: ")
    problem = send(args.host, args.port, topic, payload.encode(), password, mqtt=mqtt)
    if problem:
        out(f"not sent: {problem}")
        return 1
    out(f"sent to {topic}: {payload}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
