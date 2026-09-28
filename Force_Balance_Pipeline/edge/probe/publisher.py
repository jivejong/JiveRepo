"""The MQTT publisher (doc 04): QoS 1 publishes whose PUBACK the runtime waits for before it deletes a buffered row, plus the control
topic subscription. A thin wrapper over paho-mqtt 2.x so the runtime can be tested against a fake with the same interface.

Interface used by the runtime:
    connected            True while the broker connection is up
    disconnect_reason    why it last went down (text) or None
    publish(topic, payload, key) -> bool     False if it could not be sent; the key comes back from acked() once the PUBACK arrives
    acked() -> [key, ...]                    keys acknowledged since the last call
    reset()                                  drop the connection and reconnect (used when publishes stay unacknowledged)
    close()

Connecting never blocks the probe's loop: it uses paho's connect_async and its network thread, so scans keep their schedule while
the broker is unreachable. Credentials come from the caller (the environment), never from a file in the repository.

Every connect and disconnect prints one line to stderr (systemd sends that to journald): "probe: MQTT connected ..." or
"probe: MQTT disconnected: <reason>" / "probe: MQTT connect refused: <reason>", the same reasons the runtime's mode_transitions.jsonl
uses, so the two can be read side by side.

Concurrency (found the hard way, via py-spy on the Pi, and reproduced here at edge/tests/stress_publisher_broker.py against a real
broker): paho's own client.publish() (paho-mqtt 2.1.0/2.1.2, client.py) holds paho's _out_message_mutex for the whole call, and
paho's network thread holds that SAME mutex while it runs _handle_pubackcomp -> _do_on_publish -> our on_publish callback. An
earlier version of this file held self._lock across self.client.publish() to close the early-PUBACK race below — that is a
lock-order inversion: this thread holds self._lock wanting paho's mutex; paho's network thread holds paho's mutex wanting
self._lock. Deadlock (confirmed: a real client against a real broker hangs forever under enough volume; it did not reproduce
against the offline FakeClient in test_probe_publisher.py, which has no internal lock of its own to invert against). The rule now
is absolute: on_publish never touches self._lock (or any lock) and never blocks, and publish() never calls into paho while holding
self._lock.

on_publish instead only puts the acknowledged mid on a queue.Queue (thread-safe on its own, no lock needed). _pending, _early_acks
and _acked are touched only by the single thread that calls publish()/acked() (the runtime's tick() thread) — paho's network thread
never reaches them directly. The one remaining race, handled explicitly: a PUBACK for a publish can be queued by the network thread
before this thread's publish() has registered that mid in _pending. publish() drains the queue and checks _early_acks for its own
mid right after the paho call returns, so that PUBACK is never lost.
"""
import queue
import sys
import threading


class MqttPublisher:
    def __init__(self, host, port, client_id, username=None, password=None, control_topic=None, on_control=None,
                 keepalive=60, mqtt=None):
        if mqtt is None:
            import paho.mqtt.client as mqtt
        self._mqtt = mqtt
        self.host, self.port, self.keepalive = host, port, keepalive
        self.control_topic, self.on_control = control_topic, on_control
        self._lock = threading.Lock()                  # guards ONLY _connected / disconnect_reason; never held across a paho call
        self._connected, self.disconnect_reason = False, None
        self._acked_mids = queue.Queue()                # on_publish (network thread) -> publish()/acked() (the one caller thread)
        self._pending, self._early_acks, self._acked = {}, set(), []
        # clean_session=True: a reconnect starts a fresh session, so nothing stale is resent by the broker; anything unacknowledged
        # is still in the SQLite buffer and is resent by the drain (delivery is at-least-once, doc 04).
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
        if username:
            self.client.username_pw_set(username, password)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect, self.client.on_disconnect = self._on_connect, self._on_disconnect
        self.client.on_publish, self.client.on_message = self._on_publish, self._on_message

    def start(self):
        self.client.connect_async(self.host, self.port, self.keepalive)
        self.client.loop_start()

    # ---- callbacks (paho's network thread) ---------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        with self._lock:
            if getattr(reason_code, "is_failure", False):
                self._connected, self.disconnect_reason = False, f"connect_refused:{reason_code}"
                print(f"probe: MQTT connect refused: {reason_code}", file=sys.stderr, flush=True)
                return
            self._connected, self.disconnect_reason = True, None
        print(f"probe: MQTT connected (session present: {bool(getattr(flags, 'session_present', False))})",
             file=sys.stderr, flush=True)
        if self.control_topic:
            client.subscribe(self.control_topic, qos=1)

    def _on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        reason = f"client_disconnected:{reason_code}"
        with self._lock:
            self._connected = False
            self.disconnect_reason = reason
        self._forget_inflight()
        print(f"probe: MQTT disconnected: {reason}", file=sys.stderr, flush=True)

    def _on_publish(self, client, userdata, mid, reason_code=None, properties=None):
        # Runs on paho's network thread, sometimes while paho's own internal lock is held (_handle_pubackcomp calls this directly).
        # No lock, no blocking call of any kind here — see the module docstring.
        self._acked_mids.put(mid)

    def _on_message(self, client, userdata, message):
        if self.on_control is not None:
            self.on_control(message.payload)

    # ---- the runtime's interface -------------------------------------------------------------------------
    @property
    def connected(self):
        with self._lock:
            return self._connected

    def publish(self, topic, payload, key):
        with self._lock:
            if not self._connected:
                return False
        # Never call into paho while holding self._lock (see the module docstring: this is exactly the inversion py-spy found).
        info = self.client.publish(topic, payload, qos=1)
        if info.rc != self._mqtt.MQTT_ERR_SUCCESS:
            return False
        self._drain_acks()                              # a PUBACK for this very mid can race ahead of the next two lines
        if info.mid in self._early_acks:
            self._early_acks.discard(info.mid)
            self._acked.append(key)
        else:
            self._pending[info.mid] = key
        return True

    def acked(self):
        self._drain_acks()
        out, self._acked = self._acked, []
        return out

    def reset(self):
        with self._lock:
            self._connected, self.disconnect_reason = False, "publish_unacked"
        self._forget_inflight()
        self.client.disconnect()
        self.client.connect_async(self.host, self.port, self.keepalive)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()

    # ---- internal: caller-thread-only state (see the module docstring) --------------------------------------
    def _drain_acks(self):
        """Move every mid on_publish has queued so far into _acked (its key was already pending) or _early_acks (publish()
        has not registered that mid yet). Touches no lock and makes no paho call."""
        while True:
            try:
                mid = self._acked_mids.get_nowait()
            except queue.Empty:
                return
            key = self._pending.pop(mid, None)
            if key is not None:
                self._acked.append(key)
            else:
                self._early_acks.add(mid)

    def _forget_inflight(self):
        """Nothing in flight can be trusted after a disconnect or a reset: the rows stay in the SQLite buffer and are sent again by
        the drain (at-least-once, doc 04). Safe to call from either thread: no lock, no paho call, and dict/set/queue operations are
        each atomic under the GIL, so the worst a race does is discard an entry that a disconnect should discard anyway."""
        self._pending.clear()
        self._early_acks.clear()
        while True:
            try:
                self._acked_mids.get_nowait()
            except queue.Empty:
                return
