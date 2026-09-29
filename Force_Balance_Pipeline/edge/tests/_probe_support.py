"""Test rig for the probe runtime: a fake clock, a fake broker link with acknowledgements and detection lag, and a Rig that builds a
Runtime on a temporary state directory and drives it tick by tick. Nothing here touches the network or the real clock."""
import json
import random
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

EDGE = Path(__file__).resolve().parents[1]
ROOT = EDGE.parent
sys.path.insert(0, str(EDGE))
sys.path.insert(0, str(ROOT / "ingest" / "bridge"))

from forcesim.probe import SimProbe  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402
from probe.buffer import Buffer  # noqa: E402
from probe.clock import ClockGate, iso  # noqa: E402
from probe.faults import FaultInjector  # noqa: E402
from probe.modes import ModeController  # noqa: E402
from probe.runtime import Runtime  # noqa: E402

SECTORS = load_sectors()


def epoch(text):
    """'2026-09-27T12:00:00' (UTC) as epoch seconds."""
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class FakePublisher:
    """The runtime's publisher interface over a scripted link. `link_up` is the physical network; `connected` is what the client
    believes. Cutting the link is noticed after `detect_after` seconds (a keepalive: 60 s x 1.5 = 90 s in real life) or never
    (None), in which case only the runtime's own acknowledgement timeout finds out. A publish while the link is cut is lost."""

    def __init__(self, latency=0.2, detect_after=90.0, reconnect_delay=1.0):
        self.now = 0.0
        self.latency, self.detect_after, self.reconnect_delay = latency, detect_after, reconnect_delay
        self.link_up, self.connected, self.disconnect_reason = True, True, None
        self.received = []                    # (arrival_time, event_id, envelope)
        self.batches = []                     # (time, [event_id, ...]) one entry per publish call burst is not tracked: see arrivals
        self._acks = []                       # (ready_time, key)
        self._cut_at = None
        self._reconnect_at = None
        self.publishes_lost = 0
        self.resets = 0
        self.hook = None                      # called with (key, payload) just before each publish (write-ahead checks)
        self.hold_acks = False                # True: PUBACKs are withheld until release()

    # -- the runtime's interface --
    def publish(self, topic, payload, key):
        if not self.connected:
            return False
        if self.hook is not None:
            self.hook(key, payload)
        if self.link_up:
            self.received.append((self.now, key, json.loads(payload)))
            self._acks.append((self.now + self.latency, key))
        else:
            self.publishes_lost += 1
        return True

    def acked(self):
        due = lambda t: t <= self.now and (not self.hold_acks or t < 0)      # noqa: E731  (released acks have t < 0)
        ready = [k for t, k in self._acks if due(t)]
        self._acks = [(t, k) for t, k in self._acks if not due(t)]
        return ready

    def release(self, n=None):
        """Let the oldest n withheld PUBACKs (all when None) through on the next tick."""
        keys = [k for _, k in self._acks]
        n = len(keys) if n is None else n
        self._acks = [(-1.0, k) for k in keys[:n]] + self._acks[n:]

    def reset(self):
        self.resets += 1
        self.connected, self.disconnect_reason = False, "publish_unacked"
        self._acks.clear()
        self._reconnect_at = self.now + self.reconnect_delay

    def close(self):
        pass

    # -- the scenario's controls --
    def cut(self):
        self.link_up, self._cut_at = False, self.now

    def restore(self):
        self.link_up, self._cut_at = True, None
        if not self.connected:
            self._reconnect_at = self.now + self.reconnect_delay

    def step(self, now):
        self.now = now
        if not self.link_up and self.connected and self.detect_after is not None and now - self._cut_at >= self.detect_after:
            self.connected, self.disconnect_reason = False, "client_disconnected:keepalive"
            self._acks.clear()
        if self.link_up and not self.connected and self._reconnect_at is not None and now >= self._reconnect_at:
            self.connected, self.disconnect_reason, self._reconnect_at = True, None, None


