"""Publishing a report envelope to the broker (transport C): the intake process hands each report to the bridge through the
broker, like any other event, so the broker's persistent session and the bridge's acknowledge-after-landing carry it the rest
of the way.

The client is the broker user `web-intake`, whose ACL allows one thing: writing force/telemetry/web-intake (infra/mosquitto/acl).
It speaks MQTT 5. That is deliberate: under MQTT 3.1.1 Mosquitto acknowledges a QoS 1 publish that the ACL then drops, so a
wrong ACL would look like success and the report would vanish; under MQTT 5 the PUBACK carries a reason code ("Not
authorized"), which publish() turns into PublishError("refused"). The e2e still checks that the file really lands.

publish() returns only after the broker's PUBACK, or raises PublishError. It never queues a message for later: when the
connection is down it refuses at once. (A message that was sent but not acknowledged within the timeout may still be delivered
by paho's own retry on reconnect; it carries the same event_id, and silver keeps one copy.)

Credentials: INTAKE_MQTT_USERNAME / INTAKE_MQTT_PASSWORD from the process environment or the gitignored .env.intake.mqtt, which
holds only those two keys. The password is never printed or logged.
"""
import os
import threading
import time
from pathlib import Path

import intake_core as core

MQTT_ENV_FILE_DEFAULT = core.ROOT / ".env.intake.mqtt"
MQTT_ENV_KEYS = ("INTAKE_MQTT_USERNAME", "INTAKE_MQTT_PASSWORD")
CLIENT_ID = "force-intake"
PUBLISH_TIMEOUT_S = 10.0
KEEPALIVE_S = 30
RECONNECT_MIN_S, RECONNECT_MAX_S = 1, 30
CONNECT_FAIL_LOG_INTERVAL_S = 60
RETRY_AFTER_S = {"unavailable": 15, "timeout": 15, "failed": 30, "refused": 300}


class PublishError(Exception):
    """The report was not handed to the broker. kind: unavailable (no connection), timeout (no PUBACK in time), refused (the
    broker answered with a failure reason code, e.g. not authorized), failed (anything else)."""

    def __init__(self, kind, message):
        super().__init__(f"{kind}: {message}")
        self.kind = kind

    @property
    def retry_after_s(self):
        return RETRY_AFTER_S.get(self.kind, 30)


# ---- credentials (.env.intake.mqtt) -------------------------------------------------------------------------
def read_mqtt_env_file(path):
    """KEY=VALUE lines, # comments, optional quotes, and ONLY the two intake MQTT keys. Any other key (the Gemini key, the dbt
    token, the bridge's or operator's passwords) is refused. Messages name the file and line, never a value."""
    path = Path(path)
    values = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise core.ConfigError(f"{path.name} line {number}: expected KEY=VALUE")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in MQTT_ENV_KEYS:
            raise core.ConfigError(f"{path.name} line {number}: {key} is not allowed; this file holds only " + ", ".join(MQTT_ENV_KEYS))
        if key in values:
            raise core.ConfigError(f"{path.name} line {number}: {key} is set twice")
        values[key] = value
    return values


def mqtt_credentials(environ=os.environ, env_file=MQTT_ENV_FILE_DEFAULT):
    """(username, password), or (None, None) for an anonymous broker (a local development broker only). The process environment
    wins over the file. A username without a password, or the reverse, is refused."""
    env = {key: environ.get(key) for key in MQTT_ENV_KEYS}
    env_file = Path(env_file)
    if env_file.exists():
        for key, value in read_mqtt_env_file(env_file).items():
            if value and not env.get(key):
                env[key] = value
    username, password = env["INTAKE_MQTT_USERNAME"], env["INTAKE_MQTT_PASSWORD"]
    if bool(username) != bool(password):
        raise core.ConfigError("INTAKE_MQTT_USERNAME and INTAKE_MQTT_PASSWORD go together: set both, or neither for an anonymous broker")
    return (username, password) if username else (None, None)


# ---- the publisher ------------------------------------------------------------------------------------------
class MqttPublisher:
    """One long-lived MQTT 5 client with automatic reconnect. publish() waits for the PUBACK. Thread-safe: the HTTP server calls
    it from many threads."""

    def __init__(self, host, port, username=None, password=None, *, mqtt=None, clock=time.monotonic, log=print):
        if mqtt is None:
            try:
                import paho.mqtt.client as mqtt
            except ImportError:
                raise core.ConfigError("paho-mqtt is not installed: pip install -r edge/requirements.txt (in the edge venv)") from None
        self._mqtt, self._clock, self._log = mqtt, clock, log
        self.host, self.port = host, port
        self._cond = threading.Condition()
        self._acks = {}                     # mid -> reason code, for acknowledgements nobody has collected yet
        self._abandoned = set()             # mids whose caller gave up waiting; their late PUBACK is dropped
        self._fail_log = {"last": None, "count": 0}
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID, protocol=mqtt.MQTTv5)
        if username:
            self._client.username_pw_set(username, password)
        self._client.reconnect_delay_set(RECONNECT_MIN_S, RECONNECT_MAX_S)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_publish = self._on_publish

    # -- lifecycle
    def start(self):
        """Connect in the background; an unreachable broker is retried with backoff, never an error."""
        self._client.connect_async(self.host, self.port, keepalive=KEEPALIVE_S)
        self._client.loop_start()

    def ready(self):
        return bool(self._client.is_connected())

    def close(self):
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()

    # -- callbacks (paho's loop thread)
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            self._log(f"intake: MQTT connect refused: {reason_code}")
        else:
            self._log("intake: connected to the broker")

    def _on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        self._log(f"intake: disconnected from the broker: {reason_code}")

    def _on_connect_fail(self, client, userdata):
        state, now = self._fail_log, self._clock()
        state["count"] += 1
        if state["last"] is not None and now - state["last"] < CONNECT_FAIL_LOG_INTERVAL_S:
            return
        count, state["last"], state["count"] = state["count"], now, 0
        self._log(f"intake: cannot reach the broker at {self.host}:{self.port} ({count} failed attempt(s) since the last report); retrying")

    def _on_publish(self, client, userdata, mid, reason_code=None, properties=None):
        with self._cond:
            if mid in self._abandoned:
                self._abandoned.discard(mid)
                return
            self._acks[mid] = reason_code
            self._cond.notify_all()

    # -- publishing
    def publish(self, topic, payload, timeout=PUBLISH_TIMEOUT_S):
        """QoS 1, returning only after the broker's PUBACK. Raises PublishError."""
        if not self.ready():
            raise PublishError("unavailable", "not connected to the broker")
        # Never hold self._cond across client.publish(): paho's loop thread calls on_publish while holding the mutex that
        # publish() needs, so a lock held here that on_publish also wants would deadlock. An acknowledgement that arrives
        # before this thread starts waiting is simply already in self._acks.
        info = self._client.publish(topic, payload, qos=1)
        if info.rc != self._mqtt.MQTT_ERR_SUCCESS:
            raise PublishError("unavailable", f"the client refused the message (rc {info.rc})")
        mid, deadline = info.mid, self._clock() + timeout
        with self._cond:
            while mid not in self._acks:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self._abandoned.add(mid)
                    raise PublishError("timeout", f"no PUBACK within {timeout:g} s")
                self._cond.wait(remaining)
            reason = self._acks.pop(mid)
        if getattr(reason, "is_failure", False):
            raise PublishError("refused", f"the broker refused the publish: {reason}")
