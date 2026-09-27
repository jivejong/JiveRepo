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
"""
import threading


class MqttPublisher:
    def __init__(self, host, port, client_id, username=None, password=None, control_topic=None, on_control=None,
                 keepalive=60, mqtt=None):
        if mqtt is None:
            import paho.mqtt.client as mqtt
        self._mqtt = mqtt
        self.host, self.port, self.keepalive = host, port, keepalive
        self.control_topic, self.on_control = control_topic, on_control
        self._lock = threading.RLock()
        self._connected, self.disconnect_reason = False, None
        self._pending, self._acked = {}, []
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
                return
            self._connected, self.disconnect_reason = True, None
        if self.control_topic:
            client.subscribe(self.control_topic, qos=1)

    def _on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        with self._lock:
            self._connected = False
            self.disconnect_reason = f"client_disconnected:{reason_code}"
            self._pending.clear()

    def _on_publish(self, client, userdata, mid, reason_code=None, properties=None):
        with self._lock:
            key = self._pending.pop(mid, None)
            if key is not None:
                self._acked.append(key)

    def _on_message(self, client, userdata, message):
        if self.on_control is not None:
            self.on_control(message.payload)

    # ---- the runtime's interface -------------------------------------------------------------------------
    @property
    def connected(self):
        with self._lock:
            return self._connected

    def publish(self, topic, payload, key):
        # The lock is held across the paho call: a PUBACK handled on paho's network thread before this method has recorded the message id
        # would otherwise find nothing pending, the row would never be confirmed, and it would be sent again. (RLock: paho may call back
        # on this thread.)
        with self._lock:
            if not self._connected:
                return False
            info = self.client.publish(topic, payload, qos=1)
            if info.rc != self._mqtt.MQTT_ERR_SUCCESS:
                return False
            self._pending[info.mid] = key
            return True

    def acked(self):
        with self._lock:
            out, self._acked = self._acked, []
            return out

    def reset(self):
        with self._lock:
            self._connected, self.disconnect_reason = False, "publish_unacked"
            self._pending.clear()
        self.client.disconnect()
        self.client.connect_async(self.host, self.port, self.keepalive)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()
