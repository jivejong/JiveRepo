"""The mode controller (doc 04, The four modes): CONNECTED, DISCONNECTED, BURST, STEALTH, and every transition logged.

One mode at a time, decided each tick from three inputs:
  * what is REQUESTED: an operator override (control topic), else the schedule, else CONNECTED;
  * whether the broker is reachable (`link_up`), which the runtime derives from the MQTT client and from unacknowledged publishes;
  * the buffer backlog.

  requested DISCONNECTED (schedule/operator) or link lost   -> offline
  offline, requested STEALTH                                -> STEALTH (keeps its label and hourly cadence, buffers)
  offline otherwise                                         -> DISCONNECTED
  back online with a backlog                                -> BURST until the buffer is empty, then the requested base mode
  otherwise                                                 -> the requested base mode (CONNECTED or STEALTH)

The schedule (doc 04: DISCONNECTED 1-3 hours, roughly twice a day) is a simulated outage and is OFF unless a ModeSchedule is given.
Every change of (mode, offline) is appended to mode_transitions.jsonl: ts_utc, from, to, offline, reason, backlog. That file, not the
wall clock, is the proof of an outage (doc 07, Phase 3 checkpoint).
"""
import json
import random
from pathlib import Path

from .clock import iso

CONNECTED, DISCONNECTED, BURST, STEALTH = "CONNECTED", "DISCONNECTED", "BURST", "STEALTH"
FORCEABLE = (CONNECTED, DISCONNECTED, STEALTH)
DAY = 86400


class ModeSchedule:
    """Simulated outages: `per_day` DISCONNECTED windows a day, each between min_hours and max_hours long, one in each half of
    the UTC day. Deterministic for a seed; without a seed each instance draws its own."""

    def __init__(self, seed=None, per_day=2, min_hours=1.0, max_hours=3.0):
        self.seed = seed if seed is not None else random.SystemRandom().getrandbits(32)
        self.per_day, self.min_hours, self.max_hours = per_day, min_hours, max_hours

    def windows_for_day(self, day):
        rng = random.Random(f"{self.seed}:{day}")
        slot = DAY / self.per_day
        out = []
        for i in range(self.per_day):
            length = rng.uniform(self.min_hours, self.max_hours) * 3600
            start = day * DAY + i * slot + rng.uniform(0, slot - length)
            out.append((start, start + length, DISCONNECTED))
        return out

    def mode_at(self, epoch):
        for start, end, mode in self.windows_for_day(int(epoch // DAY)):
            if start <= epoch < end:
                return mode
        return None


class ModeController:
    def __init__(self, log_path, schedule=None, now=0.0, backlog=0):
        self.log_path = Path(log_path)
        self.schedule = schedule
        self.override = None                 # (mode, until_epoch or None)
        self.mode, self.base, self.offline = CONNECTED, CONNECTED, False
        self.draining = backlog > 0          # rows left from before a restart are drained like any backlog
        self._drain_reason = "startup_backlog" if backlog > 0 else "link_restored"
        self._forced_by = None               # "operator" or "schedule" while a requested outage is what keeps the probe offline
        self._last_source = "default"        # who asked for the mode in force on the previous tick
        self._record(now, None, "startup", backlog)

    def force(self, mode, until=None):
        if mode not in FORCEABLE:
            raise ValueError(f"a mode can be forced to one of {FORCEABLE}, not {mode!r}")
        self.override = (mode, until)

    def _requested(self, now):
        if self.override is not None:
            mode, until = self.override
            if until is None or now < until:
                return mode, "operator"
            self.override = None
        if self.schedule is not None:
            scheduled = self.schedule.mode_at(now)
            if scheduled:
                return scheduled, "schedule"
        return CONNECTED, "default"

    def update(self, now, link_up, backlog, link_reason=None):
        requested, source = self._requested(now)
        forced_offline = requested == DISCONNECTED
        base = STEALTH if requested == STEALTH else CONNECTED
        offline = forced_offline or not link_up
        ended = f"{self._forced_by}_ended" if self._forced_by else "link_restored"
        if self.offline and not offline and backlog > 0:
            self.draining = True
            self._drain_reason = ended
        drain_done = False
        if not offline and self.draining and backlog == 0:
            self.draining, drain_done = False, True
        if offline:
            mode = STEALTH if base == STEALTH else DISCONNECTED
        elif self.draining:
            mode = BURST
        else:
            mode = base
        if (mode, offline) != (self.mode, self.offline):
            if offline and not self.offline:
                reason = source if forced_offline else (link_reason or "link_lost")
            elif mode == BURST:
                reason = self._drain_reason
            elif drain_done or self.mode == BURST:
                reason = "drain_complete"
            elif self.offline and not offline:
                reason = ended
            elif source == "default" and self._last_source != "default":
                reason = f"{self._last_source}_ended"         # a forced STEALTH (or a schedule window) has run out
            else:
                reason = source
            self._record(now, mode, reason, backlog, offline)
        self._last_source = source
        self._forced_by = source if forced_offline else None
        self.mode, self.base, self.offline = mode, base, offline
        return mode

    def _record(self, now, to, reason, backlog, offline=None):
        entry = {"ts_utc": iso(now), "from": None if to is None else self.mode, "to": to or self.mode,
                 "offline": self.offline if offline is None else offline, "reason": reason, "backlog": backlog}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8", newline=chr(10)) as f:
            f.write(json.dumps(entry, separators=(",", ":")) + chr(10))
