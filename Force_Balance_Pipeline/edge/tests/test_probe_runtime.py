"""The probe runtime on a fake clock (doc 04): scans on the quarter hour, write-ahead into SQLite, deletion only on PUBACK, the clock gate,
faults only in CONNECTED and logged before buffering, STEALTH's payload and cadence, control messages, the buffer cap and its overflow
event, and a link that goes quiet without a disconnect. Every envelope it publishes must also pass the bridge's structural check. Offline."""
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _probe_support import SECTORS, FakePublisher, Rig, epoch  # noqa: E402
import bridge  # noqa: E402
from forcesim.envelope import check_envelope  # noqa: E402
from probe.clock import iso, parse_iso  # noqa: E402

START = "2026-09-27T12:00:00"


def scan_times(rig):
    """The distinct scan start times that reached the broker, in order."""
    firsts = {}
    for _, _, e in rig.received():
        if e.get("scan_id"):
            firsts.setdefault(e["scan_id"], e["event_time"])
    return sorted(firsts.values())


class Base(unittest.TestCase):
    def rig(self, **kw):
        rig = Rig(**kw)
        self.addCleanup(rig.cleanup)
        return rig


class SteadyStateTests(Base):
    def test_scans_are_taken_on_the_quarter_hour_60_readings_each(self):
        rig = self.rig(start=START)
        rig.run(3600 + 5)
        times = scan_times(rig)
        self.assertEqual(times, [f"2026-09-27T{h:02d}:{m:02d}:00.000Z" for h, m in ((12, 15), (12, 30), (12, 45), (13, 0))])
        for _, _, e in rig.received():
            self.assertEqual(e["mode"], "CONNECTED")
        self.assertEqual(len(rig.received()), 4 * 60)
        self.assertEqual(rig.runtime.stats["scans"], 4)

    def test_event_times_are_the_scan_boundary_plus_the_planet_offset_and_ids_are_unique(self):
        rig = self.rig(start=START)
        rig.run(900 + 5)
        scan = [e for _, _, e in rig.received()]
        self.assertEqual([e["sector_id"] for e in scan], [s.sector_id for s in SECTORS])
        offsets = [parse_iso(e["event_time"]) - epoch("2026-09-27T12:15:00") for e in scan]
        self.assertEqual(offsets[0], 0)
        self.assertAlmostEqual(offsets[-1], 2.95, places=3)
        self.assertEqual(len({e["event_id"] for e in scan}), 60)
        self.assertEqual(len({e["scan_id"] for e in scan}), 1)

    def test_a_steady_link_never_leaves_connected_and_the_buffer_empties(self):
        rig = self.rig(start=START)
        rig.run(7200 + 5)
        self.assertEqual([t["to"] for t in rig.transitions()], ["CONNECTED"])
        self.assertEqual(rig.buffer.depth(), 0)
        self.assertEqual(rig.runtime.stats["published"], rig.runtime.stats["acked"])

    def test_every_published_envelope_passes_the_bridges_structure_check_and_doc_02(self):
        rig = self.rig(start=START)
        rig.run(1800 + 5)
        for _, _, e in rig.received():
            self.assertEqual(bridge.check_structure(e), [])
            self.assertEqual(check_envelope(e), [])
            self.assertIs(e["is_synthetic"], False)
            self.assertIsNone(e["synthetic_ingest_ts"])
            self.assertEqual(e["source_id"], "probe-01")

    def test_the_first_scan_waits_for_the_next_boundary_unless_immediate(self):
        rig = self.rig(start="2026-09-27T12:07:00")
        rig.run(60)
        self.assertEqual(rig.received(), [])
        rig = self.rig(start="2026-09-27T12:07:00", immediate=True)
        rig.run(5)
        self.assertEqual(scan_times(rig), ["2026-09-27T12:00:00.000Z"])

    def test_scans_across_an_hour_and_a_midnight_boundary(self):
        rig = self.rig(start="2026-09-27T23:40:00")
        rig.run(2400)
        self.assertEqual(scan_times(rig), ["2026-09-27T23:45:00.000Z", "2026-09-28T00:00:00.000Z", "2026-09-28T00:15:00.000Z"])

    def test_a_late_tick_takes_every_scan_it_missed_in_order(self):
        rig = self.rig(start=START)
        rig.run(1)                                                  # the first tick schedules 12:15
        rig.run(4000, step=4000)                                    # then one tick at 13:06:41
        self.assertEqual(scan_times(rig), [f"2026-09-27T{h:02d}:{m:02d}:00.000Z" for h, m in ((12, 15), (12, 30), (12, 45), (13, 0))])

    def test_the_readings_walk_between_scans_not_reset(self):
        rig = self.rig(start=START)
        rig.run(1800 + 5)
        by_scan = {}
        for _, _, e in rig.received():
            by_scan.setdefault(e["event_time"][:16], {})[e["sector_id"]] = e["payload"]["midichlorian_ppm"]
        first, second = (v for _, v in sorted(by_scan.items())[:2])
        self.assertTrue(any(first[k] != second[k] for k in first))


