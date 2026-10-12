"""The browser request, before any model call (doc 04 fixture table: empty is rejected before inference; a 5000-character
rambling must not crash), the observed_at window (decision 11) and the deterministic event_id."""
import json
import random
import unittest
from datetime import datetime, timedelta, timezone

import _support as S
import bridge
from _support import body, core

SECTORS = core.load_sectors()


def accepted(testcase, raw):
    """parse_request(raw), turning an unexpected rejection into an assertion FAIL (not an ERROR)."""
    try:
        return core.parse_request(raw, SECTORS)
    except core.RequestError as e:
        testcase.fail(f"a valid request was rejected: {e.status} {e.code}")


def rejected(testcase, raw):
    with testcase.assertRaises(core.RequestError) as caught:
        core.parse_request(raw, SECTORS)
    return caught.exception


class ParseRequestTests(unittest.TestCase):
    def test_the_fixture_report_id_is_a_real_ulid(self):
        self.assertTrue(core.is_ulid(S.REPORT_ID))
        self.assertTrue(core.is_ulid(S.REPORT_ID_2))

    def test_a_valid_request(self):
        request = accepted(self, body())
        self.assertEqual(request.sector_id, "coruscant")
        self.assertEqual(request.description, "Saw Darth Vader walking through the market.")
        self.assertEqual(request.report_id, S.REPORT_ID)
        self.assertEqual(request.source_id, "web-a3f2")
        self.assertEqual(request.observed_at, "2026-10-14T14:59:00.000Z")

    def test_the_description_is_kept_verbatim(self):
        text = "  Saw a figure in black robes.\n\tThe air felt cold \u2014 \U0001F480 \u202e rtl \u2028 line  "
        self.assertEqual(accepted(self, body(description=text)).description, text)

    def test_an_empty_description_is_rejected(self):
        for text in ("", "   ", "\n\t \r\n"):
            with self.subTest(text=text):
                error = rejected(self, body(description=text))
                self.assertEqual((error.status, error.code), (422, "empty_description"))

    def test_a_missing_or_non_string_description_is_rejected(self):
        for raw in (body(description=None), body(description=123), body(description=["a"]), body(description={"a": 1})):
            with self.subTest(raw=raw):
                self.assertEqual(rejected(self, raw).code, "description_missing")

    def test_the_description_limit_is_exactly_8000_characters(self):
        self.assertEqual(core.MAX_DESCRIPTION_CHARS, 8000)
        self.assertEqual(len(accepted(self, body(description="a" * 8000)).description), 8000)
        error = rejected(self, body(description="a" * 8001))
        self.assertEqual((error.status, error.code), (422, "description_too_long"))

    def test_the_limit_counts_characters_not_bytes(self):
        text = "\U0001F480" * 8000          # 8,000 characters: 32,000 bytes as UTF-8, about 96,000 as the escaped JSON body() writes
        self.assertEqual(len(accepted(self, body(description=text)).description), 8000)
        raw = json.dumps({"sector_id": "coruscant", "description": text, "report_id": S.REPORT_ID, "reporter_id": "a3f2"},
                         ensure_ascii=False).encode("utf-8")
        self.assertEqual(len(accepted(self, raw).description), 8000)

    def test_a_5000_character_ramble_is_accepted(self):
        text = " ".join(random.Random(7).choice(["the", "air", "was", "cold", "and", "odd"]) for _ in range(1000))[:5000]
        self.assertEqual(accepted(self, body(description=text)).description, text)

    def test_an_oversize_body_is_rejected_before_it_is_parsed(self):
        error = rejected(self, b"x" * (core.MAX_BODY_BYTES + 1))        # not even JSON: the size check comes first
        self.assertEqual((error.status, error.code), (413, "body_too_large"))
        self.assertEqual(core.MAX_BODY_BYTES, 128 * 1024)

    def test_a_body_that_is_not_json_is_rejected(self):
        for raw in (b"", b"not json", b"\xff\xfe\x00", b"{'a': 1}"):
            with self.subTest(raw=raw):
                error = rejected(self, raw)
                self.assertEqual((error.status, error.code), (400, "not_json"))

    def test_a_deeply_nested_body_is_rejected_not_crashed(self):
        error = rejected(self, b"[" * 60000)
        self.assertEqual((error.status, error.code), (400, "not_json"))

    def test_a_body_that_is_not_an_object_is_rejected(self):
        for raw in (b"[]", b'"text"', b"42", b"null"):
            with self.subTest(raw=raw):
                self.assertEqual(rejected(self, raw).code, "not_an_object")

    def test_an_unknown_or_malformed_sector_is_rejected(self):
        for value in ("tatooine-2", "", "COruscant", ["coruscant"], {"a": 1}, 7, None):
            with self.subTest(value=value):
                raw = body(sector_id=value) if value is not None else body(sector_id=None)
                error = rejected(self, raw)
                self.assertEqual((error.status, error.code), (422, "unknown_sector"))

    def test_a_lone_surrogate_is_rejected_because_it_cannot_be_written_as_utf8(self):
        raw = b'{"sector_id":"coruscant","description":"bad \\ud800 text","report_id":"%s","reporter_id":"a3f2"}' % S.REPORT_ID.encode()
        self.assertEqual(rejected(self, raw).code, "invalid_characters")

    def test_control_characters_are_rejected_but_tab_newline_and_return_are_fine(self):
        for char in ("\x00", "\x07", "\x1b", "\x7f"):
            with self.subTest(char=repr(char)):
                self.assertEqual(rejected(self, body(description=f"a{char}b")).code, "invalid_characters")
        self.assertEqual(accepted(self, body(description="a\tb\nc\rd")).description, "a\tb\nc\rd")

    def test_report_ids_must_be_uppercase_ulids(self):
        for value in (S.REPORT_ID.lower(), S.REPORT_ID[:-1], S.REPORT_ID + "0", "8" + S.REPORT_ID[1:],
                      "01KXI000000000000000000001", "01KXL000000000000000000001", "01KXO000000000000000000001",
                      "01KXU000000000000000000001", 12345, ["x"], "", None):
            with self.subTest(value=value):
                raw = body(report_id=value) if value is not None else body(report_id=None)
                error = rejected(self, raw)
                self.assertEqual((error.status, error.code), (422, "invalid_report_id"))

    def test_reporter_ids_are_four_to_sixteen_lowercase_letters_or_digits(self):
        for value in ("abc", "a" * 17, "A3F2", "a3-f2", "a3 f2", "a3f2\n", "../../x", 1234, None):
            with self.subTest(value=value):
                raw = body(reporter_id=value) if value is not None else body(reporter_id=None)
                self.assertEqual(rejected(self, raw).code, "invalid_reporter_id")
        self.assertEqual(accepted(self, body(reporter_id="abcd")).source_id, "web-abcd")
        self.assertEqual(accepted(self, body(reporter_id="a" * 16)).source_id, "web-" + "a" * 16)

    def test_the_source_id_is_one_the_bridge_accepts_as_a_file_name(self):
        envelope_source = accepted(self, body(reporter_id="z9z9")).source_id
        self.assertRegex(envelope_source, bridge._SOURCE_ID_RE)


