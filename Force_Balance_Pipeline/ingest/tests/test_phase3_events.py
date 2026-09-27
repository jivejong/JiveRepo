"""What Phase 3 puts on the wire, through the collector bridge (docs 02 and 04): STEALTH readings with two nulls, injected faults (an unknown
sector, a null or out-of-range channel, an event_time in the future) and a buffer_overflow housekeeping event must be written EXACTLY as sent (the
bridge validates structure only and never repairs, reorders or drops), a scan whose planets include an unknown sector still completes at 60, and a
file flushed at 23:45:03 or 00:00:03 lands under the ingest hour and day, not the event's. Offline."""
import json
import random
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402
from forcesim.envelope import housekeeping_overflow_payload, make_envelope, new_ulid, stealth_payload, ts_ms  # noqa: E402
from forcesim.probe import SimProbe  # noqa: E402
from upload import UPLOADED  # noqa: E402

T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


def sweep(mode="CONNECTED", index=0):
    return SimProbe(S.SECTORS, seed=index + 1).sweep(index, T0, mode=mode)


def wire(envelope):
    return json.dumps(envelope, separators=(",", ":")).encode()


class Rig:
    """A Bridge over a scripted uploader with a controllable clock."""

    def __init__(self, start=1_790_000_000.0):
        self.clock = S.Clock(start)
        self.uploader = S.ScriptedUploader()
        self.dead = Path(tempfile.mkdtemp()) / "dead.ndjson"
        self.bridge = bridge.Bridge(self.uploader, dead_letter_path=self.dead, clock=self.clock.now)

    def feed(self, envelopes):
        batches = []
        for e in envelopes:
            batches += self.bridge.handle_message(wire(e) if isinstance(e, dict) else e)
        return batches

    def upload(self, batches):
        for b in batches:
            self.bridge.upload(b)


class Phase3ShapesTests(unittest.TestCase):
    def test_a_stealth_scan_is_a_complete_scan_and_is_written_unchanged(self):
        rig = Rig()
        envelopes = sweep("STEALTH")
        batches = rig.feed(envelopes)
        self.assertEqual(len(batches), 1)                                      # 60 planets: one complete scan, one file
        lines = batches[0].data.splitlines()
        self.assertEqual([json.loads(l) for l in lines], envelopes)
        self.assertIsNone(json.loads(lines[0])["payload"]["midichlorian_ppm"])
        self.assertNotIn(b"sensor_temp_c", batches[0].data)
        self.assertEqual(bridge.check_structure(envelopes[0]), [])

    def test_faulty_readings_pass_the_structure_check_and_are_written_untouched(self):
        envelopes = sweep()
        envelopes[3]["payload"]["kyber_resonance"] = None                      # a null channel
        envelopes[4]["payload"]["dark_side_activity"] = 140.0                  # out of range
        envelopes[5]["event_time"] = "2026-09-28T12:00:00.100Z"                # a day in the future
        envelopes[6]["sector_id"] = "unknown-1a2b3c4d"                         # not in dim_sector
        for e in envelopes:
            self.assertEqual(bridge.check_structure(e), [])
        rig = Rig()
        batches = rig.feed(envelopes)
        self.assertEqual(len(batches), 1, "the unknown sector is still a distinct sector_id, so the scan completes at 60")
        self.assertEqual([json.loads(l) for l in batches[0].data.splitlines()], envelopes)
        self.assertEqual(rig.bridge.stats["dead_lettered"], 0)                 # bad values are silver's to reject, not the bridge's

    def test_a_scan_with_two_unknown_sectors_still_completes_because_each_id_is_unique(self):
        envelopes = sweep()
        envelopes[1]["sector_id"], envelopes[2]["sector_id"] = "unknown-aaaa0001", "unknown-aaaa0002"
        self.assertEqual(len(Rig().feed(envelopes)), 1)

    def test_the_buffer_overflow_event_is_written_as_sent(self):
        event = make_envelope(event_id=new_ulid(ts_ms(T0), random.Random(1)), source_id="probe-01", source_type="probe", event_time=T0, mode="DISCONNECTED",
                              scan_id=None, sector_id="probe-01",
                              payload=housekeeping_overflow_payload(61, "2026-09-27T11:00:00.000Z", "2026-09-27T11:10:00.000Z", 100000))
        self.assertEqual(bridge.check_structure(event), [])
        rig = Rig()
        self.assertEqual(rig.feed([event]), [])                                # no scan_id: it waits for the 90 s timer like a report
        rig.clock.advance(91)
        batches = rig.bridge.tick()
        self.assertEqual(len(batches), 1)
        self.assertEqual(json.loads(batches[0].data), event)
        self.assertIsNone(json.loads(batches[0].data)["scan_id"])

    def test_replayed_and_live_events_are_never_reordered_or_deduplicated(self):
        rig = Rig()
        first = sweep(index=0)
        second = sweep(index=1)
        batches = rig.feed(second + first + first)                             # out of order, and a duplicate scan
        lines = [json.loads(l) for b in batches for l in b.data.splitlines()]
        self.assertEqual(lines, second + first + first)

    def test_a_malformed_event_is_dead_lettered_and_the_rest_still_flow(self):
        rig = Rig()
        envelopes = sweep()
        bad = dict(envelopes[0])
        del bad["event_id"]
        batches = rig.feed([bad] + envelopes[1:])
        self.assertEqual(rig.bridge.stats["dead_lettered"], 1)
        self.assertEqual(batches, [])                                          # 59 planets: not a complete scan yet


