"""The intake service and its log of finished reports: reply only after the PUBACK, idempotency, the daily caps, the error mapping and
what happens when the process dies between the PUBACK and the log write. No sockets: the model, the broker and the clock are stubs."""
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import _support as S
import files_api
import intake_mqtt
import intake_server as srv
from _support import CountingModel, FakePublisher, KEY, MODEL, body, core, model_answer, report_body, report_ids

SECTORS = core.load_sectors()


class Crash(BaseException):
    """The process dies here: not an exception any handler may catch."""


class CrashingLog(srv.ReportLog):
    def append(self, entry):
        raise Crash()


class FailingLog(srv.ReportLog):
    def append(self, entry):
        raise OSError("disk full")


class Harness:
    def __init__(self, tmp, *, publisher=None, model=None, log_class=srv.ReportLog, now=None, **service_options):
        self.path = Path(tmp) / "reports.ndjson"
        self.clock = [S.NOW] if now is None else now
        self.logs = []
        self.reports = log_class(self.path, log=self.logs.append)
        self.publisher = publisher or FakePublisher()
        self.model = model or CountingModel()
        self.service = srv.IntakeService(sectors=SECTORS, api_key=KEY, model=MODEL, publisher=self.publisher, report_log=self.reports,
                                         now=lambda: self.clock[0], http=self.model, sleep=lambda _: None, log=self.logs.append,
                                         **service_options)

    def post(self, raw):
        return self.service.handle_report(raw)

    def restart(self, **options):
        """A new service over the same log file, as after a restart."""
        return Harness(self.path.parent, publisher=options.pop("publisher", self.publisher), model=options.pop("model", self.model),
                       now=self.clock, **options)


def run(case):
    with tempfile.TemporaryDirectory() as tmp:
        case(tmp)


class ReportingTests(unittest.TestCase):
    def test_a_report_is_assessed_published_and_answered_200(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            response = h.post(report_body(1))
            self.assertEqual(response.status, 200)
            self.assertEqual(set(response.body), {"event_id", "event_time", "relevance_score", "midichlorian_ppm", "kyber_resonance",
                                                  "dark_side_activity", "inference_rationale", "inference_model"})
            self.assertEqual(response.body["event_id"], core.event_id_for(report_ids(1)))
            self.assertEqual(h.model.calls, 1)
            self.assertEqual(len(h.publisher.attempts), 1)

    def test_what_is_published_is_the_envelope_bytes_on_the_web_intake_topic_with_the_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, publish_timeout=7.5)
            response = h.post(report_body(1))
            sent = h.publisher.attempts[0]
            self.assertEqual(sent["topic"], "force/telemetry/web-intake")
            self.assertEqual(sent["timeout"], 7.5)
            envelope = json.loads(sent["payload"])
            self.assertEqual(envelope["event_id"], response.body["event_id"])
            self.assertEqual((envelope["source_type"], envelope["source_id"], envelope["sector_id"]), ("report", "web-a3f2", "coruscant"))
            self.assertEqual(envelope["payload"]["description"], "Saw Darth Vader walking through the market.")
            self.assertEqual(sent["payload"], core.serialize_envelope(envelope))

    def test_the_default_publish_bound_is_ten_seconds(self):
        self.assertEqual(intake_mqtt.PUBLISH_TIMEOUT_S, 10.0)
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            h.post(report_body(1))
            self.assertEqual(h.publisher.attempts[0]["timeout"], 10.0)

    def test_the_answer_is_recorded_only_after_the_acknowledgement(self):
        for error, status, code, retry in ((intake_mqtt.PublishError("timeout", "x"), 503, "publish_timeout", "15"),
                                           (intake_mqtt.PublishError("unavailable", "x"), 503, "publish_unavailable", "15"),
                                           (intake_mqtt.PublishError("refused", "x"), 502, "publish_refused", "300")):
            with self.subTest(kind=error.kind), tempfile.TemporaryDirectory() as tmp:
                h = Harness(tmp, publisher=FakePublisher(error=error))
                response = h.post(report_body(1))
                self.assertEqual((response.status, response.body.get("error"), response.headers.get("Retry-After")), (status, code, retry))
                self.assertIsNone(h.reports.get(report_ids(1)))
                self.assertEqual(h.reports.count("2026-10-14"), 0)
                self.assertFalse(h.path.exists())

    def test_with_the_broker_down_no_model_call_is_made(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, publisher=FakePublisher(ready=False))
            response = h.post(report_body(1))
            self.assertEqual((response.status, response.body.get("error"), response.headers.get("Retry-After")), (503, "broker_unavailable", "15"))
            self.assertEqual((h.model.calls, h.publisher.attempts), (0, []))
            self.assertFalse(h.path.exists())

    def test_a_retry_after_a_failed_publish_succeeds_and_is_recorded_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, publisher=FakePublisher(error=intake_mqtt.PublishError("timeout", "x")))
            self.assertEqual(h.post(report_body(1)).status, 503)
            h.publisher.error = None
            self.assertEqual(h.post(report_body(1)).status, 200)
            self.assertEqual(len(h.publisher.attempts), 2)
            self.assertEqual(h.model.calls, 2)                       # no log entry yet, so the retry is assessed afresh
            self.assertEqual(h.reports.count("2026-10-14"), 1)


