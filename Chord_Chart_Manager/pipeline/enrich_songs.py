#!/usr/bin/env python3
"""
Per-document metadata enrichment for Chord Chart Manager.

`load_songs.py --enrich` calls this after a committed load, for every song whose
source_document is in that songs.json (FR-101), not only the new inserts.

* Fills only missing release_year, original_key and bpm, and a Genre or Vibe
  category only when the song has no assignment in it (FR-092, FR-103, Q29).
* MusicBrainz and AcousticBrainz first; Gemini as fallback (FR-103, FR-113).
* Never reads or sends chart text or lyrics (FR-093): the song query selects
  identity and metadata columns only.
* State lives in the song rows. A rerun reads no earlier output, fills only
  what is still missing, and is a no-op on a complete document (FR-115).
* The only safeguards are the host backup taken before the run (FR-104), the
  never-overwrite rule, and a per-run Gemini call cap (FR-112).
* Writes directly; the run leaves one human-readable report (FR-102).

Settings (environment): MUSICBRAINZ_CONTACT, GEMINI_API_KEY, ENRICH_GEMINI_CAP,
ENRICH_MB_MIN_SCORE, ENRICH_MAX_GENRES, ENRICH_MAX_VIBES. The Gemini key is sent
only in the x-goog-api-key header and never printed (NFR-009).
"""
from __future__ import annotations

import http.client
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import psycopg
from psycopg.pq import TransactionStatus

APP_AGENT = "ChordChartManager-pipeline/1.0"
MB_URL = "https://musicbrainz.org/ws/2/recording/"
AB_URL = "https://acousticbrainz.org/api/v1/{mbid}/low-level"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_MODEL = "gemini-3.1-flash-lite"
# NFR-011 provider bounds.
GEMINI_MAX_INPUT_BYTES = 16_384
GEMINI_MAX_OUTPUT_TOKENS = 1024
MAX_RESPONSE_BYTES = 1 << 20
FACTUAL_INTERVAL_SECONDS = 1.0

SCALARS = ("release_year", "original_key", "bpm")
KEY_NAMES = frozenset(["C", "B#", "C#", "Db", "D", "D#", "Eb", "E", "Fb", "E#", "F", "F#", "Gb",
                       "G", "G#", "Ab", "A", "A#", "Bb", "B", "Cb"])
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

MUSICBRAINZ, ACOUSTICBRAINZ, GEMINI = "MusicBrainz", "AcousticBrainz", "Gemini"

GEMINI_INSTRUCTION = (
    "Suggest only the missing scalar values listed in requested_missing_fields, and Genre or Vibe "
    "labels chosen only from the matching allowed_names lists. Vibes describe the subjective musical, "
    "listening or performance character of the song. Return an empty array when no label fits. Use "
    "null for any scalar you are unsure of or that was not requested. Give original_key as the key "
    "the original recording is in. Do not return explanations or fields outside the schema."
)

# Identity and metadata only: chart text and lyrics are never read (FR-093).
SONG_QUERY = """SELECT s.id, s.title, a.name, s.release_year, s.original_key, s.bpm,
       EXISTS (SELECT 1 FROM public.song_genres g WHERE g.song_id = s.id),
       EXISTS (SELECT 1 FROM public.song_vibes v WHERE v.song_id = s.id)
  FROM public.songs s JOIN public.artists a ON a.id = s.artist_id
 WHERE s.source_document = ANY(%s)
 ORDER BY s.id"""


@dataclass(frozen=True)
class Settings:
    musicbrainz_contact: str = ""
    gemini_api_key: str = field(default="", repr=False)
    gemini_cap: int = 1000
    mb_min_score: int = 90
    max_genres: int = 3
    max_vibes: int = 3
    outage_failures: int = 3
    timeout_seconds: int = 20

    def __post_init__(self) -> None:
        if self.gemini_cap < 0 or self.max_genres < 0 or self.max_vibes < 0:
            raise ValueError("enrichment limits must not be negative")
        if not 0 <= self.mb_min_score <= 100:
            raise ValueError("ENRICH_MB_MIN_SCORE must be between 0 and 100")
        if self.outage_failures < 1:
            raise ValueError("the provider outage threshold must be at least 1")

    @classmethod
    def from_env(cls, environ) -> "Settings":
        def number(name: str, default: int) -> int:
            raw = (environ.get(name) or "").strip()
            if not raw:
                return default
            if not re.fullmatch(r"\d+", raw):
                raise ValueError(f"{name} must be a whole number")
            return int(raw)

        return cls(
            musicbrainz_contact=(environ.get("MUSICBRAINZ_CONTACT") or "").strip(),
            gemini_api_key=(environ.get("GEMINI_API_KEY") or "").strip(),
            gemini_cap=number("ENRICH_GEMINI_CAP", cls.gemini_cap),
            mb_min_score=number("ENRICH_MB_MIN_SCORE", cls.mb_min_score),
            max_genres=number("ENRICH_MAX_GENRES", cls.max_genres),
            max_vibes=number("ENRICH_MAX_VIBES", cls.max_vibes),
        )