class PathTests(unittest.TestCase):
    """dt= and hh= are ingest time (doc 02), set when the batch is flushed."""

    def batch_for(self, epoch, envelopes=None):
        rig = Rig(start=epoch)
        return rig.feed(envelopes or sweep())[0]

    def test_a_scan_flushed_at_2345_03_lands_in_hour_23(self):
        b = self.batch_for(datetime(2026, 9, 27, 23, 45, 3, tzinfo=timezone.utc).timestamp())
        self.assertTrue(b.relpath.startswith("dt=2026-09-27/hh=23/probe-01-"), b.relpath)

    def test_a_scan_flushed_at_0000_03_lands_in_the_next_day_and_hour_00(self):
        b = self.batch_for(datetime(2026, 9, 28, 0, 0, 3, tzinfo=timezone.utc).timestamp())
        self.assertTrue(b.relpath.startswith("dt=2026-09-28/hh=00/probe-01-"), b.relpath)

    def test_a_replay_lands_under_the_hour_it_was_ingested_not_the_hour_of_its_events(self):
        old_scan = sweep(index=0)                                              # event_time 12:00 on the 27th
        b = self.batch_for(datetime(2026, 9, 27, 13, 16, 1, tzinfo=timezone.utc).timestamp(), old_scan)
        self.assertTrue(b.relpath.startswith("dt=2026-09-27/hh=13/"), b.relpath)
        self.assertEqual(json.loads(b.data.splitlines()[0])["event_time"], "2026-09-27T12:00:00.000Z")

    def test_the_hour_boundary_is_crossed_by_consecutive_scans(self):
        rig = Rig(start=datetime(2026, 9, 27, 23, 45, 3, tzinfo=timezone.utc).timestamp())
        first = rig.feed(sweep(index=0))[0]
        rig.clock.advance(900)
        second = rig.feed(sweep(index=1))[0]
        self.assertEqual((first.relpath.split("/")[0], first.relpath.split("/")[1]), ("dt=2026-09-27", "hh=23"))
        self.assertEqual((second.relpath.split("/")[0], second.relpath.split("/")[1]), ("dt=2026-09-28", "hh=00"))
        rig.upload([first, second])
        self.assertEqual(len(rig.uploader.calls), 2)
        self.assertNotEqual(first.relpath.split("/")[-1], second.relpath.split("/")[-1])


class IdleBridgeTests(unittest.TestCase):
    def test_a_bridge_idle_for_fifteen_minutes_between_scans_flushes_nothing_extra_and_keeps_its_buffer_empty(self):
        rig = Rig()
        for i in range(4):
            batches = rig.feed(sweep(index=i))
            self.assertEqual(len(batches), 1)
            rig.upload(batches)
            rig.clock.advance(900)
            self.assertEqual(rig.bridge.tick(), [])                            # the 90 s timer has nothing to flush
            self.assertEqual(rig.bridge.depth(), 0)
        self.assertEqual(rig.bridge.stats["files_uploaded"], 4)


if __name__ == "__main__":
    unittest.main()