class ObservedAtTests(unittest.TestCase):
    def resolve(self, observed_at, now=S.NOW):
        return core.resolve_event_time(observed_at, now)

    def iso(self, delta):
        return (S.NOW + delta).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def test_a_plausible_browser_time_is_used(self):
        moment, used = self.resolve("2026-10-14T14:59:00.000Z")
        self.assertTrue(used)
        self.assertEqual(moment, datetime(2026, 10, 14, 14, 59, 0, tzinfo=timezone.utc))

    def test_no_browser_time_means_server_time(self):
        for value in (None, 123, ["x"], {"a": 1}, True):
            with self.subTest(value=value):
                self.assertEqual(self.resolve(value), (S.NOW, False))

    def test_garbage_and_naive_times_mean_server_time(self):
        for value in ("", "yesterday", "2026-13-45T00:00:00Z", "2026-10-14T14:59:00", "2026-10-14"):
            with self.subTest(value=value):
                self.assertEqual(self.resolve(value), (S.NOW, False))

    def test_seven_days_old_is_accepted_and_one_second_older_is_not(self):
        self.assertTrue(self.resolve(self.iso(-timedelta(days=7)))[1])
        self.assertEqual(self.resolve(self.iso(-timedelta(days=7, seconds=1))), (S.NOW, False))
        self.assertEqual(core.OBSERVED_MAX_AGE, timedelta(days=7))

    def test_ten_minutes_ahead_is_accepted_and_one_second_more_is_not(self):
        self.assertTrue(self.resolve(self.iso(timedelta(minutes=10)))[1])
        self.assertEqual(self.resolve(self.iso(timedelta(minutes=10, seconds=1))), (S.NOW, False))
        self.assertEqual(core.OBSERVED_FUTURE_TOLERANCE, timedelta(minutes=10))

    def test_an_offset_time_is_converted_to_utc(self):
        moment, used = self.resolve("2026-10-14T10:30:00.000-04:00")
        self.assertTrue(used)
        self.assertEqual(moment, datetime(2026, 10, 14, 14, 30, 0, tzinfo=timezone.utc))
        self.assertEqual(moment.utcoffset(), timedelta(0))

    def test_the_result_has_millisecond_precision(self):
        moment, _ = self.resolve("2026-10-14T14:59:00.123Z")
        self.assertEqual(core.format_event_time(moment), "2026-10-14T14:59:00.123Z")
        server = datetime(2026, 10, 14, 15, 0, 0, 987654, tzinfo=timezone.utc)
        moment, used = core.resolve_event_time(None, server)
        self.assertFalse(used)
        self.assertEqual(core.format_event_time(moment), "2026-10-14T15:00:00.987Z")