class IdempotencyTests(unittest.TestCase):
    def test_a_repeated_report_returns_the_stored_answer_with_no_model_call_and_no_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, model=CountingModel(respond=lambda n: (200, model_answer(relevance_score=0.9 if n == 1 else 0.1))))
            first = h.post(report_body(1))
            second = h.post(report_body(1))
            self.assertEqual((second.status, second.body), (200, first.body))
            self.assertEqual(second.body["relevance_score"], 0.9)
            self.assertEqual((h.model.calls, len(h.publisher.attempts)), (1, 1))

    def test_a_repeat_with_the_browsers_other_clock_or_retry_fields_is_still_the_same_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            first = h.post(report_body(1))
            second = h.post(report_body(1, observed_at="2026-10-14T14:00:00.000Z"))
            self.assertEqual(second.body, first.body)
            self.assertEqual(h.model.calls, 1)

    def test_the_same_report_id_for_a_different_text_or_planet_is_a_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            h.post(report_body(1))
            for other in (report_body(1, description="It rained today."), report_body(1, sector_id="naboo")):
                response = h.post(other)
                self.assertEqual((response.status, response.body.get("error")), (409, "report_id_reused"))
            self.assertEqual((h.model.calls, len(h.publisher.attempts)), (1, 1))

    def test_a_repeat_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            first = h.post(report_body(1))
            again = h.restart()
            second = again.post(report_body(1))
            self.assertEqual(second.body, first.body)
            self.assertEqual((again.model.calls, len(again.publisher.attempts)), (1, 1))      # the counters are shared; nothing new happened

    def test_two_submissions_of_one_report_at_once_make_one_model_call_and_one_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = threading.Event()
            h = Harness(tmp, model=CountingModel(gate=gate))
            results = []
            threads = [threading.Thread(target=lambda: results.append(h.post(report_body(1)))) for _ in range(2)]
            for t in threads:
                t.start()
            gate.set()
            for t in threads:
                t.join(10)
            self.assertEqual(sorted(r.status for r in results), [200, 200])
            self.assertEqual(results[0].body, results[1].body)
            self.assertEqual((h.model.calls, len(h.publisher.attempts)), (1, 1))


