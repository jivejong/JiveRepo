"""The probe's SQLite buffer (doc 04, Local buffer): the durability layer of doc 02.

Every reading is written here BEFORE it is published (one transaction per scan) and deleted only when its QoS 1 PUBACK arrives.
`payload` is the complete compact envelope as it will be published, so a replay is byte-identical and keeps event_time and mode.
The table is doc 04's schema verbatim. `scan_id` is NOT NULL there, so a housekeeping event (which has no scan_id) is stored with
the empty string; the payload keeps the real null.

Cap 100,000 rows (about 17 days of scans). On overflow the OLDEST READINGS are dropped and a `buffer_overflow` housekeeping event
takes one slot, so the loss is visible in the data. Housekeeping rows (scan_id '') are never dropped as "oldest": otherwise a later
overflow would erase the record of an earlier one. WAL mode and synchronous=FULL: about 200 commits a day at a scan a quarter hour.
"""
import sqlite3

SCHEMA = """CREATE TABLE IF NOT EXISTS buffered_events (
  event_id   TEXT PRIMARY KEY,
  event_time TEXT NOT NULL,
  scan_id    TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_time ON buffered_events(event_time);"""

DEFAULT_CAP = 100_000


class Overflow:
    """What one append had to drop to stay within the cap."""

    def __init__(self, dropped_ids, oldest_event_time, newest_event_time):
        self.dropped_ids = dropped_ids
        self.oldest_event_time, self.newest_event_time = oldest_event_time, newest_event_time

    @property
    def dropped(self):
        return len(self.dropped_ids)


class Buffer:
    def __init__(self, path, cap=DEFAULT_CAP):
        if cap < 2:
            raise ValueError("the buffer cap must leave room for an overflow event")
        self.cap = cap
        self.db = sqlite3.connect(str(path), isolation_level=None)     # explicit transactions below
        if str(path) != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        for statement in SCHEMA.split(";"):
            if statement.strip():
                self.db.execute(statement)

    def depth(self):
        return self.db.execute("SELECT count(*) FROM buffered_events").fetchone()[0]

    def append(self, rows, created_at, make_overflow=None):
        """Write `rows` ((event_id, event_time, scan_id_or_None, payload_json)) in one transaction. If that would exceed the cap,
        drop the oldest rows first (by event_time) and, when `make_overflow(overflow)` is given, add the row it returns.
        Returns the Overflow, or None."""
        overflow = None
        self.db.execute("BEGIN IMMEDIATE")
        try:
            over = self.depth() + len(rows) - self.cap
            if over > 0 and make_overflow:
                over += 1                      # the overflow event takes one slot of its own
            if over > 0:
                dropped = self.db.execute("SELECT event_id, event_time FROM buffered_events WHERE scan_id != '' "
                                          "ORDER BY event_time, event_id LIMIT ?", (over,)).fetchall()
                self.db.executemany("DELETE FROM buffered_events WHERE event_id = ?", [(d[0],) for d in dropped])
                if dropped:
                    overflow = Overflow([d[0] for d in dropped], dropped[0][1], dropped[-1][1])
            all_rows = list(rows)
            if overflow is not None and make_overflow:
                all_rows.append(make_overflow(overflow))
            self.db.executemany("INSERT INTO buffered_events (event_id, event_time, scan_id, payload, created_at) "
                                "VALUES (?, ?, ?, ?, ?)",
                                [(r[0], r[1], r[2] or "", r[3], created_at) for r in all_rows])
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return overflow

    def batch(self, n, exclude=()):
        """Up to n (event_id, payload) rows, oldest event_time first, skipping the ids in `exclude` (rows already in flight)."""
        out = []
        for event_id, payload in self.db.execute("SELECT event_id, payload FROM buffered_events ORDER BY event_time, event_id"):
            if event_id in exclude:
                continue
            out.append((event_id, payload))
            if len(out) >= n:
                break
        return out

    def delete(self, event_ids):
        """Delete acknowledged rows in one transaction."""
        ids = [(i,) for i in event_ids]
        if not ids:
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.executemany("DELETE FROM buffered_events WHERE event_id = ?", ids)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()
