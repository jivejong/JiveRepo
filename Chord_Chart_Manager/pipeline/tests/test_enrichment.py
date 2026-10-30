from __future__ import annotations

import json
import os
import re
import uuid
from urllib.parse import parse_qs, urlsplit, urlunsplit

import psycopg
import pytest
from psycopg.types.json import Jsonb

import enrich_songs
import load_songs as loader


GENRES = [(1, "Rock"), (2, "Pop"), (3, "Jazz"), (4, "Soul")]
VIBES = [(11, "Upbeat"), (12, "Mellow"), (13, "Danceable"), (14, "Romantic")]
CONTACT = "owner@example.com"
FAKE_KEY = "fake-gemini-key-for-tests"
MBID_A = "11111111-1111-4111-8111-111111111111"
MBID_B = "22222222-2222-4222-8222-222222222222"
MBID_C = "33333333-3333-4333-8333-333333333333"
MBID_D = "44444444-4444-4444-8444-444444444444"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeHttp:
    """Records every request; answers by host. No network is ever used."""

    def __init__(self, clock: FakeClock, *, mb=None, ab=None, gemini=None) -> None:
        self.clock = clock
        self.calls: list[dict] = []
        self.mb = mb or (lambda call: (200, {"recordings": []}))
        self.ab = ab or (lambda call: (404, {"message": "Not found"}))
        self.gemini = gemini or self._unexpected_gemini

    @staticmethod
    def _unexpected_gemini(call):
        raise AssertionError("unexpected Gemini call")

    def __call__(self, method, url, headers, body, timeout):
        call = {"method": method, "url": url, "headers": dict(headers), "body": body,
                "timeout": timeout, "at": self.clock()}
        self.calls.append(call)
        host = urlsplit(url).hostname
        handler = {"musicbrainz.org": self.mb, "acousticbrainz.org": self.ab,
                   "generativelanguage.googleapis.com": self.gemini}[host]
        status, payload = handler(call)
        if isinstance(payload, (bytes, bytearray)):
            return status, bytes(payload)
        return status, json.dumps(payload).encode("utf-8")

    def to(self, host: str) -> list[dict]:
        return [call for call in self.calls if urlsplit(call["url"]).hostname == host]


def recording(mbid, title, artist, score, date=None, genres=(), tags=()):
    item = {"id": mbid, "score": score, "title": title,
            "artist-credit": [{"name": artist, "joinphrase": ""}]}
    if date:
        item["first-release-date"] = date
    if genres:
        item["genres"] = [{"name": name, "count": 5} for name in genres]
    if tags:
        item["tags"] = [{"name": name, "count": 1} for name in tags]
    return item