class WriteAheadTests(Base):
    def test_each_row_is_in_the_buffer_when_it_is_published(self):
        rig = self.rig(start=START)
        seen = []

        def hook(key, payload):
            seen.append(key in {r[0] for r in rig.buffer.batch(10 ** 6)})
        rig.publisher.hook = hook
        rig.run(900 + 5)
        self.assertEqual(len(seen), 60)
        self.assertTrue(all(seen))

    def test_a_row_is_deleted_only_when_its_puback_arrives(self):
        rig = self.rig(start=START, publisher=FakePublisher(latency=20.0))
        rig.run(900 + 5)                                            # the 12:15 scan is out, its acknowledgements are not back yet
        self.assertEqual(rig.buffer.depth(), 60)
        rig.run(20)
        self.assertEqual(rig.buffer.depth(), 0)

    def test_the_payload_in_the_buffer_is_exactly_what_was_published(self):
        rig = self.rig(start=START, publisher=FakePublisher(latency=1000.0), )
        rig.runtime.ack_timeout = 10 ** 6
        rig.run(900 + 5)
        stored = dict(rig.buffer.batch(100))
        for _, key, envelope in rig.received():
            self.assertEqual(json.loads(stored[key]), envelope)
            self.assertNotIn(", ", stored[key])                     # the compact form that is published, byte for byte


class ClockGateTests(Base):
    def test_scans_due_before_sync_are_skipped_and_logged_never_stamped_or_buffered(self):
        rig = self.rig(start=START, synced=False)
        rig.run(3600 + 5)
        self.assertEqual(rig.received(), [])
        self.assertEqual(rig.buffer.depth(), 0)
        skipped = [e for e in rig.events() if e["event"] == "scan_skipped"]
        self.assertEqual([e["scan_time"] for e in skipped],
                         [f"2026-09-27T{h:02d}:{m:02d}:00.000Z" for h, m in ((12, 15), (12, 30), (12, 45), (13, 0))])
        self.assertTrue(all(e["reason"] == "clock_unsynced" for e in skipped))
        self.assertEqual(rig.runtime.stats["skipped"], 4)

    def test_scans_resume_after_sync_and_the_skipped_ones_are_not_invented(self):
        rig = self.rig(start=START, synced=False)
        rig.run(1800 + 5)
        rig.synced[0] = True
        rig.run(1800)
        self.assertEqual(scan_times(rig), ["2026-09-27T12:45:00.000Z", "2026-09-27T13:00:00.000Z"])

    def test_a_clock_that_steps_back_waits_for_the_boundary_it_scheduled_and_re_stamps_nothing(self):
        rig = self.rig(start=START)
        rig.run(1800 + 5)                                            # 12:15 and 12:30 taken
        rig.now = epoch("2026-09-27T12:20:00")                       # the clock steps back 10 minutes
        rig.publisher.now = rig.now
        rig.run(1505)                                                # ... and runs on to 12:45
        self.assertEqual(scan_times(rig), [f"2026-09-27T12:{m}:00.000Z" for m in (15, 30, 45)])     # 12:30 not taken twice
        self.assertEqual(len(rig.received()), 3 * 60)

    def test_after_a_restart_with_the_clock_behind_the_gate_refuses_the_scan_it_already_took(self):
        rig = self.rig(start=START)
        rig.run(1800 + 5)
        rig.crash()
        rig.now = epoch("2026-09-27T12:20:00")                       # restarted with the clock 10 minutes back
        rig.publisher.now = rig.now
        rig.run(1000)                                                # 12:30 is the next boundary: already taken
        skipped = [e for e in rig.events() if e["event"] == "scan_skipped"]
        self.assertEqual([(e["scan_time"], e["reason"]) for e in skipped], [("2026-09-27T12:30:00.000Z", "clock_backwards")])
        self.assertEqual(len(scan_times(rig)), 2)

    def test_the_last_boundary_is_kept_across_a_restart(self):
        rig = self.rig(start=START)
        rig.run(1800 + 5)
        rig.crash()
        rig.now = epoch("2026-09-27T12:20:00")
        rig.run(600)
        self.assertEqual(len(scan_times(rig)), 2)