class CapTests(unittest.TestCase):
    def test_a_reporter_over_the_daily_cap_gets_429_until_the_next_utc_midnight(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, per_reporter_cap=3)
            for n in (1, 2, 3):
                self.assertEqual(h.post(report_body(n)).status, 200)
            blocked = h.post(report_body(4))
            self.assertEqual((blocked.status, blocked.body.get("error")), (429, "reporter_daily_cap"))
            self.assertEqual(blocked.headers.get("Retry-After"), str(9 * 3600 + 1))               # 15:00:00Z to the next 00:00:00Z, plus a second
            self.assertEqual(h.post(report_body(5, reporter="zzzz")).status, 200)             # another reporter is not affected
            self.assertEqual(h.model.calls, 4)                                                # the refused one cost no model call

    def test_the_global_cap_applies_across_reporters(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, global_cap=2)
            self.assertEqual(h.post(report_body(1, reporter="aaaa")).status, 200)
            self.assertEqual(h.post(report_body(2, reporter="bbbb")).status, 200)
            blocked = h.post(report_body(3, reporter="cccc"))
            self.assertEqual((blocked.status, blocked.body.get("error")), (429, "daily_cap"))
            self.assertIn("Retry-After", blocked.headers)

    def test_the_defaults_are_20_per_reporter_and_200_global(self):
        self.assertEqual((srv.DEFAULT_PER_REPORTER_CAP, srv.DEFAULT_GLOBAL_CAP), (20, 200))
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            self.assertEqual((h.service.per_reporter_cap, h.service.global_cap), (20, 200))

    def test_the_caps_are_counted_from_the_log_so_they_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, per_reporter_cap=2, global_cap=3)
            h.post(report_body(1))
            h.post(report_body(2))
            again = h.restart(per_reporter_cap=2, global_cap=3)
            self.assertEqual(again.post(report_body(3)).body.get("error"), "reporter_daily_cap")
            self.assertEqual(again.post(report_body(4, reporter="bbbb")).status, 200)
            self.assertEqual(again.post(report_body(5, reporter="cccc")).body.get("error"), "daily_cap")

    def test_the_caps_reset_at_the_next_utc_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, per_reporter_cap=1)
            self.assertEqual(h.post(report_body(1)).status, 200)
            self.assertEqual(h.post(report_body(2)).status, 429)
            h.clock[0] = datetime(2026, 10, 15, 0, 0, 1, tzinfo=timezone.utc)
            self.assertEqual(h.post(report_body(2)).status, 200)

    def test_the_utc_day_is_not_the_local_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, per_reporter_cap=1)
            h.clock[0] = datetime(2026, 10, 14, 23, 59, 59, tzinfo=timezone(timedelta(hours=-4)))       # 03:59:59Z on the 15th
            self.assertEqual(h.post(report_body(1)).status, 200)
            self.assertEqual(h.reports.count("2026-10-15"), 1)                                         # filed under the UTC day, not the 14th
            h.clock[0] = datetime(2026, 10, 14, 23, 59, 58, tzinfo=timezone(timedelta(hours=-4)))       # the same UTC day, same offset
            self.assertEqual(h.post(report_body(2)).status, 429)
            h.clock[0] = datetime(2026, 10, 15, 1, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(h.post(report_body(3)).status, 429)
            h.clock[0] = datetime(2026, 10, 15, 21, 0, 0, tzinfo=timezone(timedelta(hours=-4)))         # 01:00Z on the 16th: a new UTC day
            self.assertEqual(h.post(report_body(4)).status, 200)

    def test_a_repeat_of_a_finished_report_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, per_reporter_cap=1, global_cap=1)
            self.assertEqual(h.post(report_body(1)).status, 200)
            for _ in range(3):
                self.assertEqual(h.post(report_body(1)).status, 200)
            self.assertEqual(h.reports.count("2026-10-14"), 1)

    def test_requests_that_fail_validation_or_inference_or_publish_do_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, per_reporter_cap=1, global_cap=1)
            for raw in (report_body(1, description=""), report_body(1, sector_id="nowhere"), b"{"):
                self.assertIn(h.post(raw).status, (400, 422))
            h.model.respond = lambda n: (503, b"{}")
            self.assertEqual(h.post(report_body(1)).status, 503)
            h.model.respond = lambda n: (200, model_answer())
            h.publisher.error = intake_mqtt.PublishError("timeout", "x")
            self.assertEqual(h.post(report_body(1)).status, 503)
            h.publisher.error = None
            self.assertEqual(h.post(report_body(1)).status, 200)                   # still within the cap of 1

    def test_reports_in_flight_count_against_the_cap_so_two_at_once_cannot_both_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = threading.Event()
            h = Harness(tmp, global_cap=1, model=CountingModel(gate=gate))
            results, started = [], threading.Event()
            first = threading.Thread(target=lambda: results.append(("first", h.post(report_body(1, reporter="aaaa")))))
            first.start()
            deadline = datetime.now() + timedelta(seconds=5)
            while h.model.calls == 0 and datetime.now() < deadline:
                threading.Event().wait(0.01)                                       # the first is now inside the model call
            try:
                second = h.post(report_body(2, reporter="bbbb"))
                self.assertEqual((second.status, second.body.get("error")), (429, "daily_cap"))
            finally:
                gate.set()                                  # always let the first finish, or its log file is still open when the folder goes
                first.join(10)
            self.assertEqual(results[0][1].status, 200)

    def test_two_reports_from_one_reporter_at_once_cannot_both_pass_the_reporter_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = threading.Event()
            h = Harness(tmp, per_reporter_cap=1, model=CountingModel(gate=gate))
            results = []
            first = threading.Thread(target=lambda: results.append(h.post(report_body(1, reporter="aaaa"))))
            first.start()
            deadline = datetime.now() + timedelta(seconds=5)
            while h.model.calls == 0 and datetime.now() < deadline:
                threading.Event().wait(0.01)
            try:
                second = h.post(report_body(2, reporter="aaaa"))
                self.assertEqual((second.status, second.body.get("error")), (429, "reporter_daily_cap"))
            finally:
                gate.set()
                first.join(10)
            self.assertEqual(results[0].status, 200)

    def test_a_refused_attempt_releases_its_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, global_cap=1, publisher=FakePublisher(error=intake_mqtt.PublishError("timeout", "x")))
            for _ in range(3):
                self.assertEqual(h.post(report_body(1)).status, 503)               # never 429: each failure freed its reservation
            self.assertEqual(h.service._reserved, {})