@dataclass(frozen=True)
class Song:
    """What enrichment knows about a song: identity and gaps, never chart text."""
    id: int
    title: str
    artist: str
    release_year: int | None
    original_key: str | None
    bpm: int | None
    has_genre: bool
    has_vibe: bool

    def missing(self) -> list[str]:
        return [name for name in SCALARS if getattr(self, name) is None]


@dataclass
class Outcome:
    song: Song
    filled: dict = field(default_factory=dict)    # field -> (value, source)
    genres: list = field(default_factory=list)    # (name, source)
    vibes: list = field(default_factory=list)     # (name, source)
    left: dict = field(default_factory=dict)      # field or "genres"/"vibes" -> reason

    @property
    def changed(self) -> bool:
        return bool(self.filled or self.genres or self.vibes)


@dataclass(frozen=True)
class Match:
    mbid: str
    year: int | None
    tags: tuple


@dataclass
class RunResult:
    documents: list
    outcomes: list
    gemini_calls: int
    gemini_cap: int
    notes: list
    started_at: str


class ProviderError(Exception):
    pass


def urllib_http(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _read_bounded(response)
    except urllib.error.HTTPError as error:
        try:
            data = _read_bounded(error) if error.fp is not None else b""
        except (OSError, http.client.HTTPException):
            data = b""
        finally:
            error.close()
        return error.code, data
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        # Only the exception type: messages may echo request details.
        raise ProviderError(type(error).__name__) from None


def _read_bounded(stream) -> bytes:
    data = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ProviderError("response larger than 1 MiB")
    return data


# --- Pure helpers ---------------------------------------------------------

def _match_text(value: str) -> str:
    """Loose comparison form: no accents, case, punctuation or extra spaces."""
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    plain = plain.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^\w]+", " ", plain).split())


def _label_key(value: str) -> str:
    # Same rule as load_songs._resolve_category.
    return re.sub(r"\s+", " ", value.strip()).lower()