class LinkTests(Base):
    def test_a_link_that_goes_quiet_without_a_disconnect_is_found_by_the_ack_timeout(self):
        rig = self.rig(start=START, publisher=FakePublisher(detect_after=None))
        rig.run_until("2026-09-27T12:31:00", at=[])
        rig.publisher.cut()
        rig.run_until("2026-09-27T12:46:00")
        self.assertEqual(rig.publisher.resets, 1)
        down = [t for t in rig.transitions() if t["to"] == "DISCONNECTED"]
        self.assertEqual(len(down), 1)
        self.assertEqual(down[0]["reason"], "link_lost:publish_unacked")
        self.assertEqual(down[0]["ts_utc"], "2026-09-27T12:45:34.000Z")     # published at 12:45:03, then more than 30 s without a PUBACK
        self.assertEqual(rig.buffer.depth(), 60)                            # nothing was lost: the 12:45 scan is still buffered

    def test_the_scan_taken_before_the_probe_notices_carries_connected_and_still_arrives(self):
        rig = self.rig(start=START, publisher=FakePublisher(detect_after=None))
        rig.run_until("2026-09-27T12:31:00")
        rig.publisher.cut()
        rig.run_until("2026-09-27T13:16:00", at=[(76 * 60 - 31 * 60, rig.publisher.restore)])
        rig.run(60)
        modes = {}
        for _, _, e in rig.received():
            modes.setdefault(e["event_time"][:16], Counter())[e["mode"]] += 1
        self.assertEqual(modes["2026-09-27T12:45"], Counter({"CONNECTED": 60}))         # taken before it was noticed
        self.assertEqual(modes["2026-09-27T13:00"], Counter({"DISCONNECTED": 60}))
        self.assertEqual(rig.buffer.depth(), 0)


class FaultIntegrationTests(Base):
    def test_faults_are_injected_only_in_connected_and_logged_before_the_row_is_buffered(self):
        rig = self.rig(start=START, fault_rate=6.0)
        original_append = rig.buffer.append
        problems = []

        def checked(rows, created_at, make_overflow=None):
            logged = {e["event_id"] for e in rig.faults_logged()}
            faulty = [r[0] for r in rows if any(x in r[3] for x in ("unknown-", '"midichlorian_ppm":null', '"dark_side_activity":null', '"kyber_resonance":null'))]
            problems.extend(i for i in faulty if i not in logged)
            return original_append(rows, created_at, make_overflow)
        rig.buffer.append = checked
        rig.runtime.buffer = rig.buffer
        rig.run(1800, at=[(1000, rig.publisher.cut)])                # the 12:30 scan is taken while DISCONNECTED
        self.assertEqual(problems, [])
        self.assertGreater(len(rig.faults_logged()), 5)
        logged_scans = {e["scan_id"] for e in rig.faults_logged()}
        disconnected = {e["scan_id"] for _, _, e in rig.received() if e["mode"] == "DISCONNECTED"}
        self.assertFalse(logged_scans & disconnected)

    def test_the_log_matches_what_was_published_exactly(self):
        rig = self.rig(start=START, fault_rate=3.0)
        rig.run(7200 + 5)
        by_id = {k: e for _, k, e in rig.received()}
        for entry in rig.faults_logged():
            e = by_id[entry["event_id"]]
            self.assertEqual((e["event_time"], e["sector_id"]), (entry["event_time"], entry["sector_id"]))
        real = {s.sector_id for s in SECTORS}
        end = rig.now

        def faulty(e):
            return bool(check_envelope(e)) or e["sector_id"] not in real or parse_iso(e["event_time"]) > end
        self.assertEqual({k for k, e in by_id.items() if faulty(e)}, {entry["event_id"] for entry in rig.faults_logged()})

    def test_no_faults_by_default_in_these_tests_and_none_with_rate_zero(self):
        rig = self.rig(start=START, fault_rate=0.0)
        rig.run(3600)
        self.assertEqual(rig.faults_logged(), [])
        for _, _, e in rig.received():
            self.assertEqual(check_envelope(e), [])