class Rig:
    """A Runtime on a temporary state directory, driven on a fake clock."""

    def __init__(self, start="2026-09-27T12:00:00", state_dir=None, seed=7, fault_rate=0.0, cap=100_000, synced=True,
                 publisher=None, drain_batch=500, drain_pause=10.0, immediate=False, schedule=None):
        self.tmp = None
        if state_dir is None:
            self.tmp = Path(tempfile.mkdtemp(prefix="probe-rig-"))
            state_dir = self.tmp
        self.state = Path(state_dir)
        self.now = epoch(start)
        self.synced = [synced]
        self.publisher = publisher or FakePublisher()
        self.publisher.now = self.now
        self.seed, self.fault_rate, self.cap = seed, fault_rate, cap
        self.drain_batch, self.drain_pause, self.immediate, self.schedule = drain_batch, drain_pause, immediate, schedule
        self.build()

    def build(self):
        """(Re)build the runtime from the state directory: a restart after a crash reuses buffer.db and the logs."""
        self.buffer = Buffer(self.state / "buffer.db", cap=self.cap)
        self.modes = ModeController(self.state / "mode_transitions.jsonl", self.schedule, now=self.now, backlog=self.buffer.depth())
        self.gate = ClockGate(lambda: self.synced[0], self.state / "last_scan.json")
        self.faults = FaultInjector(random.Random(self.seed), [s.sector_id for s in SECTORS],
                                    self.state / "fault_injection.jsonl", rate=self.fault_rate)
        self.sim = SimProbe(SECTORS, seed=self.seed)
        monotonic_origin = self.now       # a fresh process's monotonic clock starts at 0 relative to whenever it started
        self.runtime = Runtime(sim=self.sim, sectors=SECTORS, buffer=self.buffer, publisher=self.publisher, gate=self.gate,
                               modes=self.modes, faults=self.faults, state_dir=self.state, interval=900,
                               drain_batch=self.drain_batch, drain_pause=self.drain_pause, id_rng=random.Random(self.seed + 1),
                               immediate=self.immediate, monotonic=lambda: self.now - monotonic_origin)

    def crash(self):
        """The process dies: everything in memory is gone, buffer.db and the logs stay. The in-flight publishes are unknown."""
        self.history = getattr(self, "history", []) + list(self.runtime.trace) + [(self.now, "PROCESS DIED: buffer.db and the logs stay")]
        self.faults.close()
        self.buffer.close()
        self.publisher._acks.clear()
        self.publisher.connected = self.publisher.link_up
        self.build()

    def run(self, seconds, step=1.0, at=()):
        """Advance `seconds` in `step` ticks; `at` is [(seconds_from_now, callable)] scenario events fired at their tick."""
        pending = sorted(at, key=lambda item: item[0])
        end = self.now + seconds
        origin = self.now
        while self.now < end - 1e-9:
            self.now += step
            self.publisher.now = self.now                      # a scenario event (cut, restore) happens at this tick's time
            while pending and origin + pending[0][0] <= self.now + 1e-9:
                pending.pop(0)[1]()
            self.publisher.step(self.now)
            self.runtime.tick(self.now)

    def run_until(self, when, **kw):
        self.run(epoch(when) - self.now, **kw)

    def transitions(self):
        path = self.state / "mode_transitions.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def events(self):
        path = self.state / "probe_events.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def faults_logged(self):
        path = self.state / "fault_injection.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def received(self):
        return self.publisher.received

    def cleanup(self):
        self.faults.close()
        self.buffer.close()
        if self.tmp is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)


def scan_ids(received):
    return sorted({e["scan_id"] for _, _, e in received if e.get("scan_id")})


def time_of(text):
    return epoch(text)


__all__ = ["Rig", "FakePublisher", "epoch", "iso", "scan_ids", "SECTORS"]
