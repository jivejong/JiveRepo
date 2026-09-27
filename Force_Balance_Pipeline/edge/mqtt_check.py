#!/usr/bin/env python3
"""Verify the LAN broker (docs 04, 05, Phase 3 staging): the Pi can connect and nothing else can.

    python edge/mqtt_check.py --host <DESKTOP_IP> --only probe-01              # on the Pi: only its own password is known
    python edge/mqtt_check.py --host <DESKTOP_IP>                              # on the desktop: all three users

Checks (passwords come from the environment, PROBE_MQTT_PASSWORD, BRIDGE_MQTT_PASSWORD and OPERATOR_MQTT_PASSWORD, or a prompt; a
password is never printed):
  1. an anonymous connection is refused;
  2. probe-01 with a wrong password is refused;
  3. probe-01 with its password connects, may subscribe force/control/probe-01 and publish force/telemetry/probe-01 (PUBACK);
  and with all three passwords, the ACL by DELIVERY (Mosquitto acknowledges a QoS 1 publish it then drops, so only a subscriber can tell):
  4. probe-01's telemetry reaches force-bridge;  5. operator's control message reaches probe-01;
  6. probe-01 publishing to the control topic does NOT reach operator;  7. force-bridge publishing to the control topic does NOT
     reach probe-01;  8. operator publishing telemetry does NOT reach force-bridge.
A connection that times out (no answer at all) is reported as such: that is what a firewall drop looks like, and it is the expected
result for a device the rule does not admit. It does not use HTTP, so the project User-Agent does not apply.
"""
import argparse
import getpass
import os
import sys
import threading
import uuid

USERS = {"probe-01": "PROBE_MQTT_PASSWORD", "force-bridge": "BRIDGE_MQTT_PASSWORD", "operator": "OPERATOR_MQTT_PASSWORD"}
TELEMETRY, CONTROL = "force/telemetry/probe-01", "force/control/probe-01"