def gemini_reply(**values):
    answer = {"release_year": None, "bpm": None, "original_key": None, "genres": [], "vibes": [], **values}
    return 200, {"status": "completed",
                 "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(answer)}]}]}


def gemini_request(call) -> dict:
    return json.loads(call["body"].decode("utf-8"))


def gemini_input(call) -> dict:
    text = gemini_request(call)["input"]
    return json.loads(text[text.index("{"):])


def song(song_id=1, title="Do It Again", artist="Steely Dan", *, release_year=None, original_key=None,
         bpm=None, genres=(), secondary=False, has_vibe=False):
    return enrich_songs.Song(id=song_id, title=title, artist=artist, release_year=release_year,
                             original_key=original_key, bpm=bpm, genres=tuple(genres),
                             has_secondary_genre=secondary, has_vibe=has_vibe)


def enricher(http, clock, **settings):
    values = {"musicbrainz_contact": CONTACT, "gemini_api_key": FAKE_KEY, **settings}
    return enrich_songs.Enricher(enrich_songs.Settings(**values), genres=GENRES, vibes=VIBES,
                                 http=http, sleep=clock.sleep, clock=clock)


def complete(**overrides):
    # A complete song: all scalars set, a primary plus a secondary Genre, and a Vibe.
    values = {"release_year": 1972, "original_key": "G", "bpm": 120, "genres": ("Rock", "Pop"),
              "secondary": True, "has_vibe": True}
    return song(**{**values, **overrides})


# FR-113
def test_best_score_above_minimum_wins_and_year_is_earliest_among_matching_recordings() -> None:
    clock = FakeClock()
    page = {"recordings": [
        "not a recording",
        {"score": 100, "title": "Do It Again"},
        {"id": MBID_D, "score": "abc", "title": "Do It Again"},
        recording(MBID_A, "Do It Again", "Steely Dan", 100, "1973-01-15"),
        recording(MBID_D, "Do It Again", "The Beach Boys", 98, "1968-07-08"),
        recording(MBID_B, "Do it again!", "Steely Dan", 95, "1972-11"),
        recording(MBID_C, "Do It Again", "Steely Dan", 92, "1985"),
    ]}
    http = FakeHttp(clock, mb=lambda call: (200, page))
    outcome = enricher(http, clock).lookup(complete(release_year=None))

    assert outcome.filled == {"release_year": (1972, "MusicBrainz")}
    assert outcome.left == {}
    assert len(http.to("musicbrainz.org")) == 1
    assert http.to("acousticbrainz.org") == []
    query = parse_qs(urlsplit(http.calls[0]["url"]).query)
    assert query["query"] == ['recording:"Do It Again" AND artist:"Steely Dan"']
    assert query["fmt"] == ["json"]


# FR-113
def test_no_recording_at_minimum_score_falls_back_to_gemini() -> None:
    clock = FakeClock()
    page = {"recordings": [recording(MBID_A, "Do It Again", "Steely Dan", 80, "1972")]}
    http = FakeHttp(clock, mb=lambda call: (200, page), gemini=lambda call: gemini_reply(release_year=1972))
    outcome = enricher(http, clock).lookup(complete(release_year=None))

    assert outcome.filled == {"release_year": (1972, "Gemini")}
    assert len(http.to("generativelanguage.googleapis.com")) == 1
    assert http.to("acousticbrainz.org") == []


# FR-113
def test_a_page_with_no_usable_entries_is_no_match_not_a_failure() -> None:
    clock = FakeClock()
    http = FakeHttp(clock, mb=lambda call: (200, {"recordings": "garbage"}),
                    gemini=lambda call: gemini_reply(release_year=1990))
    run = enricher(http, clock)
    outcome = run.lookup(complete(release_year=None))
    assert outcome.filled == {"release_year": (1990, "Gemini")}
    assert run.notes == []


# FR-103
def test_acousticbrainz_fills_key_and_bpm_for_the_chosen_recording() -> None:
    clock = FakeClock()
    page = {"recordings": [recording(MBID_A, "Do It Again", "Steely Dan", 100, "1972")]}
    lowlevel = {"rhythm": {"bpm": 117.6}, "tonal": {"key_key": "F#", "key_scale": "minor"}}
    http = FakeHttp(clock, mb=lambda call: (200, page), ab=lambda call: (200, lowlevel))
    outcome = enricher(http, clock).lookup(complete(original_key=None, bpm=None))

    assert outcome.filled == {"original_key": ("F#m", "AcousticBrainz"), "bpm": (118, "AcousticBrainz")}
    assert [urlsplit(call["url"]).path for call in http.to("acousticbrainz.org")] == [f"/api/v1/{MBID_A}/low-level"]


# FR-103
def test_acousticbrainz_without_data_leaves_the_gap_for_gemini() -> None:
    clock = FakeClock()
    page = {"recordings": [recording(MBID_A, "Do It Again", "Steely Dan", 100, "1972")]}
    http = FakeHttp(clock, mb=lambda call: (200, page),
                    gemini=lambda call: gemini_reply(bpm=96, original_key={"key_key": "Bb", "key_scale": "major"}))
    outcome = enricher(http, clock).lookup(complete(original_key=None, bpm=None))

    assert outcome.filled == {"original_key": ("Bb", "Gemini"), "bpm": (96, "Gemini")}
    assert len(http.to("acousticbrainz.org")) == 1


# FR-103
def test_malformed_acousticbrainz_values_are_ignored() -> None:
    clock = FakeClock()
    page = {"recordings": [recording(MBID_A, "Do It Again", "Steely Dan", 100, "1972")]}
    lowlevel = {"rhythm": {"bpm": -4}, "tonal": {"key_key": "H", "key_scale": "minor"}}
    http = FakeHttp(clock, mb=lambda call: (200, page), ab=lambda call: (200, lowlevel),
                    gemini=lambda call: gemini_reply(bpm=100, original_key={"key_key": "C", "key_scale": "minor"}))
    outcome = enricher(http, clock).lookup(complete(original_key=None, bpm=None))
    assert outcome.filled == {"original_key": ("Cm", "Gemini"), "bpm": (100, "Gemini")}


# FR-103
def test_musicbrainz_genres_fill_an_empty_genre_category_from_managed_names_only() -> None:
    clock = FakeClock()
    page = {"recordings": [recording(MBID_A, "Do It Again", "Steely Dan", 100, "1972",
                                     genres=("jazz", "classic  rock", "yacht rock"), tags=("rock",))]}
    http = FakeHttp(clock, mb=lambda call: (200, page))
    outcome = enricher(http, clock).lookup(complete(genres=(), secondary=False))

    assert outcome.genres == [("Jazz", "MusicBrainz"), ("Rock", "MusicBrainz")]
    assert outcome.vibes == []
    assert http.to("generativelanguage.googleapis.com") == []


# FR-092 FR-115
def test_a_complete_song_makes_no_requests() -> None:
    clock = FakeClock()
    http = FakeHttp(clock)
    outcome = enricher(http, clock).lookup(complete())
    assert http.calls == []
    assert outcome.filled == {} and outcome.genres == [] and outcome.vibes == [] and outcome.left == {}


# FR-092 FR-115
def test_only_missing_fields_are_asked_for_and_kept() -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: gemini_reply(
        release_year=1999, bpm=88, original_key={"key_key": "D", "key_scale": "major"}, genres=["Pop"]))
    outcome = enricher(http, clock).lookup(complete(bpm=None))

    assert outcome.filled == {"bpm": (88, "Gemini")}
    assert outcome.genres == []
    request = gemini_request(http.to("generativelanguage.googleapis.com")[0])
    properties = request["response_format"]["schema"]["properties"]
    assert properties["release_year"] == {"type": "null"}
    assert properties["original_key"] == {"type": "null"}
    assert properties["bpm"] != {"type": "null"}
    assert gemini_input(http.calls[-1])["requested_missing_fields"] == ["bpm"]


