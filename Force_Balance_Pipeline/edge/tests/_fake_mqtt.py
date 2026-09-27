"""A tiny in-memory MQTT broker that behaves like the Phase 3 Mosquitto for authentication and ACLs, exposed through a paho-like `Client`,
so mqtt_check.py and probe_ctl.py (and the publisher) can be tested offline. Like Mosquitto it ACKNOWLEDGES a QoS 1 publish that the ACL then
drops, so only delivery shows a denial. It is a model of the contract, not a broker: the real proof is the desktop and Pi steps."""
import fnmatch
from types import SimpleNamespace

MQTT_ERR_SUCCESS = 0


def acl_from_file(text):
    """{user: {"read": [patterns], "write": [patterns]}} from infra/mosquitto/acl."""
    acl, user = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, rest = line.partition(" ")
        if key == "user":
            user = rest.strip()
            acl[user] = {"read": [], "write": []}
        elif key == "topic":
            kind, _, pattern = rest.strip().partition(" ")
            acl[user][kind].append(pattern.strip())
    return acl


def matches(pattern, topic):
    return fnmatch.fnmatchcase(topic, pattern.replace("#", "*"))


class Broker:
    def __init__(self, users, acl, allow_anonymous=False, reachable=True):
        self.users, self.acl, self.allow_anonymous, self.reachable = dict(users), acl, allow_anonymous, reachable
        self.clients, self.retained = [], []

    def deliver(self, sender, topic, payload):
        if not any(matches(p, topic) for p in self.acl.get(sender.username, {}).get("write", [])):
            return                                                    # acknowledged, then dropped
        for c in self.clients:
            if c.connected and any(matches(s, topic) for s in c.subscriptions):
                c.on_message(c, None, SimpleNamespace(topic=topic, payload=payload))


class ReasonCode:
    def __init__(self, failure):
        self.is_failure = failure
        self.value = 135 if failure else 0

    def __str__(self):
        return "Not authorized" if self.is_failure else "Success"


class Client:
    """paho-mqtt 2.x Client, as far as this project uses it. `Client.broker` is set by the test."""
    broker = None

    def __init__(self, version=None, client_id=None, clean_session=None):
        self.client_id, self.username, self.password = client_id, None, None
        self.connected, self.subscriptions = False, []
        self.on_connect = self.on_message = self.on_subscribe = self.on_publish = self.on_disconnect = None
        self.mids = 0

    def username_pw_set(self, username, password=None):
        self.username, self.password = username, password

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        pass

    def connect(self, host, port=1883, keepalive=60):
        if not self.broker.reachable:
            raise TimeoutError("timed out")
        self._host = host

    connect_async = connect

    def loop_start(self):
        ok = (self.username in self.broker.users and self.broker.users[self.username] == self.password) if self.username is not None \
            else self.broker.allow_anonymous
        self.connected = bool(ok)
        if self.connected:
            self.broker.clients.append(self)
        if self.on_connect:
            self.on_connect(self, None, SimpleNamespace(session_present=False), ReasonCode(not ok))

    def loop_stop(self):
        pass

    def disconnect(self):
        self.connected = False
        if self in self.broker.clients:
            self.broker.clients.remove(self)

    def subscribe(self, topic, qos=0):
        self.mids += 1
        allowed = any(matches(p, topic) or matches(topic, p) for p in self.broker.acl.get(self.username, {}).get("read", []))
        if allowed:
            self.subscriptions.append(topic)
        if self.on_subscribe:
            self.on_subscribe(self, None, self.mids, [SimpleNamespace(value=1 if allowed else 128)])
        return (0, self.mids)

    def publish(self, topic, payload=None, qos=0):
        self.mids += 1
        payload = payload.encode() if isinstance(payload, str) else payload
        self.broker.deliver(self, topic, payload)
        if self.on_publish:
            self.on_publish(self, None, self.mids, None, None)
        return SimpleNamespace(rc=MQTT_ERR_SUCCESS, mid=self.mids)


def module(broker):
    """A stand-in for `paho.mqtt.client` wired to `broker`."""
    client_cls = type("Client", (Client,), {"broker": broker})
    return SimpleNamespace(Client=client_cls, CallbackAPIVersion=SimpleNamespace(VERSION2="v2"), MQTT_ERR_SUCCESS=0)