class Session:
    """One MQTT connection: connect result, subscriptions granted, messages received, publishes acknowledged."""

    def __init__(self, mqtt, host, port, client_id, username=None, password=None, timeout=5.0):
        self.mqtt, self.timeout = mqtt, timeout
        self.result, self.received, self.granted = None, [], {}
        self._connected, self._acked, self._subscribed = threading.Event(), threading.Event(), threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
        if username is not None:
            self.client.username_pw_set(username, password)
        self.client.on_connect = self._on_connect
        self.client.on_message = lambda c, u, m: self.received.append(m.payload)
        self.client.on_subscribe = self._on_subscribe
        self.client.on_publish = lambda c, u, mid, rc=None, props=None: self._acked.set()
        self.host, self.port = host, port

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.result = "refused" if getattr(reason_code, "is_failure", False) else "connected"
        self._connected.set()

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        self.granted = {"ok": all(getattr(rc, "value", rc) < 128 for rc in reason_codes)}
        self._subscribed.set()

    def connect(self):
        try:
            self.client.connect(self.host, self.port, keepalive=30)
        except OSError as e:
            self.result = f"unreachable ({type(e).__name__})"
            return self.result
        self.client.loop_start()
        if not self._connected.wait(self.timeout):
            self.result = "timeout"
        return self.result

    def subscribe(self, topic):
        self._subscribed.clear()
        self.client.subscribe(topic, qos=1)
        return self._subscribed.wait(self.timeout) and self.granted.get("ok", False)

    def publish(self, topic, payload):
        self._acked.clear()
        self.client.publish(topic, payload, qos=1)
        return self._acked.wait(self.timeout)

    def close(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass


class Report:
    def __init__(self, out):
        self.out, self.failures = out, []

    def result(self, ok, text):
        self.out(f"  [{'PASS' if ok else 'FAIL'}] {text}")
        if not ok:
            self.failures.append(text)


def password_for(user, environ, prompt):
    value = environ.get(USERS[user])
    if value:
        return value
    return prompt(f"password for MQTT user {user}: ") if prompt else None


def delivered(mqtt, host, port, timeout, listener, topic, sender, payload, wait):
    """Did `payload`, published by `sender` (a connected Session) to `topic`, reach `listener` (a connected Session)?"""
    listener.received.clear()
    sender.publish(topic, payload)
    end = threading.Event()
    end.wait(wait)
    return payload in listener.received


def run(host, port, only=None, mqtt=None, environ=None, prompt=None, wait=2.0, out=print, timeout=5.0):
    if mqtt is None:
        import paho.mqtt.client as mqtt
    environ = os.environ if environ is None else environ
    report = Report(out)
    tag = uuid.uuid4().hex[:8]
    sessions = []

    def session(user, password, name=None):
        s = Session(mqtt, host, port, f"mqtt-check-{name or user}-{tag}", user, password, timeout)
        sessions.append(s)
        return s

    try:
        out(f"1. refusals ({host}:{port})")
        anonymous = session(None, None, "anon")
        got = anonymous.connect()
        report.result(got in ("refused", "timeout"), f"an anonymous connection is not accepted ({got})")
        probe_password = password_for("probe-01", environ, prompt)
        wrong = session("probe-01", (probe_password or "") + "-wrong", "wrong")
        got = wrong.connect()
        report.result(got in ("refused", "timeout"), f"probe-01 with a wrong password is not accepted ({got})")
        out("2. probe-01 with its own password")
        probe = session("probe-01", probe_password, "probe")
        got = probe.connect()
        report.result(got == "connected", f"probe-01 connects ({got})")
        if got != "connected":
            return report.failures
        report.result(probe.subscribe(CONTROL), f"probe-01 may subscribe {CONTROL}")
        report.result(probe.publish(TELEMETRY, f"check-{tag}".encode()), f"probe-01 publishes {TELEMETRY} and the broker acknowledges it")
        if only == "probe-01":
            out("(only probe-01 was checked: the ACL delivery checks need the other two passwords, run them on the desktop)")
            return report.failures
        out("3. the ACL, by delivery")
        bridge_s = session("force-bridge", password_for("force-bridge", environ, prompt), "bridge")
        operator = session("operator", password_for("operator", environ, prompt), "operator")
        for s, user in ((bridge_s, "force-bridge"), (operator, "operator")):
            report.result(s.connect() == "connected", f"{user} connects")
        if report.failures:
            return report.failures
        bridge_s.subscribe("force/telemetry/#")
        operator.subscribe(CONTROL)
        probe.subscribe(CONTROL)
        msg = lambda label: f"check-{label}-{tag}".encode()      # noqa: E731
        report.result(delivered(mqtt, host, port, timeout, bridge_s, TELEMETRY, probe, msg("t1"), wait),
                      "probe-01's telemetry reaches force-bridge")
        report.result(delivered(mqtt, host, port, timeout, probe, CONTROL, operator, msg("c1"), wait),
                      "operator's control message reaches probe-01")
        report.result(not delivered(mqtt, host, port, timeout, operator, CONTROL, probe, msg("c2"), wait),
                      "probe-01 publishing to the control topic does NOT reach operator")
        report.result(not delivered(mqtt, host, port, timeout, probe, CONTROL, bridge_s, msg("c3"), wait),
                      "force-bridge publishing to the control topic does NOT reach probe-01")
        report.result(not delivered(mqtt, host, port, timeout, bridge_s, TELEMETRY, operator, msg("t2"), wait),
                      "operator publishing telemetry does NOT reach force-bridge")
    finally:
        for s in sessions:
            s.close()
    return report.failures


def main(argv=None, **kw):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--only", choices=["probe-01"], help="check only probe-01 (the Pi knows only its own password)")
    p.add_argument("--no-prompt", action="store_true", help="do not ask for a missing password; fail instead")
    args = p.parse_args(argv)
    prompt = None if args.no_prompt else getpass.getpass
    failures = run(args.host, args.port, only=args.only, prompt=kw.pop("prompt", prompt), **kw)
    print("\n" + ("BROKER CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
