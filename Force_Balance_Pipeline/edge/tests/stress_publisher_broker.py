"""Stress test for edge/probe/publisher.py against a REAL paho-mqtt client and a real broker — not offline, and not part of the
test_*.py sweep (needs Docker; kept out of that glob on purpose). Reproduces, at full scale, the deadlock py-spy found live on the
Pi: paho's own client.publish() holds paho's internal _out_message_mutex for the whole call, and paho's network thread holds that
same mutex while it runs on_publish (_handle_pubackcomp -> _do_on_publish). A fake client (test_probe_publisher.py) cannot
reproduce this: it has no internal lock of its own to invert against.

Measured empirically (this file's history): 3,000 publishes sent as 6 batches of 500, waiting for each batch to fully acknowledge
before the next, did NOT reproduce the deadlock — waiting between batches gives paho's network thread time to finish processing
each PUBACK before the next publish() call, closing the collision window. A tight, unthrottled loop of 50,000 reproduced it almost
immediately (the process never even flushed its first print). This test fires a large unthrottled run for that reason, sliced into
chunks of 500 (doc 04's drain batch size) only for bookkeeping, with no wait between chunks — the opposite of throttling is what
makes the race likely, which is exactly why it is the realistic case to test.

Starts a throwaway, anonymous Mosquitto container on 127.0.0.1:11883 — the committed local-only config (mosquitto.conf;
allow_anonymous true, loopback only), never the Phase 3 LAN broker (force-mosquitto) — and stops it whether the test passes,
fails, or is interrupted.

Run directly, with a hard wall-clock timeout from the outside (this process can genuinely hang if the bug is back — that IS the
point of the mutation check; the fix is not expected to come anywhere close):
    timeout 120 python edge/tests/stress_publisher_broker.py         # bash
"""
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from probe.publisher import MqttPublisher  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PORT = 11883
CONTAINER = "force-probe-stress-broker"
TOTAL, CHUNK = 30_000, 500                      # CHUNK matches doc 04's drain batch size; no wait is inserted between chunks


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def broker_up():
    docker("rm", "-f", CONTAINER)                # in case a previous run was killed before cleanup
    r = docker("run", "-d", "--rm", "--name", CONTAINER, "-p", f"127.0.0.1:{PORT}:1883",
              "-v", f"{ROOT / 'infra' / 'mosquitto'}:/mosquitto/config:ro",
              "eclipse-mosquitto:2", "mosquitto", "-c", "/mosquitto/config/mosquitto.conf")
    if r.returncode != 0:
        raise RuntimeError(f"docker run failed: {r.stderr}")


def broker_down():
    docker("stop", CONTAINER)


def wait_for_broker(timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("the throwaway broker never opened its port")


class RealBrokerStressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        broker_up()
        cls.addClassCleanup(broker_down)
        wait_for_broker()

    def test_an_unthrottled_run_in_500_row_chunks_is_all_acked_exactly_once(self):
        p = MqttPublisher(host="127.0.0.1", port=PORT, client_id="stress-probe-01")
        p.start()
        self.addCleanup(p.close)
        deadline = time.time() + 10.0
        while not p.connected:
            self.assertLess(time.time(), deadline, "never connected to the throwaway broker")
            time.sleep(0.02)

        keys = [f"E{i:06d}" for i in range(TOTAL)]
        sent = []
        for start in range(0, TOTAL, CHUNK):                        # doc 04's batch size, sending, no synchronisation between chunks
            chunk = keys[start:start + CHUNK]
            for k in chunk:
                self.assertTrue(p.publish("force/telemetry/stress-probe-01", k.encode(), k), f"publish refused for {k}")
            sent.extend(chunk)

        acked, ack_deadline = [], time.time() + 60.0
        while len(acked) < TOTAL:
            acked.extend(p.acked())
            self.assertLess(time.time(), ack_deadline,
                            f"only {len(acked)}/{TOTAL} acked in 60s after sending finished — a hang upstream is the deadlock back")
            time.sleep(0.01)

        self.assertEqual(sorted(acked), sorted(sent))
        self.assertEqual(len(acked), len(set(acked)), "a key was acknowledged more than once")
        self.assertEqual(len(acked), TOTAL)


if __name__ == "__main__":
    unittest.main()