class StealthTests(Base):
    def force(self, rig, mode, seconds=None):
        payload = json.dumps({"mode": mode, **({"for_seconds": seconds} if seconds else {})}).encode()
        self.assertTrue(rig.runtime.on_control(payload, rig.now))

    def test_stealth_scans_are_hourly_on_the_hour_with_two_null_channels_and_no_temperature(self):
        rig = self.rig(start=START)
        self.force(rig, "STEALTH")
        rig.run(3 * 3600 + 5)
        self.assertEqual(scan_times(rig), [f"2026-09-27T{h}:00:00.000Z" for h in (13, 14, 15)])
        for _, _, e in rig.received():
            self.assertEqual(e["mode"], "STEALTH")
            self.assertIsNone(e["payload"]["midichlorian_ppm"])
            self.assertIsNone(e["payload"]["kyber_resonance"])
            self.assertIn("midichlorian_ppm", e["payload"])                 # an explicit null, not a missing key
            self.assertNotIn("sensor_temp_c", e["payload"])
            self.assertIsInstance(e["payload"]["dark_side_activity"], float)
            self.assertIsInstance(e["payload"]["battery_pct"], int)
            self.assertEqual(check_envelope(e), [])
            self.assertEqual(bridge.check_structure(e), [])
        self.assertEqual(len(rig.received()), 3 * 60)

    def test_a_stealth_sweep_covers_all_60_planets_under_one_scan_id(self):
        rig = self.rig(start=START)
        self.force(rig, "STEALTH")
        rig.run(3600 + 5)
        scan = [e for _, _, e in rig.received()]
        self.assertEqual([e["sector_id"] for e in scan], [s.sector_id for s in SECTORS])
        self.assertEqual(len({e["scan_id"] for e in scan}), 1)

    def test_no_faults_are_injected_in_stealth(self):
        rig = self.rig(start=START, fault_rate=8.0)
        self.force(rig, "STEALTH")
        rig.run(4 * 3600)
        self.assertEqual(rig.faults_logged(), [])

    def test_stealth_ends_and_the_quarter_hour_cadence_returns(self):
        rig = self.rig(start=START)
        self.force(rig, "STEALTH", 2 * 3600)
        rig.run(4 * 3600 + 5)
        modes = {e["event_time"][11:16]: e["mode"] for _, _, e in rig.received() if e["sector_id"] == SECTORS[0].sector_id}
        self.assertEqual([t for t, m in sorted(modes.items()) if m == "STEALTH"], ["13:00"])
        self.assertEqual(modes["14:00"], "CONNECTED")            # the override ends at 14:00:00, the scan due then is CONNECTED
        self.assertEqual(modes["14:15"], "CONNECTED")
        self.assertEqual(modes["14:30"], "CONNECTED")
        self.assertEqual([t["reason"] for t in rig.transitions()], ["startup", "operator", "operator_ended"])

    def test_stealth_that_loses_the_link_buffers_keeps_its_label_and_drains_after(self):
        rig = self.rig(start=START)
        self.force(rig, "STEALTH")
        rig.run_until("2026-09-27T12:30:00")
        rig.publisher.cut()
        rig.run_until("2026-09-27T15:30:00", at=[(3 * 3600 - 60, rig.publisher.restore)])
        rig.run(5)
        self.assertEqual(rig.buffer.depth(), 0)
        self.assertEqual(scan_times(rig), [f"2026-09-27T{h}:00:00.000Z" for h in (13, 14, 15)])
        self.assertEqual({e["mode"] for _, _, e in rig.received()}, {"STEALTH"})
        self.assertIn(("STEALTH", True), [(t["to"], t["offline"]) for t in rig.transitions()])
        self.assertIn("BURST", [t["to"] for t in rig.transitions()])


