"""The probe runtime (doc 04): takes a scan on every boundary, writes it to the SQLite buffer before publishing, publishes it unless
offline, deletes rows only on PUBACK, and drains the backlog in batches of 500 when the broker returns.

`tick(now)` is the whole loop body and is deterministic: time, the publisher, the sync check and randomness are all passed in, so
a 45-minute outage runs in a test in a fraction of a second. In production a thread calls tick() about twice a second.

Per tick:  1 process PUBACKs (delete confirmed rows)  2 decide whether the broker is reachable  3 update the mode
           4 take any scan that is due  5 in BURST, send the next drain batch.

Cadence: scans align to :00/:15/:30/:45 UTC (interval 900 s), or to :00 in STEALTH (3600 s); the sweep is executed SCAN_DURATION
(3 s) after its boundary, when its last reading has been taken. A scan due before the clock is
synchronised, or not after the last one taken, is skipped and logged, never stamped (probe.clock).
Delivery is at-least-once: a batch that is not fully acknowledged stays buffered and is sent again, so a duplicate event_id is possible.

Every completed scan (not a skipped one, which probe.clock's own log already covers) prints one line to stderr, journald on the
Pi: "probe: scan <scan_id> (<mode>): <n> buffered, <m> published". Every drain batch (BURST) prints two: one when it is sent
(acked=0 so far) and one when it is fully acknowledged and deleted (acked equals sent), each with the buffer depth remaining at
that moment — so a stalled drain (a batch sent but never acked) is visible in the log rather than only inferred from silence.
"""
import json
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from forcesim.envelope import housekeeping_overflow_payload, make_envelope, new_ulid, ts_ms

from . import control
from .clock import boundary_after, boundary_at_or_after, iso
from .modes import BURST, CONNECTED, STEALTH

TOPIC_PREFIX = "force/telemetry"
DEFAULT_INTERVAL, DEFAULT_STEALTH_INTERVAL = 900, 3600
SCAN_DURATION = 3.0                 # the readings of a sweep are stamped 0-2.95 s after the boundary (forcesim); the scan is written
                                    # and published once the last one has been taken, so no event_time is ahead of the moment it is sent
DEFAULT_ACK_TIMEOUT = 30.0          # the publish-confirmation timeout of Phase 2 (probe_sim PUBLISH_TIMEOUT_S)
DEFAULT_DRAIN_BATCH = 500           # doc 04
DEFAULT_DRAIN_PAUSE = 10.0          # doc 04 (the backfill's model of a live drain)


def compact(envelope):
    return json.dumps(envelope, separators=(",", ":"))