class EventIdTests(unittest.TestCase):
    def test_the_event_id_is_a_pure_function_of_the_report_id(self):
        first = core.event_id_for(S.REPORT_ID)
        self.assertEqual(core.event_id_for(S.REPORT_ID), first)
        self.assertEqual(core.event_id_for(S.REPORT_ID), first)

    def test_different_report_ids_give_different_event_ids(self):
        self.assertNotEqual(core.event_id_for(S.REPORT_ID), core.event_id_for(S.REPORT_ID_2))

    def test_the_event_id_is_a_valid_ulid_but_not_the_report_id(self):
        event_id = core.event_id_for(S.REPORT_ID)
        self.assertTrue(core.is_ulid(event_id))
        self.assertNotEqual(event_id, S.REPORT_ID)

    def test_the_event_id_keeps_the_report_ids_timestamp(self):
        self.assertEqual(core.ulid_timestamp_ms(core.event_id_for(S.REPORT_ID)), core.ulid_timestamp_ms(S.REPORT_ID))

    def test_a_retried_request_carries_the_same_event_id(self):
        a = core.parse_request(body(), SECTORS)
        b = core.parse_request(body(description="Saw Darth Vader walking through the market.", observed_at=None), SECTORS)
        self.assertEqual(core.event_id_for(a.report_id), core.event_id_for(b.report_id))

    def test_the_ulid_encoding_matches_the_bridges(self):
        for stamp, noise in ((0, 0), (1_788_000_000_123, 2 ** 80 - 1), (2 ** 48 - 1, 12345678901234567890), (1_788_000_000_000, 1)):
            with self.subTest(stamp=stamp):
                self.assertEqual(core.new_ulid(stamp, noise), bridge.new_ulid(stamp, noise))

    def test_the_timestamp_decodes_back(self):
        for stamp in (0, 1, 1_788_000_000_123, 2 ** 48 - 1):
            self.assertEqual(core.ulid_timestamp_ms(core.new_ulid(stamp, 99)), stamp)


if __name__ == "__main__":
    unittest.main()