class ControlIntegrationTests(Base):
    def test_an_injection_is_applied_from_the_next_scan(self):
        rig = self.rig(start=START)
        rig.run(20 * 60)
        self.assertTrue(rig.runtime.on_control(json.dumps({"inject": "spike", "sector_id": "tatooine",
                                                           "signature": "sith_presence"}).encode(), rig.now))
        episodes = rig.sim.planets["tatooine"].episodes
        self.assertEqual(len(episodes), 1)
        self.assertEqual((episodes[0].signature, episodes[0].start), ("sith_presence", rig.runtime.scan_index))
        self.assertEqual((episodes[0].ramp, episodes[0].hold, episodes[0].decay), (2, 4, 3))
        self.assertIn("control_applied", [e["event"] for e in rig.events()])

    def test_the_injected_planet_deviates_and_the_others_do_not_get_a_spike(self):
        rig = self.rig(start=START)
        rig.run(20 * 60)
        rig.runtime.on_control(json.dumps({"inject": "spike", "sector_id": "tatooine", "signature": "sith_presence"}).encode(), rig.now)
        rig.run(8 * 900)
        dark = {}
        for _, _, e in rig.received():
            dark.setdefault(e["sector_id"], []).append(e["payload"]["dark_side_activity"])
        s = next(x for x in SECTORS if x.sector_id == "tatooine")
        self.assertGreater(max(dark["tatooine"]), s.dark_baseline + 3 * s.dark_sigma)     # the sith_presence target is +4 sigma dark

    def test_a_bad_message_is_logged_and_ignored_and_the_probe_keeps_running(self):
        rig = self.rig(start=START)
        for payload in (b"garbage", json.dumps({"inject": "spike", "sector_id": "nowhere", "signature": "sith_presence"}).encode(),
                        json.dumps({"mode": "BURST"}).encode()):
            self.assertFalse(rig.runtime.on_control(payload, rig.now))
        rig.run(900 + 5)
        self.assertEqual(len(rig.received()), 60)
        rejected = [e for e in rig.events() if e["event"] == "control_rejected"]
        self.assertEqual(len(rejected), 3)
        self.assertTrue(all(e["reason"] for e in rejected))

    def test_a_forced_disconnect_buffers_then_drains_when_it_ends(self):
        rig = self.rig(start=START)
        rig.run(600)
        rig.runtime.on_control(json.dumps({"mode": "DISCONNECTED", "for_seconds": 2700}).encode(), rig.now)
        rig.run(2700 + 600)
        reasons = [t["reason"] for t in rig.transitions()]
        self.assertEqual(reasons, ["startup", "operator", "operator_ended", "drain_complete"])
        self.assertEqual(rig.buffer.depth(), 0)
        self.assertEqual(len(scan_times(rig)), len(set(scan_times(rig))))
        self.assertEqual(Counter(e["mode"] for _, _, e in rig.received())["DISCONNECTED"], 3 * 60)


class OverflowTests(Base):
    def test_the_oldest_rows_are_dropped_and_one_overflow_event_says_how_many(self):
        rig = self.rig(start=START, cap=200)
        rig.run_until("2026-09-27T12:20:00")
        rig.publisher.cut()
        rig.run_until("2026-09-27T14:20:00", at=[(0.0, lambda: None)])      # 12:30 .. 14:15: 8 scans buffered against a cap of 200
        self.assertLessEqual(rig.buffer.depth(), 200)
        overflow_events = [e for e in rig.events() if e["event"] == "buffer_overflow"]
        self.assertGreater(len(overflow_events), 0)
        rig.publisher.restore()
        rig.run(610)
        self.assertEqual(rig.buffer.depth(), 0)
        housekeeping = [e for _, _, e in rig.received() if "kind" in e["payload"]]
        self.assertGreater(len(housekeeping), 0)
        first = housekeeping[0]
        self.assertEqual(first["payload"]["kind"], "buffer_overflow")
        self.assertEqual(first["payload"]["cap"], 200)
        self.assertGreater(first["payload"]["dropped"], 0)
        self.assertEqual((first["source_type"], first["sector_id"], first["scan_id"]), ("probe", "probe-01", None))
        self.assertEqual(check_envelope(first), [])
        self.assertEqual(bridge.check_structure(first), [])

    def test_the_overflow_events_scan_id_is_json_null_though_the_buffer_column_is_the_empty_string(self):
        rig = self.rig(start=START, cap=100)
        rig.publisher.hold_acks = True                          # keep the overflow row in the buffer after it is published
        rig.runtime.ack_timeout = 10 ** 6
        rig.run_until("2026-09-27T12:31:00")                   # scans at 12:15 and 12:30: 120 readings against a cap of 100
        column, payload = rig.buffer.db.execute("SELECT scan_id, payload FROM buffered_events WHERE scan_id = ''").fetchone()
        self.assertEqual(column, "")                            # doc 04: scan_id is NOT NULL in this table
        self.assertIn('"scan_id":null', payload)                # the payload keeps the real JSON null; that is what a replay publishes
        self.assertIsNone(json.loads(payload)["scan_id"])
        housekeeping = next(e for _, _, e in rig.received() if "kind" in e["payload"])
        self.assertIsNone(housekeeping["scan_id"])

    def test_delivered_plus_dropped_equals_everything_that_was_taken(self):
        rig = self.rig(start=START, cap=200)
        rig.run_until("2026-09-27T12:20:00")
        rig.publisher.cut()
        rig.run_until("2026-09-27T14:20:00")
        rig.publisher.restore()
        rig.run(900)
        readings = [e for _, _, e in rig.received() if "kind" not in e["payload"]]
        housekeeping = [e for _, _, e in rig.received() if "kind" in e["payload"]]
        dropped_total = sum(h["payload"]["dropped"] for h in housekeeping)
        taken = rig.runtime.stats["scans"] * 60
        self.assertEqual(len({e["event_id"] for e in readings}) + dropped_total, taken)
        self.assertEqual(len(readings), len({e["event_id"] for e in readings}))

    def test_the_buffer_never_exceeds_its_cap(self):
        rig = self.rig(start=START, cap=150)
        rig.publisher.cut()
        rig.publisher.detect_after = 0
        for _ in range(12):
            rig.run(900)
            self.assertLessEqual(rig.buffer.depth(), 150)