class Runtime:
    def __init__(self, *, sim, sectors, buffer, publisher, gate, modes, faults, state_dir, source_id="probe-01",
                 interval=DEFAULT_INTERVAL, stealth_interval=DEFAULT_STEALTH_INTERVAL, ack_timeout=DEFAULT_ACK_TIMEOUT,
                 drain_batch=DEFAULT_DRAIN_BATCH, drain_pause=DEFAULT_DRAIN_PAUSE, id_rng=None, immediate=False):
        self.sim, self.sectors, self.buffer, self.publisher = sim, list(sectors), buffer, publisher
        self.gate, self.modes, self.faults = gate, modes, faults
        self.source_id, self.topic = source_id, f"{TOPIC_PREFIX}/{source_id}"
        self.interval, self.stealth_interval = interval, stealth_interval
        self.ack_timeout, self.drain_batch, self.drain_pause = ack_timeout, drain_batch, drain_pause
        self.events_path = Path(state_dir) / "probe_events.jsonl"
        self.id_rng, self.immediate = id_rng, immediate
        self.scan_index = 0
        self.next_scan, self._cadence_base = None, None
        self.inflight = {}                  # event_id -> time published, waiting for its PUBACK
        self.drain_ids, self.resume_at = set(), 0.0
        self.stats = {"scans": 0, "skipped": 0, "published": 0, "acked": 0, "overflows": 0}
        self.trace = deque(maxlen=5000)     # (time, text): what the tick did, for tests and the outage report

    # ---- the loop body --------------------------------------------------------------------------------
    def tick(self, now):
        self._process_acks(now)
        self._settle_drain_batch(now)
        link_up, reason = self._link(now)
        self.modes.update(now, link_up, self.buffer.depth(), reason)
        self._retime(now)
        while self.next_scan is not None and now >= self.next_scan + SCAN_DURATION:
            boundary = self.next_scan
            self._scan(boundary, now)
            self.next_scan = boundary_after(boundary, self._interval())
        if self.modes.mode == BURST and not self.modes.offline:
            self._drain(now)

    def _interval(self):
        return self.stealth_interval if self.modes.base == STEALTH else self.interval

    def _retime(self, now):
        """(Re)compute the next scan when the cadence changes (CONNECTED <-> STEALTH) and at the first tick."""
        if self.modes.base != self._cadence_base:
            first = self._cadence_base is None
            self._cadence_base = self.modes.base
            interval = self._interval()
            self.next_scan = (now // interval * interval if first and self.immediate
                              else boundary_at_or_after(now, interval))

    # ---- acknowledgements and the link ------------------------------------------------------------------
    def _process_acks(self, now):
        acked = [k for k in self.publisher.acked()]
        if acked:
            self.buffer.delete(acked)
            for key in acked:
                self.inflight.pop(key, None)
            self.stats["acked"] += len(acked)

    def _link(self, now):
        if not self.publisher.connected:
            self._link_down()
            return False, "link_lost:" + (self.publisher.disconnect_reason or "client_disconnected")
        if self.inflight and now - min(self.inflight.values()) > self.ack_timeout:
            self.publisher.reset()
            self._link_down()
            return False, "link_lost:publish_unacked"
        return True, None

    def _link_down(self):
        """Nothing in flight can be trusted any more: the rows stay in the buffer and are sent again after the reconnect."""
        self.inflight.clear()
        self.drain_ids = set()

    # ---- taking a scan ---------------------------------------------------------------------------------
    def _scan(self, boundary, now):
        refused = self.gate.check(boundary)
        if refused:
            self.stats["skipped"] += 1
            self._event(now, "scan_skipped", reason=refused, scan_time=iso(boundary))
            self.trace.append((now, f"scan {iso(boundary)} skipped: {refused}"))
            return
        mode = self.modes.mode
        envelopes = self.sim.sweep(self.scan_index, datetime.fromtimestamp(boundary, timezone.utc), mode=mode)
        self.scan_index += 1
        if mode == CONNECTED and self.faults is not None:
            for envelope in envelopes:
                self.faults.apply(envelope, iso(now))          # logged before the row is written
        rows = [(e["event_id"], e["event_time"], e["scan_id"], compact(e)) for e in envelopes]
        overflow_rows = []

        def make_overflow(overflow):
            event = make_envelope(event_id=new_ulid(ts_ms(datetime.fromtimestamp(now, timezone.utc)), self.id_rng),
                                  source_id=self.source_id, source_type="probe",
                                  event_time=datetime.fromtimestamp(now, timezone.utc), mode=mode, scan_id=None,
                                  sector_id=self.source_id,
                                  payload=housekeeping_overflow_payload(overflow.dropped, overflow.oldest_event_time,
                                                                        overflow.newest_event_time, self.buffer.cap))
            row = (event["event_id"], event["event_time"], None, compact(event))
            overflow_rows.append(row)
            return row

        overflow = self.buffer.append(rows, iso(now), make_overflow)
        self.gate.record(boundary)
        self.stats["scans"] += 1
        if overflow is not None:
            self.stats["overflows"] += 1
            for dropped in overflow.dropped_ids:
                self.inflight.pop(dropped, None)
            self._event(now, "buffer_overflow", dropped=overflow.dropped, oldest=overflow.oldest_event_time,
                        newest=overflow.newest_event_time)
        sending = rows + overflow_rows
        published = 0
        if not self.modes.offline:
            published = self._publish(sending, now)
        self.trace.append((now, f"scan {iso(boundary)} taken as {mode}: {len(rows)} readings buffered, {published} published"))
        print(f"probe: scan {envelopes[0]['scan_id']} ({mode}): {len(rows)} buffered, {published} published",
             file=sys.stderr, flush=True)

    def _publish(self, rows, now):
        sent = 0
        for event_id, _, _, payload in rows:
            if self.publisher.publish(self.topic, payload.encode("utf-8"), event_id):
                self.inflight[event_id] = now
                sent += 1
        self.stats["published"] += sent
        return sent

    # ---- draining --------------------------------------------------------------------------------------
    def _settle_drain_batch(self, now):
        """If the batch currently in flight has just been fully acknowledged, log it and clear it, freeing the next tick to send
        more. Called every tick, unconditionally: modes.update() (right after this) can move the mode out of BURST the instant
        the backlog it is given reaches 0, and _drain() below only ever runs while mode == BURST — so without this, the very
        last batch of a drain would never be settled or logged, though the buffer and mode would still end up correct."""
        if self.drain_ids and not any(i in self.inflight for i in self.drain_ids):
            acked_n = len(self.drain_ids)
            self.trace.append((now, f"drain batch of {acked_n} acknowledged and deleted; pausing {self.drain_pause:g} s"))
            print(f"probe: drain batch sent={acked_n} acked={acked_n} remaining_buffered={self.buffer.depth()}",
                 file=sys.stderr, flush=True)
            self.drain_ids, self.resume_at = set(), now + self.drain_pause

    def _drain(self, now):
        if self.drain_ids:
            return                                              # still waiting on _settle_drain_batch to resolve it
        if now < self.resume_at:
            return
        rows = self.buffer.batch(self.drain_batch, exclude=self.inflight)
        if not rows:
            return
        batch = [(event_id, None, None, payload) for event_id, payload in rows]
        sent = self._publish(batch, now)
        self.drain_ids = {event_id for event_id, *_ in batch if event_id in self.inflight}
        self.trace.append((now, f"drain batch of {sent} published (oldest first, {self.buffer.depth()} buffered)"))
        print(f"probe: drain batch sent={sent} acked=0 remaining_buffered={self.buffer.depth()}", file=sys.stderr, flush=True)

    # ---- control messages ------------------------------------------------------------------------------
    def on_control(self, payload, now):
        """Apply one control message; a bad one is logged and ignored, never raised into the loop."""
        try:
            command = control.parse(payload, self.sectors)
            if command["kind"] == "inject":
                self.sim.inject(command["sector_id"], command["signature"], self.scan_index, command["ramp"],
                                command["hold"], command["decay"])
            else:
                until = None if command["for_seconds"] is None else now + command["for_seconds"]
                self.modes.force(command["mode"], until)
        except (control.ControlError, ValueError) as e:
            self._event(now, "control_rejected", reason=str(e))
            self.trace.append((now, f"control message rejected: {e}"))
            return False
        self._event(now, "control_applied", **command)
        self.trace.append((now, f"control message applied: {command}"))
        return True

    def _event(self, now, event, **fields):
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline=chr(10)) as f:
            f.write(json.dumps({"ts_utc": iso(now), "event": event, **fields}, separators=(",", ":")) + chr(10))