# FR-103
def test_a_song_with_a_secondary_genre_or_any_vibe_gets_no_new_labels() -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: gemini_reply(genres=["Jazz"], vibes=["Mellow"]))
    outcome = enricher(http, clock).lookup(complete(has_vibe=False))

    assert outcome.genres == []
    assert outcome.vibes == [("Mellow", "Gemini")]
    allowed = gemini_input(http.to("generativelanguage.googleapis.com")[0])["allowed_names"]
    assert allowed == {"genres": [], "vibes": ["Upbeat", "Mellow", "Danceable", "Romantic"]}

    quiet = FakeHttp(clock)
    assert enricher(quiet, clock).lookup(complete(genres=("Rock",), secondary=True)).genres == []
    assert quiet.calls == []


# FR-103
def test_a_song_whose_only_genre_is_its_primary_gets_secondary_genres_up_to_three_in_total() -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: gemini_reply(genres=["Rock", "Pop", "Jazz", "Soul"]))
    outcome = enricher(http, clock).lookup(complete(genres=("Rock",), secondary=False))

    assert outcome.genres == [("Pop", "Gemini"), ("Jazz", "Gemini")]
    request = http.to("generativelanguage.googleapis.com")[0]
    assert gemini_input(request)["allowed_names"]["genres"] == ["Pop", "Jazz", "Soul"]
    assert gemini_request(request)["response_format"]["schema"]["properties"]["genres"]["maxItems"] == 2


# FR-103
def test_musicbrainz_tags_for_the_primary_genre_are_not_added_again() -> None:
    clock = FakeClock()
    page = {"recordings": [recording(MBID_A, "Do It Again", "Steely Dan", 100, "1972",
                                     genres=("rock", "jazz", "soul"))]}
    http = FakeHttp(clock, mb=lambda call: (200, page))
    outcome = enricher(http, clock, max_genres=2).lookup(complete(genres=("Rock",), secondary=False))
    assert outcome.genres == [("Jazz", "MusicBrainz")]
    assert http.to("generativelanguage.googleapis.com") == []


# FR-103
def test_a_primary_only_song_is_left_alone_when_the_genre_limit_is_already_met() -> None:
    clock = FakeClock()
    http = FakeHttp(clock)
    outcome = enricher(http, clock, max_genres=1).lookup(complete(genres=("Rock",), secondary=False))
    assert outcome.genres == [] and outcome.left == {}
    assert http.calls == []


# FR-092 FR-103
def test_unknown_duplicate_and_excess_labels_are_dropped() -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: gemini_reply(
        genres=["Polka", "Rock", "rock", "Soul", "Jazz", "Pop"],
        vibes=["Upbeat", "Brooding", "Romantic"]))
    outcome = enricher(http, clock, max_genres=3, max_vibes=1).lookup(complete(genres=(), secondary=False, has_vibe=False))

    assert outcome.genres == [("Rock", "Gemini"), ("Soul", "Gemini"), ("Jazz", "Gemini")]
    assert outcome.vibes == [("Upbeat", "Gemini")]


