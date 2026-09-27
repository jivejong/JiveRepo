"""The probe's SQLite buffer (doc 04, Local buffer): the schema is the doc's, rows come out oldest event_time first, deletion is
explicit, the cap drops the OLDEST rows and adds one buffer_overflow event, a failed write leaves nothing half done, and everything
survives a restart. Offline."""
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _probe_support import ROOT  # noqa: E402
from probe.buffer import DEFAULT_CAP, SCHEMA, Buffer  # noqa: E402

NOW = "2026-09-27T12:00:00.000Z"


def rows(n, start=0, scan="SCAN1"):
    """n rows with unique, increasing event_times (one second apart from 12:00:00) and ids EV00000..."""
    def stamp(k):
        return f"2026-09-27T12:{k // 60:02d}:{k % 60:02d}.000Z"
    return [(f"EV{start + i:05d}", stamp(start + i), scan, f'{{"n":{start + i}}}') for i in range(n)]


def normalise(sql):
    return re.sub(r"\s+", " ", sql).strip().rstrip(";")


class SchemaTests(unittest.TestCase):
    def test_the_schema_is_the_one_in_doc_04(self):
        doc = (ROOT / "docs" / "04-edge-simulators.md").read_text(encoding="utf-8")
        block = re.search(r"```sql\n(.*?)```", doc.split("### Local buffer")[1], re.S).group(1)
        self.assertEqual(normalise(SCHEMA), normalise(block))

    def test_the_default_cap_is_100000_rows_about_17_days_of_scans(self):
        self.assertEqual(DEFAULT_CAP, 100_000)
        self.assertAlmostEqual(DEFAULT_CAP / (60 * 96), 17.4, places=1)     # 60 readings x 96 scans a day

    def test_the_cap_must_leave_room_for_the_overflow_event(self):
        with self.assertRaises(ValueError):
            Buffer(":memory:", cap=1)


class BufferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="buffer-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "buffer.db"
        self.buffer = Buffer(self.path, cap=100)
        self.addCleanup(lambda: self.buffer.close() if self.buffer else None)

    def test_rows_come_out_oldest_event_time_first_whatever_the_insert_order(self):
        self.buffer.append([("B", "2026-09-27T12:30:00.000Z", "S", "b"), ("A", "2026-09-27T12:00:00.000Z", "S", "a")], NOW)
        self.buffer.append([("C", "2026-09-27T12:15:00.000Z", "S", "c")], NOW)
        self.assertEqual([r[0] for r in self.buffer.batch(10)], ["A", "C", "B"])

    def test_batch_size_and_exclusion(self):
        self.buffer.append(rows(10), NOW)
        self.assertEqual(len(self.buffer.batch(4)), 4)
        self.assertEqual([r[0] for r in self.buffer.batch(3, exclude={"EV00000", "EV00001"})], ["EV00002", "EV00003", "EV00004"])
        self.assertEqual(len(self.buffer.batch(50)), 10)

    def test_payload_is_stored_verbatim_and_a_null_scan_id_is_stored_as_the_empty_string(self):
        self.buffer.append([("H1", "2026-09-27T12:00:00.000Z", None, '{"kind":"buffer_overflow"}')], NOW)
        self.assertEqual(self.buffer.batch(1), [("H1", '{"kind":"buffer_overflow"}')])
        stored = sqlite3.connect(str(self.path)).execute("SELECT scan_id, created_at FROM buffered_events").fetchone()
        self.assertEqual(stored, ("", NOW))                     # scan_id is NOT NULL in doc 04's table

    def test_delete_removes_only_the_acknowledged_rows(self):
        self.buffer.append(rows(5), NOW)
        self.buffer.delete(["EV00001", "EV00003", "NOT-THERE"])
        self.assertEqual([r[0] for r in self.buffer.batch(10)], ["EV00000", "EV00002", "EV00004"])
        self.buffer.delete([])
        self.assertEqual(self.buffer.depth(), 3)

    def test_everything_survives_a_restart(self):
        self.buffer.append(rows(7), NOW)
        self.buffer.close()
        self.buffer = Buffer(self.path, cap=100)
        self.assertEqual(self.buffer.depth(), 7)
        self.assertEqual(self.buffer.batch(1)[0][0], "EV00000")

    def test_wal_and_synchronous_full(self):
        self.assertEqual(self.buffer.db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        self.assertEqual(self.buffer.db.execute("PRAGMA synchronous").fetchone()[0], 2)       # FULL

    def test_a_failed_append_leaves_nothing_behind(self):
        self.buffer.append(rows(3), NOW)
        with self.assertRaises(sqlite3.IntegrityError):
            self.buffer.append(rows(2, start=10) + rows(1, start=0), NOW)   # the last row repeats an event_id
        self.assertEqual(self.buffer.depth(), 3)
        self.buffer.append(rows(1, start=20), NOW)                          # and the buffer is still usable
        self.assertEqual(self.buffer.depth(), 4)


class CapTests(unittest.TestCase):
    def setUp(self):
        self.buffer = Buffer(":memory:", cap=100)
        self.addCleanup(self.buffer.close)

    def marker(self, overflow):
        return ("OVERFLOW", "2026-09-27T13:00:00.000Z", None, f'{{"kind":"buffer_overflow","dropped":{overflow.dropped}}}')

    def test_filling_exactly_to_the_cap_is_not_an_overflow(self):
        self.assertIsNone(self.buffer.append(rows(100), NOW, self.marker))
        self.assertEqual(self.buffer.depth(), 100)

    def test_one_over_drops_the_oldest_and_adds_the_overflow_event(self):
        self.buffer.append(rows(100), NOW)
        overflow = self.buffer.append(rows(1, start=100), NOW, self.marker)
        self.assertEqual(overflow.dropped, 2)                        # one for the new row, one for the overflow event
        self.assertEqual(overflow.dropped_ids, ["EV00000", "EV00001"])
        self.assertEqual(self.buffer.depth(), 100)                   # the cap is never exceeded
        ids = [r[0] for r in self.buffer.batch(200)]
        self.assertIn("OVERFLOW", ids)
        self.assertIn("EV00100", ids)
        self.assertNotIn("EV00000", ids)

    def test_the_oldest_by_event_time_are_dropped_not_the_oldest_inserted(self):
        self.buffer.append([("LATE", "2026-09-27T23:00:00.000Z", "S", "x")] + rows(99, start=1), NOW)   # inserted first, newest
        overflow = self.buffer.append(rows(1, start=200), NOW, self.marker)
        self.assertNotIn("LATE", overflow.dropped_ids)
        self.assertEqual(overflow.dropped_ids[0], "EV00001")

    def test_the_overflow_reports_the_range_of_what_was_dropped(self):
        self.buffer.append(rows(100), NOW)
        overflow = self.buffer.append(rows(30, start=100), NOW, self.marker)
        self.assertEqual(overflow.dropped, 31)
        self.assertEqual((overflow.oldest_event_time, overflow.newest_event_time),
                         ("2026-09-27T12:00:00.000Z", "2026-09-27T12:00:30.000Z"))

    def test_an_overflow_event_is_never_dropped_as_the_oldest_row_by_a_later_overflow(self):
        self.buffer.append(rows(100), NOW)
        first = self.buffer.append(rows(1, start=100), NOW, self.marker)
        self.assertEqual(first.dropped, 2)
        dropped_ids = []
        for start in range(200, 1200, 10):                              # many more overflows
            overflow = self.buffer.append(rows(10, start=start), NOW, lambda o: ("OVERFLOW%d" % start, "2026-09-27T13:00:00.000Z", None, "{}"))
            dropped_ids += overflow.dropped_ids
        self.assertNotIn("OVERFLOW", dropped_ids)
        self.assertFalse([d for d in dropped_ids if d.startswith("OVERFLOW")])
        ids = [r[0] for r in self.buffer.batch(10 ** 6)]
        self.assertIn("OVERFLOW", ids)
        self.assertEqual(len([i for i in ids if i.startswith("OVERFLOW")]), 101)

    def test_without_a_marker_callback_it_only_drops(self):
        self.buffer.append(rows(100), NOW)
        overflow = self.buffer.append(rows(5, start=100), NOW)
        self.assertEqual(overflow.dropped, 5)
        self.assertEqual(self.buffer.depth(), 100)


if __name__ == "__main__":
    unittest.main()