def _lucene_phrase(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _credited_artist(recording: dict) -> str:
    credits = recording.get("artist-credit")
    if not isinstance(credits, list):
        return ""
    parts = []
    for credit in credits:
        if isinstance(credit, dict):
            parts.append(credit.get("name") if isinstance(credit.get("name"), str) else "")
            parts.append(credit.get("joinphrase") if isinstance(credit.get("joinphrase"), str) else "")
    return "".join(parts)


def _score(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _year_of(date) -> int | None:
    if not isinstance(date, str) or not re.match(r"\d{4}(?!\d)", date):
        return None
    return _valid_year(int(date[:4]))


def _valid_year(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= 32767 else None


def _valid_bpm(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    rounded = math.floor(value + 0.5)
    return rounded if 1 <= rounded <= 300 else None


def _valid_key(key_key, key_scale) -> str | None:
    if key_key not in KEY_NAMES or key_scale not in ("major", "minor"):
        return None
    return key_key + ("m" if key_scale == "minor" else "")


def choose_recording(payload, title: str, artist: str, min_score: int) -> Match | None:
    """FR-113: best score at or above the minimum; earliest year among matching recordings."""
    recordings = payload.get("recordings") if isinstance(payload, dict) else None
    if not isinstance(recordings, list):
        return None
    usable = []
    for item in recordings:
        if not isinstance(item, dict):
            continue
        mbid, name, score = item.get("id"), item.get("title"), _score(item.get("score"))
        if isinstance(mbid, str) and UUID_PATTERN.fullmatch(mbid) and isinstance(name, str) and score is not None:
            usable.append((score, item))
    eligible = [entry for entry in usable if entry[0] >= min_score]
    if not eligible:
        return None
    best = max(eligible, key=lambda entry: entry[0])[1]
    identity = (_match_text(best["title"]), _match_text(_credited_artist(best)))
    years = [year for _, item in usable
             if (_match_text(item["title"]), _match_text(_credited_artist(item))) == identity
             and (year := _year_of(item.get("first-release-date"))) is not None]
    tags = []
    for group in ("genres", "tags"):
        entries = best.get(group)
        if isinstance(entries, list):
            tags.extend(entry["name"] for entry in entries
                        if isinstance(entry, dict) and isinstance(entry.get("name"), str))
    return Match(mbid=best["id"].lower(), year=min(years) if years else None, tags=tuple(tags))


def acousticbrainz_values(payload) -> dict:
    values = {}
    if not isinstance(payload, dict):
        return values
    rhythm, tonal = payload.get("rhythm"), payload.get("tonal")
    if isinstance(rhythm, dict) and (bpm := _valid_bpm(rhythm.get("bpm"))) is not None:
        values["bpm"] = bpm
    if isinstance(tonal, dict) and (key := _valid_key(tonal.get("key_key"), tonal.get("key_scale"))) is not None:
        values["original_key"] = key
    return values


def _gemini_schema(requested: list[str], max_genres: int, max_vibes: int) -> dict:
    scalar = {
        "release_year": {"type": ["integer", "null"]},
        "bpm": {"type": ["number", "null"], "minimum": 0},
        "original_key": {"type": ["object", "null"], "properties": {
            "key_key": {"type": "string", "enum": sorted(KEY_NAMES)},
            "key_scale": {"type": "string", "enum": ["major", "minor"]},
        }, "required": ["key_key", "key_scale"], "additionalProperties": False},
    }
    properties = {name: scalar[name] if name in requested else {"type": "null"} for name in SCALARS}
    properties["genres"] = {"type": "array", "items": {"type": "string"}, "maxItems": max_genres}
    properties["vibes"] = {"type": "array", "items": {"type": "string"}, "maxItems": max_vibes}
    return {"type": "object", "properties": properties, "required": [*SCALARS, "genres", "vibes"],
            "additionalProperties": False}


def gemini_body(song: Song, requested: list[str], genres: list[str], vibes: list[str],
                settings: Settings) -> bytes:
    """FR-093: the only song data sent is its title and artist."""
    payload = {"title": song.title, "artist": song.artist, "requested_missing_fields": requested,
               "allowed_names": {"genres": genres, "vibes": vibes}}
    body = {
        "model": GEMINI_MODEL,
        "input": f"{GEMINI_INSTRUCTION}\n{json.dumps(payload, ensure_ascii=False)}",
        "system_instruction": GEMINI_INSTRUCTION,
        "store": False,
        "response_format": {"type": "text", "mime_type": "application/json",
                            "schema": _gemini_schema(requested, settings.max_genres if genres else 0,
                                                     settings.max_vibes if vibes else 0)},
        "generation_config": {"max_output_tokens": GEMINI_MAX_OUTPUT_TOKENS},
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def gemini_answer(body: bytes) -> dict | None:
    try:
        envelope = json.loads(body)
    except ValueError:
        return None
    if not isinstance(envelope, dict) or envelope.get("status") != "completed" \
            or not isinstance(envelope.get("steps"), list):
        return None
    steps = [step for step in envelope["steps"] if isinstance(step, dict) and step.get("type") == "model_output"]
    if len(steps) != 1 or not isinstance(steps[0].get("content"), list):
        return None
    texts = [part["text"] for part in steps[0]["content"]
             if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)]
    if len(texts) != 1:
        return None
    try:
        answer = json.loads(texts[0])
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


# --- Provider calls -------------------------------------------------------

class Enricher:
    """Finds values for one song at a time. Holds only this run's counters."""

    def __init__(self, settings: Settings, *, genres, vibes, http=urllib_http,
                 sleep=time.sleep, clock=time.monotonic) -> None:
        self.settings = settings
        self.http, self.sleep, self.clock = http, sleep, clock
        self.genre_names = [name for _, name in genres]
        self.vibe_names = [name for _, name in vibes]
        self.genre_ids = {name: definition_id for definition_id, name in genres}
        self.vibe_ids = {name: definition_id for definition_id, name in vibes}
        self._genre_keys = self._label_map(self.genre_names)
        self._vibe_keys = self._label_map(self.vibe_names)
        self.gemini_calls = 0
        self.notes: list[str] = []
        self._failures = {MUSICBRAINZ: 0, ACOUSTICBRAINZ: 0, GEMINI: 0}
        self._stopped: set[str] = set()
        self._last_request: dict[str, float] = {}
        if not settings.musicbrainz_contact:
            self._stopped.update({MUSICBRAINZ, ACOUSTICBRAINZ})
            self.notes.append("MusicBrainz and AcousticBrainz skipped: MUSICBRAINZ_CONTACT is not set.")
        if not settings.gemini_api_key:
            self.notes.append("Gemini skipped: GEMINI_API_KEY is not set.")

    @staticmethod
    def _label_map(names: list[str]) -> dict[str, str]:
        mapping = {}
        for name in names:
            mapping.setdefault(_label_key(name), name)
        return mapping

    def lookup(self, song: Song) -> Outcome:
        outcome = Outcome(song)
        missing = song.missing()
        want_genres = not song.has_genre and self.settings.max_genres > 0 and bool(self.genre_names)
        want_vibes = not song.has_vibe and self.settings.max_vibes > 0 and bool(self.vibe_names)
        if not missing and not want_genres and not want_vibes:
            return outcome

        if missing or want_genres:
            match = self._musicbrainz(song)
            if match is not None:
                if "release_year" in missing and match.year is not None:
                    outcome.filled["release_year"] = (match.year, MUSICBRAINZ)
                if want_genres:
                    outcome.genres = self._labels(match.tags, self._genre_keys, self.settings.max_genres, MUSICBRAINZ)
                if "original_key" in missing or "bpm" in missing:
                    for name, value in self._acousticbrainz(match.mbid).items():
                        if name in missing:
                            outcome.filled[name] = (value, ACOUSTICBRAINZ)

        still = [name for name in missing if name not in outcome.filled]
        need_genres = want_genres and not outcome.genres
        if still or need_genres or want_vibes:
            answer, reason = self._gemini(song, still, need_genres, want_vibes)
            if answer is not None:
                self._take_gemini(outcome, answer, still, need_genres, want_vibes)
            reason = reason or "no valid answer from Gemini"
            for name in still:
                if name not in outcome.filled:
                    outcome.left[name] = reason
            if need_genres and not outcome.genres:
                outcome.left["genres"] = reason
            if want_vibes and not outcome.vibes:
                outcome.left["vibes"] = reason
        return outcome

    def _labels(self, names, keys: dict[str, str], limit: int, source: str) -> list:
        chosen = []
        for name in names:
            canonical = keys.get(_label_key(name)) if isinstance(name, str) else None
            if canonical and canonical not in (item[0] for item in chosen):
                chosen.append((canonical, source))
            if len(chosen) == limit:
                break
        return chosen

    def _take_gemini(self, outcome: Outcome, answer: dict, still, need_genres, want_vibes) -> None:
        if "release_year" in still and (year := _valid_year(answer.get("release_year"))) is not None:
            outcome.filled["release_year"] = (year, GEMINI)
        if "bpm" in still and (bpm := _valid_bpm(answer.get("bpm"))) is not None:
            outcome.filled["bpm"] = (bpm, GEMINI)
        key = answer.get("original_key")
        if "original_key" in still and isinstance(key, dict) and set(key) == {"key_key", "key_scale"} \
                and (value := _valid_key(key["key_key"], key["key_scale"])) is not None:
            outcome.filled["original_key"] = (value, GEMINI)
        for wanted, field_name, keys, limit in ((need_genres, "genres", self._genre_keys, self.settings.max_genres),
                                                (want_vibes, "vibes", self._vibe_keys, self.settings.max_vibes)):
            labels = answer.get(field_name)
            if wanted and isinstance(labels, list):
                setattr(outcome, field_name, self._labels(labels, keys, limit, GEMINI))

    def _musicbrainz(self, song: Song) -> Match | None:
        query = f'recording:"{_lucene_phrase(song.title)}" AND artist:"{_lucene_phrase(song.artist)}"'
        url = MB_URL + "?" + urlencode({"query": query, "fmt": "json", "limit": 25}, quote_via=quote)
        body = self._request(MUSICBRAINZ, "GET", url, self._factual_headers())
        if body is None:
            return None
        try:
            payload = json.loads(body)
        except ValueError:
            return None
        return choose_recording(payload, song.title, song.artist, self.settings.mb_min_score)

    def _acousticbrainz(self, mbid: str) -> dict:
        url = AB_URL.format(mbid=mbid) + "?n=0"
        body = self._request(ACOUSTICBRAINZ, "GET", url, self._factual_headers(), absent=(404,))
        if body is None:
            return {}
        try:
            return acousticbrainz_values(json.loads(body))
        except ValueError:
            return {}

    def _factual_headers(self) -> dict:
        return {"User-Agent": f"{APP_AGENT} ( {self.settings.musicbrainz_contact} )",
                "Accept": "application/json"}

    def _gemini(self, song: Song, still, need_genres, want_vibes):
        if not self.settings.gemini_api_key:
            return None, "not filled: Gemini skipped (GEMINI_API_KEY is not set)"
        if GEMINI in self._stopped:
            return None, "not filled: Gemini stopped after repeated failures"
        if self.gemini_calls >= self.settings.gemini_cap:
            return None, "not enriched: Gemini call cap reached"
        body = gemini_body(song, still, self.genre_names if need_genres else [],
                           self.vibe_names if want_vibes else [], self.settings)
        if len(body) > GEMINI_MAX_INPUT_BYTES:
            return None, "not filled: Gemini request too large"
        self.gemini_calls += 1
        if self.gemini_calls == self.settings.gemini_cap:
            self.notes.append(f"Gemini call cap of {self.settings.gemini_cap} reached.")
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.settings.gemini_api_key}
        response = self._request(GEMINI, "POST", GEMINI_URL, headers, body)
        if response is None:
            return None, "not filled: Gemini request failed"
        return gemini_answer(response), None

    def _request(self, provider: str, method: str, url: str, headers: dict,
                 body: bytes | None = None, absent=()) -> bytes | None:
        if provider in self._stopped:
            return None
        if provider != GEMINI:
            self._wait(provider)
        try:
            status, data = self.http(method, url, headers, body, self.settings.timeout_seconds)
        except ProviderError:
            status, data = None, None
        if status == 200:
            self._failures[provider] = 0
            return data
        if status in absent:
            self._failures[provider] = 0
            return None
        self._failures[provider] += 1
        if self._failures[provider] >= self.settings.outage_failures:
            self._stopped.add(provider)
            self.notes.append(f"{provider} stopped after {self._failures[provider]} consecutive failed "
                              "requests; later songs did not use it.")
        return None

    def _wait(self, provider: str) -> None:
        """NFR-011: at least one second between requests to each factual provider."""
        last = self._last_request.get(provider)
        if last is not None:
            remaining = FACTUAL_INTERVAL_SECONDS - (self.clock() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request[provider] = self.clock()


# --- Database -------------------------------------------------------------

def run(conn, documents, settings: Settings, *, http=urllib_http, sleep=time.sleep,
        clock=time.monotonic, now=None) -> RunResult:
    started = (now or (lambda: datetime.now(timezone.utc)))()
    documents = sorted(set(documents))
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("enrichment requires an idle connection")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM public.genres ORDER BY id")
            genres = cur.fetchall()
            cur.execute("SELECT id, name FROM public.vibes ORDER BY id")
            vibes = cur.fetchall()
            cur.execute(SONG_QUERY, (documents,))
            songs = [Song(*row) for row in cur.fetchall()]

    enricher = Enricher(settings, genres=genres, vibes=vibes, http=http, sleep=sleep, clock=clock)
    outcomes = []
    for song in songs:
        outcome = enricher.lookup(song)
        if outcome.changed:
            try:
                _write(conn, outcome, enricher.genre_ids, enricher.vibe_ids)
            except psycopg.Error as error:
                reason = f"write failed (SQLSTATE {getattr(error, 'sqlstate', None) or 'unknown'}); rerun to retry"
                for name in outcome.filled:
                    outcome.left[name] = reason
                for name in ("genres", "vibes"):
                    if getattr(outcome, name):
                        outcome.left[name] = reason
                outcome.filled, outcome.genres, outcome.vibes = {}, [], []
        outcomes.append(outcome)
    return RunResult(documents=documents, outcomes=outcomes, gemini_calls=enricher.gemini_calls,
                     gemini_cap=settings.gemini_cap, notes=enricher.notes,
                     started_at=started.strftime("%Y-%m-%d %H:%M:%S UTC"))


def _write(conn, outcome: Outcome, genre_ids: dict, vibe_ids: dict) -> None:
    """One transaction per song. Never overwrites; fills a category only while it is empty."""
    song_id = outcome.song.id
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT release_year, original_key, bpm FROM public.songs WHERE id=%s FOR UPDATE",
                        (song_id,))
            row = cur.fetchone()
            if row is None:
                for name in [*outcome.filled, *(n for n in ("genres", "vibes") if getattr(outcome, n))]:
                    outcome.left[name] = "song deleted during the run"
                outcome.filled, outcome.genres, outcome.vibes = {}, [], []
                return
            current = dict(zip(SCALARS, row))
            for name in [name for name in outcome.filled if current[name] is not None]:
                del outcome.filled[name]
                outcome.left[name] = "filled by another change during the run"
            outcome.genres = _insert_labels(cur, outcome, "genres", "song_genres", "genre_id", "genres", genre_ids)
            outcome.vibes = _insert_labels(cur, outcome, "vibes", "song_vibes", "vibe_id", "vibes", vibe_ids)
            if outcome.changed:
                # Also moves updated_at forward so devices pull the change.
                values = [outcome.filled.get(name, (None,))[0] for name in SCALARS]
                cur.execute("""UPDATE public.songs SET release_year = COALESCE(release_year, %s),
                                      original_key = COALESCE(original_key, %s), bpm = COALESCE(bpm, %s)
                                WHERE id = %s""", (*values, song_id))


def _insert_labels(cur, outcome: Outcome, name: str, table: str, column: str, definitions: str, ids: dict) -> list:
    labels = getattr(outcome, name)
    if not labels:
        return []
    cur.execute(f"SELECT EXISTS (SELECT 1 FROM public.{table} WHERE song_id=%s)", (outcome.song.id,))
    if cur.fetchone()[0]:
        outcome.left[name] = "category filled by another change during the run"
        return []
    wanted = [ids[label] for label, _ in labels]
    cur.execute(f"SELECT id FROM public.{definitions} WHERE id = ANY(%s) FOR SHARE", (wanted,))
    present = {row[0] for row in cur.fetchall()}
    kept = [(label, source) for label, source in labels if ids[label] in present]
    for label, _ in kept:
        cur.execute(f"INSERT INTO public.{table}(song_id, {column}) VALUES (%s, %s)", (outcome.song.id, ids[label]))
    return kept


# --- Report ---------------------------------------------------------------

def _counts(result: RunResult) -> dict:
    outcomes = result.outcomes
    return {"scope": len(outcomes),
            "complete": sum(1 for item in outcomes if not item.changed and not item.left),
            "changed": sum(1 for item in outcomes if item.changed),
            "left": sum(1 for item in outcomes if item.left)}


def summary_lines(result: RunResult) -> list[str]:
    counts = _counts(result)
    lines = [f"ENRICHMENT {result.started_at}  documents: {', '.join(result.documents) or '(none)'}",
             f"  songs in scope: {counts['scope']}; already complete: {counts['complete']}; "
             f"changed: {counts['changed']}; not fully enriched: {counts['left']}",
             f"  Gemini calls: {result.gemini_calls} of {result.gemini_cap}"]
    lines.extend(f"  note: {note}" for note in result.notes)
    return lines


def render_report(result: RunResult) -> str:
    """One human-readable record of the run (FR-102). Identities and values only."""
    lines = [*summary_lines(result), ""]
    for outcome in result.outcomes:
        if not outcome.changed and not outcome.left:
            continue
        song = outcome.song
        lines.append(f"{song.id}  {song.title} / {song.artist}")
        filled = [f"{name}={value} ({source})" for name, (value, source) in outcome.filled.items()]
        filled += [f"Genre: {label} ({source})" for label, source in outcome.genres]
        filled += [f"Vibe: {label} ({source})" for label, source in outcome.vibes]
        if filled:
            lines.append("    filled: " + "; ".join(filled))
        for name, reason in outcome.left.items():
            lines.append(f"    left:   {name}: {reason}")
    return "\n".join(lines) + "\n"


def write_report(result: RunResult, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    text = render_report(result)
    for attempt in range(1, 100):
        path = directory / (f"enrichment-report-{stamp}.txt" if attempt == 1
                            else f"enrichment-report-{stamp}-{attempt}.txt")
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            return path
        except FileExistsError:
            continue
    raise RuntimeError("could not choose a new report file name")


def enrich_and_report(conn, documents, report_dir: Path) -> int:
    """Entry point for load_songs.py --enrich. Returns a process exit code."""
    try:
        settings = Settings.from_env(os.environ)
    except ValueError as error:
        print(f"Enrichment not run: {error}", file=sys.stderr)
        return 2
    result = run(conn, documents, settings)
    path = write_report(result, Path(report_dir))
    for line in summary_lines(result):
        print(line)
    print(f"  report: {path}")
    return 0