# FR-092
@pytest.mark.parametrize("answer", [
    {"release_year": 0}, {"release_year": 40000}, {"release_year": "1972"}, {"release_year": 1972.5},
    {"bpm": 0}, {"bpm": 400}, {"bpm": "fast"},
    {"original_key": {"key_key": "H", "key_scale": "major"}},
    {"original_key": {"key_key": "C", "key_scale": "dorian"}},
    {"original_key": "C"},
])
def test_invalid_gemini_values_are_not_kept(answer) -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: gemini_reply(**answer))
    outcome = enricher(http, clock).lookup(complete(release_year=None, bpm=None, original_key=None))
    assert outcome.filled == {}
    assert set(outcome.left) == {"release_year", "original_key", "bpm"}


# FR-103
@pytest.mark.parametrize("reply", [
    (200, b"not json"),
    (200, {"status": "failed", "steps": []}),
    (200, {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": "{oops"}]}]}),
    (500, {"error": "boom"}),
])
def test_a_failed_gemini_answer_leaves_the_gaps_reported(reply) -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: reply)
    run = enricher(http, clock)
    outcome = run.lookup(complete(release_year=None, has_vibe=False))
    assert outcome.filled == {} and outcome.vibes == []
    assert set(outcome.left) == {"release_year", "vibes"}
    assert run.gemini_calls == 1


# FR-093
def test_requests_carry_identity_and_managed_names_only() -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: gemini_reply(release_year=1972))
    enricher(http, clock).lookup(complete(release_year=None, has_vibe=False))

    request = gemini_request(http.to("generativelanguage.googleapis.com")[0])
    assert sorted(request) == ["generation_config", "input", "model", "response_format", "store", "system_instruction"]
    assert gemini_input(http.calls[-1]) == {
        "title": "Do It Again", "artist": "Steely Dan",
        "requested_missing_fields": ["release_year"],
        "allowed_names": {"genres": [], "vibes": ["Upbeat", "Mellow", "Danceable", "Romantic"]},
    }
    assert request["store"] is False
    assert request["model"] == "gemini-3.1-flash-lite"


# FR-093
def test_the_song_query_reads_no_chart_or_lyric_column() -> None:
    query = enrich_songs.SONG_QUERY.lower()
    assert "chart" not in query and "lyric" not in query and "*" not in query


# FR-112
def test_gemini_cap_stops_calls_and_reports_the_remaining_songs() -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: gemini_reply(release_year=2001))
    run = enricher(http, clock, gemini_cap=2)
    outcomes = [run.lookup(complete(song_id=n, release_year=None)) for n in range(1, 6)]

    assert len(http.to("generativelanguage.googleapis.com")) == 2
    assert run.gemini_calls == 2
    assert [bool(item.filled) for item in outcomes] == [True, True, False, False, False]
    assert all("cap" in item.left["release_year"] for item in outcomes[2:])
    assert len(http.to("musicbrainz.org")) == 5


# NFR-011
def test_musicbrainz_requests_are_spaced_and_identify_the_app_and_contact() -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: gemini_reply(release_year=2001))
    run = enricher(http, clock)
    for n in range(1, 4):
        run.lookup(complete(song_id=n, release_year=None))

    times = [call["at"] for call in http.to("musicbrainz.org")]
    assert all(later - earlier >= 1.0 for earlier, later in zip(times, times[1:]))
    agent = http.to("musicbrainz.org")[0]["headers"]["User-Agent"]
    assert re.fullmatch(r"ChordChartManager-pipeline/\S+ \( owner@example\.com \)", agent)


# NFR-011
def test_provider_requests_use_the_twenty_second_timeout_and_gemini_bounds() -> None:
    clock = FakeClock()
    page = {"recordings": [recording(MBID_A, "Do It Again", "Steely Dan", 100, "1972")]}
    http = FakeHttp(clock, mb=lambda call: (200, page),
                    gemini=lambda call: gemini_reply(bpm=100))
    enricher(http, clock).lookup(complete(bpm=None))

    assert {call["timeout"] for call in http.calls} == {20}
    gemini_call = http.to("generativelanguage.googleapis.com")[0]
    assert len(gemini_call["body"]) <= 16_384
    assert gemini_request(gemini_call)["generation_config"] == {"max_output_tokens": 1024}


# NFR-011
def test_an_oversized_gemini_request_is_not_sent() -> None:
    clock = FakeClock()
    http = FakeHttp(clock)
    many = [(1000 + n, f"Vibe label number {n} " + "x" * 40) for n in range(400)]
    run = enrich_songs.Enricher(enrich_songs.Settings(musicbrainz_contact=CONTACT, gemini_api_key=FAKE_KEY),
                                genres=GENRES, vibes=many, http=http, sleep=clock.sleep, clock=clock)
    outcome = run.lookup(complete(has_vibe=False))
    assert http.to("generativelanguage.googleapis.com") == []
    assert "too large" in outcome.left["vibes"]


