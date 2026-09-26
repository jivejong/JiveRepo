"""The backfill window and the rule that keeps synthetic and live readings apart (docs 04, 07).

The window is [start, end) with start = end - 90 days, one scan per 15-minute UTC boundary from start up to the
boundary before end (96 x 90 = 8,640 scans). `end` is the last 15-minute boundary BEFORE the earliest live probe
event already in bronze, not before generation time, so a synthetic reading and a live reading for probe-01 can
never share an event_time range. It is an explicit input, recorded in the manifest (to_manifest), and two checks
refuse a window that would overlap:

  * make_window / BackfillWindow.validate: `end` must be a boundary at or before the last boundary before the
    earliest live event, and the window's last synthetic event (the last scan plus the 0-3 s reading offsets)
    must be before that event;
  * OverlapGuard.check: the generator calls it for every synthetic event and it refuses any event_time at or
    after the earliest live event_time.

This module is only the window and the refusal. The rest of the backfill generator is built later. Standard
library only.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .constants import SCANS_PER_DAY
from .envelope import format_ts
from .probe import READING_SPACING_MS
from .sectors import EXPECTED_ROWS

INTERVAL = timedelta(minutes=15)   # the scan cycle (doc 04)
BACKFILL_DAYS = 90                 # doc 07
SCANS = BACKFILL_DAYS * SCANS_PER_DAY
MAX_READING_OFFSET = timedelta(milliseconds=READING_SPACING_MS * (EXPECTED_ROWS - 1))  # the last planet of a sweep

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MICROSECONDS = timedelta(microseconds=1)


class OverlapRefused(ValueError):
    """The backfill would overlap the live readings, or the window is not what the rule allows."""


def _utc(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OverlapRefused(f"{name} must be a timezone-aware datetime (UTC)")
    return value.astimezone(timezone.utc)


def last_boundary_before(moment, interval=INTERVAL):
    """The last interval boundary (UTC) strictly before `moment`."""
    moment = _utc(moment, "the moment")
    step = interval // _MICROSECONDS
    quotient, remainder = divmod((moment - _EPOCH) // _MICROSECONDS, step)
    return _EPOCH + timedelta(microseconds=(quotient if remainder else quotient - 1) * step)


def _on_boundary(moment, interval=INTERVAL):
    return ((moment - _EPOCH) // _MICROSECONDS) % (interval // _MICROSECONDS) == 0


@dataclass(frozen=True)
class BackfillWindow:
    end: datetime
    earliest_live: datetime

    @property
    def start(self):
        return self.end - timedelta(days=BACKFILL_DAYS)

    @property
    def last_scan(self):
        return self.end - INTERVAL

    @property
    def last_event_time(self):
        return self.last_scan + MAX_READING_OFFSET

    @property
    def latest_allowed_end(self):
        return last_boundary_before(self.earliest_live)

    def scan_times(self):
        return [self.start + i * INTERVAL for i in range(SCANS)]

    def validate(self):
        """Raise OverlapRefused unless the window is allowed. Returns self."""
        end, live = _utc(self.end, "the window end"), _utc(self.earliest_live, "the earliest live event time")
        if not _on_boundary(end):
            raise OverlapRefused(f"the window end {format_ts(end)} is not a 15-minute UTC boundary")
        if end > self.latest_allowed_end:
            raise OverlapRefused(
                f"the window end {format_ts(end)} is after {format_ts(self.latest_allowed_end)}, the last 15-minute "
                f"boundary before the earliest live event at {format_ts(live)}")
        if not self.last_event_time < live:
            raise OverlapRefused(
                f"the last synthetic event ({format_ts(self.last_event_time)}) is not before the earliest live event "
                f"({format_ts(live)})")
        return self

    def to_manifest(self):
        """The window as recorded in the backfill manifest (explicit inputs and what follows from them)."""
        return {
            "window_start_utc": format_ts(self.start),
            "window_end_utc": format_ts(self.end),
            "interval_seconds": int(INTERVAL.total_seconds()),
            "days": BACKFILL_DAYS,
            "scans": SCANS,
            "last_scan_utc": format_ts(self.last_scan),
            "last_synthetic_event_time_utc": format_ts(self.last_event_time),
            "earliest_live_event_time_utc": format_ts(self.earliest_live),
            "latest_allowed_window_end_utc": format_ts(self.latest_allowed_end),
            "overlap_rule": "no synthetic event_time at or after earliest_live_event_time_utc",
        }


def make_window(earliest_live, end=None):
    """The backfill window for a given earliest live event time. `end` defaults to the last 15-minute boundary
    before it; an explicit `end` is accepted only at or before that boundary. Raises OverlapRefused otherwise,
    and when no live event time is given at all (the rule cannot be applied without one)."""
    if earliest_live is None:
        raise OverlapRefused("no earliest live event time was given; the window end is defined relative to it")
    earliest_live = _utc(earliest_live, "the earliest live event time")
    end = last_boundary_before(earliest_live) if end is None else _utc(end, "the window end")
    return BackfillWindow(end=end, earliest_live=earliest_live).validate()


class OverlapGuard:
    """Refuses any synthetic event_time at or after the earliest live event_time. Call check() per event."""

    def __init__(self, earliest_live):
        if earliest_live is None:
            raise OverlapRefused("no earliest live event time was given; the guard cannot be applied")
        self.earliest_live = _utc(earliest_live, "the earliest live event time")
        self.checked = 0

    def check(self, event_time):
        event_time = _utc(event_time, "the synthetic event_time")
        if event_time >= self.earliest_live:
            raise OverlapRefused(f"synthetic event_time {format_ts(event_time)} is at or after the earliest live "
                                 f"event_time {format_ts(self.earliest_live)}")
        self.checked += 1
