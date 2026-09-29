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
Every change of (mode, offline) is appended to mode_transitions.jsonl: ts_utc, from, to, offline, reason, backlog (also printed to
stderr/journald, one line per transition). That file, not the wall clock, is the proof of an outage (doc 07, Phase 3 checkpoint).

Before the probe's very first successful connect, link_up is False for a reason that is not a link loss: there has never been a
link to lose. update() treats that specifically (found live: the broker log showed one continuous connection with no disconnect
near a restart that mode_transitions.jsonl nonetheless logged as "link_lost") — nothing is recorded while waiting for it, so
CONNECTED (the constructed default) is never read as the `from` of a transition before on_connect has ever fired, and the first
real connect goes straight to BURST with reason startup_backlog if there is one, never through a false DISCONNECTED step. A
forced DISCONNECTED (operator or schedule) is honoured even during that wait, since it has nothing to do with the link.

A boot with no broker is different from a slow connect. When the runtime says the wait has run past its own real-loss detection window
(first_connect_overdue; the runtime passes its ack_timeout, so this adds no number of its own), the probe is genuinely offline: it
records DISCONNECTED with reason no_initial_connect and buffers and labels scans as in any outage, and the first connect that follows
drains the backlog through BURST. A connect that arrives inside the window records nothing at all.

Nothing is WRITTEN, either, before the clock is confirmed synced (clock_synced, from the runtime's own timedatectl check; a Pi has
no RTC, and fake-hwclock can hand the process a stale time before NTP corrects it -- found live: a "startup" entry stamped before
the boot it belongs to). The mode still updates normally in memory throughout, so buffering and publishing stay correct; only the
disk write (and the stderr line) waits. The first update() call with clock_synced=True writes exactly one snapshot -- "startup" if
nothing happened in the meantime, or "clock_synced" if something did, with suppressed_transitions (how many) and, if any of them
entered DISCONNECTED, disconnected_before_sync (its reason) and disconnected_before_sync_uptime_s (when, by uptime -- the runtime's
monotonic clock, never the wall clock that was not yet trustworthy) -- so a boot with the broker down is never silently hidden by
a boot with a merely-slow clock. Ordinary transition logging resumes right after.
"""
import json
import random
import sys
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
        self._ever_connected = False         # the probe has not yet completed its first real MQTT connect
        self._deferred_write_done = False    # nothing is written until the clock is confirmed synced at least once
        self._suppressed_transitions = 0     # how many (mode, offline) changes happened before that
        self._disconnected_before_sync = None  # (reason, uptime_s) of the first one that entered DISCONNECTED, if any

    @property
    def waiting_for_first_connect(self):
        return not self._ever_connected

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

    def update(self, now, link_up, backlog, link_reason=None, first_connect_overdue=False,
               clock_synced=True, uptime_s=None):
        if not self._deferred_write_done and clock_synced and self._suppressed_transitions == 0 \
                and self._disconnected_before_sync is None:
            # the clock was already synced when this, the first call, arrived: nothing was ever suppressed, so this is
            # exactly what __init__ used to do -- write "startup" now, for the state as constructed, then fall through
            # to this same call's own normal processing below, unchanged.
            self._deferred_write_done = True
            self._record(now, None, "startup", backlog)

        requested, source = self._requested(now)
        forced_offline = requested == DISCONNECTED
        first_connect = link_up and not self._ever_connected
        if link_up:
            self._ever_connected = True
        elif not self._ever_connected and not forced_offline and not first_connect_overdue:
            return self.mode              # waiting for the first connect (see the module docstring): not a link loss
        base = STEALTH if requested == STEALTH else CONNECTED
        offline = forced_offline or not link_up
        ended = f"{self._forced_by}_ended" if self._forced_by else "link_restored"
        if self.offline and not offline and backlog > 0:
            self.draining = True
            self._drain_reason = ended
        elif first_connect and not offline and backlog > 0 and not self.draining:
            self.draining = True              # rows buffered while the very first connect was still pending (never offline)
            self._drain_reason = "startup_backlog"
        drain_done = False
        if not offline and self.draining and backlog == 0:
            self.draining, drain_done = False, True
        if offline:
            mode = STEALTH if base == STEALTH else DISCONNECTED
        elif self.draining:
            mode = BURST
        else:
            mode = base
        changed = (mode, offline) != (self.mode, self.offline)
        reason = None
        if changed:
            if offline and not self.offline:
                if forced_offline:
                    reason = source
                elif not self._ever_connected:
                    reason = "no_initial_connect"     # the wait for the first connect ran past the window
                else:
                    reason = link_reason or "link_lost"
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

        if not self._deferred_write_done:
            if not clock_synced:
                # still unsynced: update state in memory (buffering/publishing must stay correct) but write nothing
                if changed:
                    self._suppressed_transitions += 1
                    if offline and not self.offline and self._disconnected_before_sync is None:
                        self._disconnected_before_sync = (reason, uptime_s)
                self._last_source = source
                self._forced_by = source if forced_offline else None
                self.mode, self.base, self.offline = mode, base, offline
                return mode
            # clock_synced is True here and something WAS suppressed earlier (the top-of-function branch already
            # handled the "nothing ever suppressed" case) -- write one combined snapshot of where we ended up instead
            # of this tick's own (possibly misleading, mid-outage) transition.
            self._deferred_write_done = True
            self.mode, self.base, self.offline = mode, base, offline
            extra = {"suppressed_transitions": self._suppressed_transitions}
            if self._disconnected_before_sync is not None:
                d_reason, d_uptime = self._disconnected_before_sync
                extra["disconnected_before_sync"] = d_reason
                extra["disconnected_before_sync_uptime_s"] = d_uptime
            self._record(now, None, "clock_synced", backlog, offline, extra=extra)
            self._last_source = source
            self._forced_by = source if forced_offline else None
            return mode

        if changed:
            self._record(now, mode, reason, backlog, offline)
        self._last_source = source
        self._forced_by = source if forced_offline else None
        self.mode, self.base, self.offline = mode, base, offline
        return mode

    def _record(self, now, to, reason, backlog, offline=None, extra=None):
        entry = {"ts_utc": iso(now), "from": None if to is None else self.mode, "to": to or self.mode,
                 "offline": self.offline if offline is None else offline, "reason": reason, "backlog": backlog}
        if extra:
            entry.update(extra)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8", newline=chr(10)) as f:
            f.write(json.dumps(entry, separators=(",", ":")) + chr(10))
        print(f"probe: mode {entry['from']} -> {entry['to']} ({reason}, offline={entry['offline']}, backlog={backlog})",
             file=sys.stderr, flush=True)