# NFR-011
def test_without_a_contact_musicbrainz_and_acousticbrainz_are_skipped() -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: gemini_reply(release_year=1972))
    run = enricher(http, clock, musicbrainz_contact="")
    outcome = run.lookup(complete(release_year=None))

    assert http.to("musicbrainz.org") == [] and http.to("acousticbrainz.org") == []
    assert outcome.filled == {"release_year": (1972, "Gemini")}
    assert any("MUSICBRAINZ_CONTACT" in note for note in run.notes)


# FR-112
def test_without_a_gemini_key_gaps_are_reported_and_nothing_is_sent() -> None:
    clock = FakeClock()
    http = FakeHttp(clock)
    run = enricher(http, clock, gemini_api_key="")
    outcome = run.lookup(complete(release_year=None))
    assert http.to("generativelanguage.googleapis.com") == []
    assert "GEMINI_API_KEY" in outcome.left["release_year"]


# FR-103
def test_consecutive_provider_failures_stop_calls_to_that_provider() -> None:
    clock = FakeClock()
    http = FakeHttp(clock, mb=lambda call: (503, {"error": "busy"}),
                    gemini=lambda call: gemini_reply(release_year=1980))
    run = enricher(http, clock, outage_failures=3)
    outcomes = [run.lookup(complete(song_id=n, release_year=None)) for n in range(1, 6)]

    assert len(http.to("musicbrainz.org")) == 3
    assert all(item.filled == {"release_year": (1980, "Gemini")} for item in outcomes)
    assert any("MusicBrainz" in note and "stopped" in note for note in run.notes)


# FR-103
def test_a_single_failure_does_not_stop_a_provider() -> None:
    clock = FakeClock()
    replies = iter([(503, {}), (200, {"recordings": []}), (503, {}), (503, {}), (200, {"recordings": []})])
    http = FakeHttp(clock, mb=lambda call: next(replies), gemini=lambda call: gemini_reply(release_year=1980))
    run = enricher(http, clock, outage_failures=3)
    for n in range(1, 6):
        run.lookup(complete(song_id=n, release_year=None))
    assert len(http.to("musicbrainz.org")) == 5


# NFR-009 FR-102
def test_the_key_appears_only_in_its_header_and_never_in_the_report(capsys) -> None:
    clock = FakeClock()
    http = FakeHttp(clock, gemini=lambda call: gemini_reply(release_year=1972, vibes=["Mellow"]))
    run = enricher(http, clock)
    outcome = run.lookup(complete(release_year=None, has_vibe=False))
    result = enrich_songs.RunResult(documents=["Lite.docx"], outcomes=[outcome], gemini_calls=run.gemini_calls,
                                    gemini_cap=1000, notes=run.notes, started_at="2026-10-29 19:00:00 UTC")
    report = enrich_songs.render_report(result)
    print("\n".join(enrich_songs.summary_lines(result)))

    for call in http.calls:
        assert FAKE_KEY not in call["url"]
        assert call["body"] is None or FAKE_KEY.encode() not in call["body"]
        for name, value in call["headers"].items():
            assert FAKE_KEY not in value or name == "x-goog-api-key"
    assert http.to("generativelanguage.googleapis.com")[0]["headers"]["x-goog-api-key"] == FAKE_KEY
    assert FAKE_KEY not in report and FAKE_KEY not in capsys.readouterr().out
    assert FAKE_KEY not in repr(enrich_songs.Settings(gemini_api_key=FAKE_KEY))
    assert "Do It Again / Steely Dan" in report and "release_year=1972 (Gemini)" in report