class ErrorMappingTests(unittest.TestCase):
    def test_request_errors_keep_their_status_and_code(self):
        cases = ((report_body(1, description=""), 422, "empty_description"), (report_body(1, sector_id="nowhere"), 422, "unknown_sector"),
                 (b"{", 400, "not_json"), (b"x" * 140000, 413, "body_too_large"), (report_body(1, report_id="nope"), 422, "invalid_report_id"))
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            for raw, status, code in cases:
                with self.subTest(code=code):
                    response = h.post(raw)
                    self.assertEqual((response.status, response.body.get("error")), (status, code))
                    self.assertNotIn("Retry-After", response.headers)
            self.assertEqual((h.model.calls, h.publisher.attempts), (0, []))

    def test_a_model_refusal_is_a_422_blocked_by_model_and_is_not_retried_or_recorded(self):
        refusal = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, model=CountingModel(respond=lambda n: (200, refusal)))
            response = h.post(report_body(1))
            self.assertEqual((response.status, response.body.get("error"), response.body.get("message")), (422, "blocked_by_model", core.BLOCKED_MESSAGE))
            self.assertNotIn("Retry-After", response.headers)
            self.assertEqual((h.model.calls, h.publisher.attempts, h.path.exists()), (1, [], False))
            self.assertNotIn("SAFETY", json.dumps(response.body))

    def test_inference_failures_are_503_with_the_errors_own_retry_after(self):
        for respond, retry in ((lambda n: (503, b"{}"), "30"), (lambda n: (429, b"{}"), "30"), (lambda n: (401, b"{}"), "300"),
                               (lambda n: (200, b"garbage"), "30")):
            with self.subTest(retry=retry), tempfile.TemporaryDirectory() as tmp:
                h = Harness(tmp, model=CountingModel(respond=respond))
                response = h.post(report_body(1))
                self.assertEqual((response.status, response.body.get("error"), response.headers.get("Retry-After")), (503, "inference_unavailable", retry))
                self.assertEqual((h.publisher.attempts, h.path.exists()), ([], False))

    def test_no_answer_carries_the_key_the_description_or_the_models_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, model=CountingModel(respond=lambda n: (403, json.dumps({"error": {"status": "PERMISSION_DENIED", "message": f"key {KEY}"}}).encode())))
            response = h.post(report_body(1, description="Saw a figure in black robes, SECRET-TEXT"))
            text = json.dumps(response.body) + json.dumps(response.headers) + "".join(h.logs)
            for secret in (KEY, "SECRET-TEXT", "black robes"):
                self.assertNotIn(secret, text)


