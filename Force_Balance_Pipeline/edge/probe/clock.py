"""The clock gate (doc 04, Clock): the Pi 3 has no real-time clock, so event_time is trusted only once NTP has synchronised, and
never goes backwards.

  * A scan due before the clock is synchronised is SKIPPED, not stamped with a guessed time and not buffered. The caller logs it
    (`clock_unsynced`); in the data it is a gap.
  * The boundary of the last scan taken is kept in the state directory. A scan whose boundary is not after it is refused
    (`clock_backwards`), so a clock that steps backwards cannot re-stamp history.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

UNSYNCED, BACKWARDS = "clock_unsynced", "clock_backwards"


def timedatectl_synced():
    """True when systemd reports NTPSynchronized=yes. Anywhere timedatectl is missing (a laptop) it is False, which is the safe
    answer: run with --assume-clock-synced there, never on the Pi."""
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "yes"


def iso(epoch):
    """ISO 8601 UTC with milliseconds and a Z, as in doc 02."""
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def parse_iso(text):
    return datetime.fromisoformat(text[:-1] + "+00:00").timestamp()


def boundary_at_or_after(epoch, interval):
    """The first interval boundary (UTC) at or after `epoch`."""
    return -(-epoch // interval) * interval


def boundary_after(epoch, interval):
    """The first interval boundary strictly after `epoch`."""
    return (epoch // interval + 1) * interval


class ClockGate:
    def __init__(self, sync_check, state_path):
        self.sync_check = sync_check
        self.state_path = Path(state_path)
        self.last_boundary = None
        if self.state_path.exists():
            try:
                self.last_boundary = parse_iso(json.loads(self.state_path.read_text(encoding="utf-8"))["last_scan_boundary"])
            except (ValueError, KeyError, OSError):
                self.last_boundary = None      # an unreadable state file must not stop the probe; the sync check still applies

    def check(self, boundary):
        """None if a scan at `boundary` (epoch seconds) may be stamped, else the reason it may not."""
        if not self.sync_check():
            return UNSYNCED
        if self.last_boundary is not None and boundary <= self.last_boundary:
            return BACKWARDS
        return None

    def record(self, boundary):
        """Remember the boundary of the scan just taken (atomically)."""
        self.last_boundary = boundary
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(self.state_path.name + f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps({"last_scan_boundary": iso(boundary)}) + chr(10), encoding="utf-8")
        os.replace(tmp, self.state_path)