# NFR-011
def test_urllib_adapter_bounds_responses_and_maps_errors(monkeypatch) -> None:
    import io
    import urllib.error

    class Response(io.BytesIO):
        status = 200

    seen = {}

    def ok(request, timeout):
        seen.update(url=request.full_url, method=request.get_method(), timeout=timeout,
                    agent=request.get_header("User-agent"))
        return Response(b'{"ok": true}')

    monkeypatch.setattr(enrich_songs.urllib.request, "urlopen", ok)
    assert enrich_songs.urllib_http("GET", "https://musicbrainz.org/ws/2/recording/?x=1",
                                    {"User-Agent": "UA"}, None, 20) == (200, b'{"ok": true}')
    assert seen == {"url": "https://musicbrainz.org/ws/2/recording/?x=1", "method": "GET", "timeout": 20, "agent": "UA"}

    monkeypatch.setattr(enrich_songs.urllib.request, "urlopen",
                        lambda request, timeout: Response(b"x" * (enrich_songs.MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(enrich_songs.ProviderError):
        enrich_songs.urllib_http("GET", "https://acousticbrainz.org/", {}, None, 20)

    def not_found(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(enrich_songs.urllib.request, "urlopen", not_found)
    assert enrich_songs.urllib_http("GET", "https://acousticbrainz.org/", {}, None, 20) == (404, b"{}")

    def no_body(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, None)

    monkeypatch.setattr(enrich_songs.urllib.request, "urlopen", no_body)
    assert enrich_songs.urllib_http("GET", "https://musicbrainz.org/", {}, None, 20) == (503, b"")

    def timed_out(request, timeout):
        raise TimeoutError(f"timed out calling {request.full_url} with {FAKE_KEY}")

    monkeypatch.setattr(enrich_songs.urllib.request, "urlopen", timed_out)
    with pytest.raises(enrich_songs.ProviderError) as raised:
        enrich_songs.urllib_http("POST", enrich_songs.GEMINI_URL, {"x-goog-api-key": FAKE_KEY}, b"{}", 20)
    assert FAKE_KEY not in str(raised.value)


# FR-112 NFR-011
def test_settings_defaults_and_environment() -> None:
    defaults = enrich_songs.Settings.from_env({})
    assert (defaults.gemini_cap, defaults.mb_min_score, defaults.max_genres, defaults.max_vibes,
            defaults.outage_failures, defaults.timeout_seconds) == (1000, 90, 3, 3, 3, 20)
    configured = enrich_songs.Settings.from_env({
        "MUSICBRAINZ_CONTACT": " me@example.com ", "GEMINI_API_KEY": FAKE_KEY, "ENRICH_GEMINI_CAP": "5",
        "ENRICH_MB_MIN_SCORE": "80", "ENRICH_MAX_GENRES": "2", "ENRICH_MAX_VIBES": "1"})
    assert (configured.musicbrainz_contact, configured.gemini_cap, configured.mb_min_score,
            configured.max_genres, configured.max_vibes) == ("me@example.com", 5, 80, 2, 1)
    with pytest.raises(ValueError):
        enrich_songs.Settings.from_env({"ENRICH_GEMINI_CAP": "-1"})


# FR-101 FR-104
def test_loader_enriches_only_when_asked_and_only_after_a_committed_load(tmp_path, monkeypatch, capsys) -> None:
    songs_json = tmp_path / "songs.json"
    songs_json.write_text("[]", encoding="utf-8")
    songs = [{"source_document": "B.docx"}, {"source_document": "A.docx"}, {"source_document": "B.docx"},
             {"source_document": None}]
    monkeypatch.setattr(loader, "_validate_batch", lambda data: ([], []))
    monkeypatch.setattr(loader.json, "load", lambda handle: songs)
    monkeypatch.setattr(loader.psycopg, "connect", lambda *args, **kwargs: _DummyConnection())
    committed = {"state": "committed", "counts": {key: 0 for key in (
        "proposed_inserts", "exact_skips", "blocking_collisions", "unresolved_categories",
        "parser_validation_failures")}, "proposed_inserts": [], "exact_skips": [], "blocking_collisions": [],
        "unresolved_categories": [], "parser_validation_failures": []}
    monkeypatch.setattr(loader, "load", lambda conn, data, dry_run=False: committed)
    seen = []
    monkeypatch.setattr(enrich_songs, "enrich_and_report",
                        lambda conn, documents, report_dir: seen.append((documents, report_dir)) or 0)

    monkeypatch.setattr("sys.argv", ["load_songs.py", str(songs_json), "--database-url", "postgresql://x/y"])
    assert loader.main() == 0
    assert seen == []

    monkeypatch.setattr("sys.argv", ["load_songs.py", str(songs_json), "--database-url", "postgresql://x/y", "--enrich"])
    assert loader.main() == 0
    assert seen == [(["A.docx", "B.docx"], tmp_path)]

    monkeypatch.setattr("sys.argv", ["load_songs.py", str(songs_json), "--dry-run", "--enrich"])
    assert loader.main() == 2
    assert len(seen) == 1


class _DummyConnection:
    def close(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _scratch_database_url() -> str:
    database = os.environ.get("PIPELINE_TEST_DATABASE", "")
    if not re.fullmatch(r"ccm_loader_c1_[a-z0-9_]{1,32}", database):
        raise RuntimeError("Set PIPELINE_TEST_DATABASE to this run's ccm_loader_c1_<suffix> database.")
    base = os.environ.get("DATABASE_URL")
    if not base:
        raise RuntimeError("Compose DATABASE_URL is required for the scratch test.")
    parts = urlsplit(base)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


# FR-092 FR-093 FR-101 FR-103 FR-115
def test_scratch_database_fills_only_gaps_for_the_run_documents_and_rerun_is_a_no_op(tmp_path) -> None:
    tag = uuid.uuid4().hex[:8]
    document, other_document = f"enrich-pytest-{tag}.docx", f"enrich-pytest-other-{tag}.docx"
    chart_text = f"[Verse]\nsecret lyric line {tag}\n"
    genre_name, vibe_name, kept_genre = f"Enrich Genre {tag}", f"Enrich Vibe {tag}", f"Kept Genre {tag}"

    with psycopg.connect(_scratch_database_url(), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            assert cur.fetchone()[0] == os.environ["PIPELINE_TEST_DATABASE"]
            cur.execute("INSERT INTO artists(name) VALUES (%s) RETURNING id", (f"Enrich artist {tag}",))
            artist_id = cur.fetchone()[0]
            ids = {}
            for name in (genre_name, kept_genre):
                cur.execute("INSERT INTO genres(name) VALUES (%s) RETURNING id", (name,))
                ids[name] = cur.fetchone()[0]
            cur.execute("INSERT INTO vibes(name) VALUES (%s) RETURNING id", (vibe_name,))
            ids[vibe_name] = cur.fetchone()[0]

            def insert(title, source, **values):
                cur.execute(
                    """INSERT INTO songs(title,artist_id,chart_content,chart_source,source_document,
                                         release_year,original_key,bpm)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (title, artist_id, Jsonb({"sections": []}), chart_text, source,
                     values.get("release_year"), values.get("original_key"), values.get("bpm")))
                return cur.fetchone()[0]

            empty = insert("Empty song", document)
            partial = insert("Partial song", document, release_year=1999)
            cur.execute("INSERT INTO song_genres(song_id,genre_id,is_primary) VALUES (%s,%s,true)",
                        (partial, ids[kept_genre]))
            full = insert("Full song", document, release_year=1970, original_key="E", bpm=90)
            cur.execute("INSERT INTO song_genres(song_id,genre_id,is_primary) VALUES (%s,%s,true)",
                        (full, ids[kept_genre]))
            cur.execute("INSERT INTO song_genres(song_id,genre_id,is_primary) VALUES (%s,%s,false)",
                        (full, ids[genre_name]))
            cur.execute("INSERT INTO song_vibes(song_id,vibe_id) VALUES (%s,%s)", (full, ids[vibe_name]))
            outside = insert("Other document song", other_document)
        conn.commit()

        def snapshot():
            with conn.cursor() as cur:
                cur.execute("""SELECT id, release_year, original_key, bpm, updated_at, chart_source,
                                      (SELECT array_agg(genre_id || ':' || is_primary ORDER BY genre_id)
                                         FROM song_genres g WHERE g.song_id=s.id),
                                      (SELECT array_agg(vibe_id ORDER BY vibe_id) FROM song_vibes v WHERE v.song_id=s.id)
                               FROM songs s WHERE id = ANY(%s) ORDER BY id""",
                            ([empty, partial, full, outside],))
                rows = {row[0]: row[1:] for row in cur.fetchall()}
            conn.commit()
            return rows

        before = snapshot()
        clock = FakeClock()
        http = FakeHttp(clock, gemini=lambda call: gemini_reply(
            release_year=2001, bpm=101, original_key={"key_key": "A", "key_scale": "minor"},
            genres=[genre_name], vibes=[vibe_name]))
        settings = enrich_songs.Settings(musicbrainz_contact=CONTACT, gemini_api_key=FAKE_KEY)
        result = enrich_songs.run(conn, [document], settings, http=http, sleep=clock.sleep, clock=clock)
        after = snapshot()

        assert after[empty][:3] == (2001, "Am", 101)
        assert after[empty][5] == [f"{ids[genre_name]}:false"] and after[empty][6] == [ids[vibe_name]]
        assert after[partial][:3] == (1999, "Am", 101)
        # The primary stays primary; the secondary Genre is added beside it (FR-103).
        assert after[partial][5] == [f"{ids[genre_name]}:false", f"{ids[kept_genre]}:true"]
        assert after[partial][6] == [ids[vibe_name]]
        assert after[full] == before[full]
        assert after[outside] == before[outside]
        assert all(row[4] == chart_text for row in after.values())
        assert after[empty][3] > before[empty][3]
        for call in http.calls:
            assert "secret lyric" not in call["url"]
            assert call["body"] is None or b"secret lyric" not in call["body"]
        assert {item.song.id for item in result.outcomes} == {empty, partial, full}
        report = enrich_songs.render_report(result)
        assert "secret lyric" not in report

        rerun_http = FakeHttp(clock)
        enrich_songs.run(conn, [document], settings, http=rerun_http, sleep=clock.sleep, clock=clock)
        assert rerun_http.calls == []
        assert snapshot() == after


# FR-092 FR-103
def test_scratch_database_write_never_overwrites_a_value_or_category_filled_meanwhile() -> None:
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(_scratch_database_url(), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO artists(name) VALUES (%s) RETURNING id", (f"Race artist {tag}",))
            artist_id = cur.fetchone()[0]
            cur.execute("INSERT INTO genres(name) VALUES (%s) RETURNING id", (f"Race Genre {tag}",))
            genre_id = cur.fetchone()[0]
            cur.execute("INSERT INTO genres(name) VALUES (%s) RETURNING id", (f"User Genre {tag}",))
            user_genre_id = cur.fetchone()[0]
            cur.execute("""INSERT INTO songs(title,artist_id,chart_content,source_document)
                           VALUES (%s,%s,%s,%s) RETURNING id""",
                        (f"Race song {tag}", artist_id, Jsonb({"sections": []}), f"race-{tag}.docx"))
            song_id = cur.fetchone()[0]
        conn.commit()

        outcome = enrich_songs.Outcome(song(song_id, f"Race song {tag}", f"Race artist {tag}"))
        outcome.filled = {"release_year": (2001, "Gemini"), "bpm": (101, "Gemini")}
        outcome.genres = [(f"Race Genre {tag}", "Gemini")]
        with conn.cursor() as cur:
            # A user edit lands between the lookup and the write.
            cur.execute("UPDATE songs SET bpm=77 WHERE id=%s", (song_id,))
            cur.execute("INSERT INTO song_genres(song_id,genre_id,is_primary) VALUES (%s,%s,false)",
                        (song_id, user_genre_id))
        conn.commit()

        enrich_songs._write(conn, outcome, {f"Race Genre {tag}": genre_id}, {})
        with conn.cursor() as cur:
            cur.execute("SELECT release_year, bpm FROM songs WHERE id=%s", (song_id,))
            assert cur.fetchone() == (2001, 77)
            cur.execute("SELECT genre_id, is_primary FROM song_genres WHERE song_id=%s", (song_id,))
            assert cur.fetchall() == [(user_genre_id, False)]
        conn.commit()
        assert outcome.filled == {"release_year": (2001, "Gemini")}
        assert outcome.genres == []
        assert "another change" in outcome.left["bpm"] and "another change" in outcome.left["genres"]


# FR-092 FR-103 FR-109
def test_scratch_database_genres_beside_a_primary_respect_the_total_limit_and_keep_the_primary() -> None:
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(_scratch_database_url(), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO artists(name) VALUES (%s) RETURNING id", (f"Cap artist {tag}",))
            artist_id = cur.fetchone()[0]
            genre_ids = {}
            for label in ("Primary", "One", "Two", "Three"):
                cur.execute("INSERT INTO genres(name) VALUES (%s) RETURNING id", (f"Cap {label} {tag}",))
                genre_ids[label] = cur.fetchone()[0]
            cur.execute("""INSERT INTO songs(title,artist_id,chart_content,source_document)
                           VALUES (%s,%s,%s,%s) RETURNING id""",
                        (f"Cap song {tag}", artist_id, Jsonb({"sections": []}), f"cap-{tag}.docx"))
            song_id = cur.fetchone()[0]
            cur.execute("INSERT INTO song_genres(song_id,genre_id,is_primary) VALUES (%s,%s,true)",
                        (song_id, genre_ids["Primary"]))
        conn.commit()

        names = {f"Cap {label} {tag}": genre_ids[label] for label in genre_ids}
        outcome = enrich_songs.Outcome(song(song_id, f"Cap song {tag}", f"Cap artist {tag}"))
        # A lookup that (wrongly or stale) returns the primary and three more must still stop at 3 in total.
        outcome.genres = [(f"Cap {label} {tag}", "Gemini") for label in ("Primary", "One", "Two", "Three")]
        enrich_songs._write(conn, outcome, names, {}, 3)

        with conn.cursor() as cur:
            cur.execute("SELECT genre_id, is_primary FROM song_genres WHERE song_id=%s ORDER BY genre_id", (song_id,))
            rows = cur.fetchall()
        conn.commit()
        assert rows == [(genre_ids["Primary"], True), (genre_ids["One"], False), (genre_ids["Two"], False)]
        assert [label for label, _ in outcome.genres] == [f"Cap One {tag}", f"Cap Two {tag}"]