class DurabilityTests(unittest.TestCase):
    def test_a_log_write_failure_after_the_acknowledgement_still_answers_200_and_says_so_in_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, log_class=FailingLog)
            try:
                response = h.post(report_body(1))
            except OSError as e:
                self.fail(f"a log write failure after the acknowledgement escaped as {type(e).__name__}")
            self.assertEqual(response.status, 200)
            self.assertEqual(len(h.publisher.attempts), 1)
            self.assertTrue(any("ERROR the report log could not be written" in line and response.body["event_id"] in line for line in h.logs), h.logs)

    def test_a_crash_between_the_acknowledgement_and_the_log_write_republishes_the_same_event_id_on_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Harness(tmp, log_class=CrashingLog, model=CountingModel(respond=lambda n: (200, model_answer(relevance_score=0.9))))
            with self.assertRaises(Crash):
                first.post(report_body(1))
            self.assertEqual(len(first.publisher.attempts), 1)                       # it WAS published and acknowledged
            self.assertFalse(first.path.exists())                                    # ...and nothing was logged as done
            after = Harness(tmp, publisher=first.publisher, model=CountingModel(respond=lambda n: (200, model_answer(relevance_score=0.4))))
            retry = after.post(report_body(1))
            self.assertEqual(retry.status, 200)
            self.assertEqual(after.model.calls, 1)                                   # assessed again
            self.assertEqual(len(after.publisher.attempts), 2)                       # published again
            events = [json.loads(a["payload"]) for a in after.publisher.attempts]
            self.assertEqual(events[0]["event_id"], events[1]["event_id"])           # the same event_id: silver keeps the first copy
            self.assertEqual(events[0]["payload"]["relevance_score"], 0.9)
            self.assertEqual(events[1]["payload"]["relevance_score"], 0.4)           # the values may differ; the stored (first) ones win
            self.assertEqual(retry.body["relevance_score"], 0.4)

    def test_a_finished_report_is_on_disk_when_the_answer_is_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            response = h.post(report_body(1))
            entry = json.loads(h.path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(entry["response"], response.body)
            self.assertEqual(entry["description"], "Saw Darth Vader walking through the market.")
            self.assertEqual((entry["report_id"], entry["source_id"], entry["sector_id"]), (report_ids(1), "web-a3f2", "coruscant"))
            self.assertEqual(entry["accepted_at"], "2026-10-14T15:00:00.000Z")


class ReportLogTests(unittest.TestCase):
    def entry(self, n=1, source="web-a3f2", sector="coruscant", at="2026-10-14T15:00:00.000Z", description="text"):
        return {"report_id": report_ids(n), "event_id": core.event_id_for(report_ids(n)), "source_id": source, "sector_id": sector,
                "accepted_at": at, "fingerprint": srv.fingerprint(sector, description), "description": description, "response": {"n": n}}

    def test_entries_survive_a_reload_and_are_counted_by_day_and_reporter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.ndjson"
            log = srv.ReportLog(path)
            log.append(self.entry(1))
            log.append(self.entry(2, source="web-bbbb"))
            log.append(self.entry(3, at="2026-10-15T01:00:00.000Z"))
            again = srv.ReportLog(path)
            self.assertEqual(again.get(report_ids(2))["response"], {"n": 2})
            self.assertEqual((again.count("2026-10-14"), again.count("2026-10-14", "web-a3f2"), again.count("2026-10-14", "web-bbbb")), (2, 1, 1))
            self.assertEqual((again.count("2026-10-15"), again.count("2026-10-16")), (1, 0))

    def test_every_append_is_fsynced(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = srv.ReportLog(Path(tmp) / "r.ndjson")
            with mock.patch.object(srv.os, "fsync", wraps=os.fsync) as fsync:
                log.append(self.entry(1))
                log.append(self.entry(2))
            self.assertEqual(fsync.call_count, 2)

    def test_a_torn_last_line_is_skipped_and_the_next_append_starts_a_fresh_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.ndjson"
            srv.ReportLog(path).append(self.entry(1))
            with path.open("ab") as f:
                f.write(b'{"report_id": "01KX0000000000000000')                 # a crash mid-write: no newline
            messages = []
            log = srv.ReportLog(path, log=messages.append)
            self.assertEqual((log.skipped, log.count("2026-10-14")), (1, 1))
            self.assertTrue(any("skipped 1 unreadable line" in m for m in messages))
            log.append(self.entry(2))
            again = srv.ReportLog(path)
            self.assertEqual(again.count("2026-10-14"), 2)
            self.assertIsNotNone(again.get(report_ids(2)))

    def test_garbage_and_incomplete_lines_are_skipped_and_the_first_copy_of_a_report_stands(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.ndjson"
            first, second = self.entry(1), dict(self.entry(1), response={"n": "second"})
            lines = [json.dumps(first), "not json", json.dumps({"report_id": "x"}), "", "[1, 2]", "\xff-not-utf8".encode("latin-1").decode("latin-1"),
                     json.dumps(second)]
            path.write_bytes(("\n".join(lines) + "\n").encode("latin-1", "replace"))
            log = srv.ReportLog(path)
            self.assertEqual(log.get(report_ids(1))["response"], {"n": 1})
            self.assertEqual(log.count("2026-10-14"), 1)
            self.assertGreaterEqual(log.skipped, 3)

    def test_descriptions_with_newlines_and_unicode_stay_on_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.ndjson"
            text = "line one\nline two \U0001F480 caf\u00e9 \u2028 end"
            srv.ReportLog(path).append(self.entry(1, description=text))
            raw = path.read_bytes()
            self.assertEqual(raw.count(b"\n"), 1)
            self.assertNotIn(b"\r", raw)
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(srv.ReportLog(path).get(report_ids(1))["description"], text)

    def test_a_failed_append_indexes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "r.ndjson"
            log = srv.ReportLog(blocked)
            blocked.mkdir()                                                      # a directory where the file should be
            with self.assertRaises(OSError):
                log.append(self.entry(1))
            self.assertEqual((log.get(report_ids(1)), log.count("2026-10-14")), (None, 0))

    def test_recent_lists_one_planets_reports_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = srv.ReportLog(Path(tmp) / "r.ndjson")
            log.append(self.entry(1, at="2026-10-14T10:00:00.000Z"))
            log.append(self.entry(2, at="2026-10-14T12:00:00.000Z"))
            log.append(self.entry(3, sector="naboo", at="2026-10-14T13:00:00.000Z"))
            log.append(self.entry(4, at="2026-10-14T11:00:00.000Z"))
            self.assertEqual([e["report_id"] for e in log.recent("coruscant")], [report_ids(2), report_ids(4), report_ids(1)])
            self.assertEqual(len(log.recent("coruscant", limit=2)), 2)
            self.assertEqual(log.recent("alderaan"), [])


class SectorsTests(unittest.TestCase):
    def test_the_listing_is_all_60_planets_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = Harness(tmp).service.sectors_listing()
        self.assertEqual(len(rows), 60)
        self.assertTrue(all(set(r) == {"sector_id", "sector_name"} for r in rows))
        names = [r["sector_name"].lower() for r in rows]
        self.assertEqual(names, sorted(names))
        self.assertIn({"sector_id": "coruscant", "sector_name": "Coruscant"}, rows)


class CapHelperTests(unittest.TestCase):
    def test_seconds_until_the_next_utc_midnight(self):
        self.assertEqual(srv.seconds_until_next_utc_day(datetime(2026, 10, 14, 15, 0, 0, tzinfo=timezone.utc)), 9 * 3600 + 1)
        self.assertEqual(srv.seconds_until_next_utc_day(datetime(2026, 10, 14, 23, 59, 59, tzinfo=timezone.utc)), 2)
        self.assertEqual(srv.seconds_until_next_utc_day(datetime(2026, 10, 14, 0, 0, 0, tzinfo=timezone.utc)), 86401)


if __name__ == "__main__":
    unittest.main()