class DrainAcknowledgementTests(Base):
    """The drain sends the next batch only when the WHOLE previous one is acknowledged, never resends a row that is still in flight,
    and an overflow event is sent like any other reading."""

    def backlog_rig(self, drain_batch):
        rig = self.rig(start=START, drain_batch=drain_batch)
        rig.run_until("2026-09-27T12:16:00")
        rig.publisher.cut()
        rig.run_until("2026-09-27T12:59:53")                   # the 12:30 and 12:45 scans are buffered: 120 rows
        self.assertEqual(rig.buffer.depth(), 120)
        rig.publisher.hold_acks = True
        rig.publisher.restore()
        rig.run_until("2026-09-27T12:59:54")                   # reconnected: the first batch has just been published
        return rig

    @staticmethod
    def replayed(rig):
        """The rows of the two buffered scans (12:30 and 12:45) that have reached the broker, duplicates included."""
        return [k for _, k, e in rig.received() if "2026-09-27T12:30" <= e["event_time"] < "2026-09-27T13:00"]

    def test_a_partly_acknowledged_batch_holds_back_the_next_one(self):
        rig = self.backlog_rig(drain_batch=100)
        self.assertEqual(len(self.replayed(rig)), 100)
        rig.publisher.release(10)
        rig.run(20)                                            # the live 13:00 scan is taken and published meanwhile; it is not part of the drain
        self.assertEqual(len(self.replayed(rig)), 100, "the next batch went out before the first was fully acknowledged")
        self.assertEqual(rig.modes.mode, "BURST")
        rig.publisher.release()
        rig.run(5)
        self.assertEqual(len(self.replayed(rig)), 100, "the pause after a batch was skipped")
        rig.run(10)
        self.assertEqual(len(self.replayed(rig)), 120)

    def test_a_live_row_still_in_flight_is_not_sent_again_by_the_drain(self):
        rig = self.backlog_rig(drain_batch=100)
        rig.publisher.release()                                # batch 1 confirmed; the live 13:00 scan will be taken during the pause
        rig.run_until("2026-09-27T13:00:12")
        live = [e for _, _, e in rig.received() if e["event_time"].startswith("2026-09-27T13:00")]
        self.assertEqual(len(live), 60)
        self.assertEqual({e["mode"] for e in live}, {"BURST"})
        again = [k for k, n in Counter(k for _, k, _ in rig.received()).items() if n > 1]
        self.assertEqual(again, [], "the drain resent rows that were still waiting for their PUBACK")
        rig.publisher.hold_acks = False
        rig.run(30)
        self.assertEqual(rig.buffer.depth(), 0)
        self.assertEqual(len(rig.received()), 240)              # the 12:15 scan, the 12:30 and 12:45 replay, the live 13:00 scan: once each

    def test_an_overflow_event_is_published_at_once_when_the_probe_is_online(self):
        rig = self.rig(start=START, cap=100)
        rig.publisher.hold_acks = True                          # nothing is ever confirmed, so the buffer fills while online
        rig.runtime.ack_timeout = 10 ** 6
        rig.run_until("2026-09-27T12:31:00")                   # scans at 12:15 and 12:30: 120 readings against a cap of 100
        self.assertEqual(rig.runtime.stats["overflows"], 1)
        housekeeping = [e for _, _, e in rig.received() if "kind" in e["payload"]]
        self.assertEqual([h["payload"]["kind"] for h in housekeeping], ["buffer_overflow"])


if __name__ == "__main__":
    unittest.main()
