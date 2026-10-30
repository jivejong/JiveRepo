#!/usr/bin/env python3
"""
Per-document metadata enrichment for Chord Chart Manager.

`load_songs.py --enrich` calls this after a committed load, for every song whose
source_document is in that songs.json (FR-101), not only the new inserts.

* Fills only missing release_year, original_key and bpm; Vibes only when the song
  has none; Genres only when it has no non-primary Genre, up to max_genres in
  total counting the primary (FR-092, FR-103, Q29 as revised 2026-10-29).
* One Gemini call per song fills every gap: release year, key, BPM, Genres and
  Vibes (FR-103). The owner dropped AcousticBrainz and then MusicBrainz on
  2026-10-30 after two live trials (wrong BPM and key; wrong years from title
  variants), so Gemini is the only provider.
* Never reads or sends chart text or lyrics (FR-093): the song query selects
  identity and metadata columns only.
* State lives in the song rows. A rerun reads no earlier output, fills only
  what is still missing, and is a no-op on a complete document (FR-115).
* The only safeguards are the host backup taken before the run (FR-104), the
  never-overwrite rule, and a per-run Gemini call cap (FR-112).
* Writes directly; the run leaves one human-readable report (FR-102).

Settings (environment): GEMINI_API_KEY, ENRICH_GEMINI_CAP, ENRICH_MAX_GENRES,
ENRICH_MAX_VIBES. The Gemini key is sent only in the x-goog-api-key header and
never printed (NFR-009).
"""
from __future__ import annotations

import http.client
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.pq import TransactionStatus

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_MODEL = "gemini-3.1-flash-lite"
# NFR-011 provider bounds.
GEMINI_MAX_INPUT_BYTES = 16_384
GEMINI_MAX_OUTPUT_TOKENS = 1024
MAX_RESPONSE_BYTES = 1 << 20

SCALARS = ("release_year", "original_key", "bpm")
KEY_NAMES = frozenset(["C", "B#", "C#", "Db", "D", "D#", "Eb", "E", "Fb", "E#", "F", "F#", "Gb",
                       "G", "G#", "Ab", "A", "A#", "Bb", "B", "Cb"])

GEMINI = "Gemini"

GEMINI_INSTRUCTION = (
    "requested_missing_fields lists everything to suggest: any of release_year, original_key and bpm, "
    "and the label categories genres and vibes. For each requested scalar give your best answer, or "
    "null if you are unsure. For genres and vibes choose labels only from the matching allowed_names "
    "list, choosing every label that fits, and return an empty array only when none fit. Vibes "
    "describe the subjective musical, listening or performance character of the song. Use null for "
    "any scalar that was not requested and an empty array for any category that was not requested. "
    "Give original_key as the key the original recording is in. Do not return explanations or fields "
    "outside the schema."
)

# Identity and metadata only: chart text and lyrics are never read (FR-093).
SONG_QUERY = """SELECT s.id, s.title, a.name, s.release_year, s.original_key, s.bpm,
       ARRAY(SELECT d.name FROM public.song_genres g JOIN public.genres d ON d.id = g.genre_id
              WHERE g.song_id = s.id ORDER BY d.id),
       EXISTS (SELECT 1 FROM public.song_genres g WHERE g.song_id = s.id AND NOT g.is_primary),
       EXISTS (SELECT 1 FROM public.song_vibes v WHERE v.song_id = s.id)
  FROM public.songs s JOIN public.artists a ON a.id = s.artist_id
 WHERE s.source_document = ANY(%s)
 ORDER BY s.id"""


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = field(default="", repr=False)
    gemini_cap: int = 1000
    max_genres: int = 3
    max_vibes: int = 3
    outage_failures: int = 3
    timeout_seconds: int = 20

    def __post_init__(self) -> None:
        if self.gemini_cap < 0 or self.max_genres < 0 or self.max_vibes < 0:
            raise ValueError("enrichment limits must not be negative")
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
            gemini_api_key=(environ.get("GEMINI_API_KEY") or "").strip(),
            gemini_cap=number("ENRICH_GEMINI_CAP", cls.gemini_cap),
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
    genres: tuple          # names of the Genres the song already has (the primary included)
    has_secondary_genre: bool
    has_vibe: bool

    @classmethod
    def from_row(cls, row) -> "Song":
        return cls(*row[:6], tuple(row[6] or ()), row[7], row[8])

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

def _label_key(value: str) -> str:
    # Same rule as load_songs._resolve_category.
    return re.sub(r"\s+", " ", value.strip()).lower()


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
                genre_limit: int, vibe_limit: int) -> bytes:
    """FR-093: the only song data sent is its title and artist."""
    # The label categories must be listed too: with only scalars named (or nothing named),
    # Gemini answered a labels-only request with empty arrays (live trial, 2026-10-30).
    wanted = [*requested, *(category for category, names in (("genres", genres), ("vibes", vibes)) if names)]
    payload = {"title": song.title, "artist": song.artist, "requested_missing_fields": wanted,
               "allowed_names": {"genres": genres, "vibes": vibes}}
    body = {
        "model": GEMINI_MODEL,
        "input": f"{GEMINI_INSTRUCTION}\n{json.dumps(payload, ensure_ascii=False)}",
        "system_instruction": GEMINI_INSTRUCTION,
        "store": False,
        "response_format": {"type": "text", "mime_type": "application/json",
                            "schema": _gemini_schema(requested, genre_limit if genres else 0,
                                                     vibe_limit if vibes else 0)},
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


# --- Gemini ---------------------------------------------------------------

class Enricher:
    """Finds values for one song at a time with one Gemini call. Holds only this run's counters."""

    def __init__(self, settings: Settings, *, genres, vibes, http=urllib_http) -> None:
        self.settings = settings
        self.http = http
        self.genre_names = [name for _, name in genres]
        self.vibe_names = [name for _, name in vibes]
        self.genre_ids = {name: definition_id for definition_id, name in genres}
        self.vibe_ids = {name: definition_id for definition_id, name in vibes}
        self._genre_keys = self._label_map(self.genre_names)
        self._vibe_keys = self._label_map(self.vibe_names)
        self.gemini_calls = 0
        self.notes: list[str] = []
        self._failures = 0
        self._stopped = False
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
        # A Genre is added only to a song with no non-primary Genre yet, and never past
        # max_genres in total; the primary counts toward that total (FR-103).
        has = {_label_key(name) for name in song.genres}
        slots = 0 if song.has_secondary_genre else max(0, self.settings.max_genres - len(song.genres))
        allowed_genres = {key: name for key, name in self._genre_keys.items() if key not in has}
        want_genres = slots > 0 and bool(allowed_genres)
        want_vibes = not song.has_vibe and self.settings.max_vibes > 0 and bool(self.vibe_names)
        if not missing and not want_genres and not want_vibes:
            return outcome

        answer, reason = self._gemini(song, missing, list(allowed_genres.values()) if want_genres else [],
                                      slots, want_vibes)
        if answer is not None:
            self._take_gemini(outcome, answer, missing, allowed_genres if want_genres else {}, slots, want_vibes)
        value_reason = label_reason = reason
        if reason is None:
            value_reason, label_reason = (("Gemini gave no usable value", "Gemini answered with no usable labels")
                                          if answer is not None else ("no valid answer from Gemini",) * 2)
        for name in missing:
            if name not in outcome.filled:
                outcome.left[name] = value_reason
        if want_genres and not outcome.genres:
            outcome.left["genres"] = label_reason
        if want_vibes and not outcome.vibes:
            outcome.left["vibes"] = label_reason
        return outcome

    def _labels(self, names, keys: dict[str, str], limit: int) -> list:
        chosen = []
        for name in names:
            canonical = keys.get(_label_key(name)) if isinstance(name, str) else None
            if canonical and canonical not in (item[0] for item in chosen):
                chosen.append((canonical, GEMINI))
            if len(chosen) == limit:
                break
        return chosen

    def _take_gemini(self, outcome: Outcome, answer: dict, missing, genre_keys, genre_limit, want_vibes) -> None:
        if "release_year" in missing and (year := _valid_year(answer.get("release_year"))) is not None:
            outcome.filled["release_year"] = (year, GEMINI)
        if "bpm" in missing and (bpm := _valid_bpm(answer.get("bpm"))) is not None:
            outcome.filled["bpm"] = (bpm, GEMINI)
        key = answer.get("original_key")
        if "original_key" in missing and isinstance(key, dict) and set(key) == {"key_key", "key_scale"} \
                and (value := _valid_key(key["key_key"], key["key_scale"])) is not None:
            outcome.filled["original_key"] = (value, GEMINI)
        for wanted, field_name, keys, limit in ((bool(genre_keys), "genres", genre_keys, genre_limit),
                                                (want_vibes, "vibes", self._vibe_keys, self.settings.max_vibes)):
            labels = answer.get(field_name)
            if wanted and isinstance(labels, list):
                setattr(outcome, field_name, self._labels(labels, keys, limit))

    def _gemini(self, song: Song, missing, genre_names, genre_limit, want_vibes):
        if not self.settings.gemini_api_key:
            return None, "not filled: Gemini skipped (GEMINI_API_KEY is not set)"
        if self._stopped:
            return None, "not filled: Gemini stopped after repeated failures"
        if self.gemini_calls >= self.settings.gemini_cap:
            return None, "not enriched: Gemini call cap reached"
        body = gemini_body(song, missing, genre_names, self.vibe_names if want_vibes else [],
                           genre_limit, self.settings.max_vibes)
        if len(body) > GEMINI_MAX_INPUT_BYTES:
            return None, "not filled: Gemini request too large"
        self.gemini_calls += 1
        if self.gemini_calls == self.settings.gemini_cap:
            self.notes.append(f"Gemini call cap of {self.settings.gemini_cap} reached.")
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.settings.gemini_api_key}
        try:
            status, data = self.http("POST", GEMINI_URL, headers, body, self.settings.timeout_seconds)
        except ProviderError:
            status, data = None, None
        if status == 200:
            self._failures = 0
            return gemini_answer(data), None
        self._failures += 1
        if self._failures >= self.settings.outage_failures:
            self._stopped = True
            self.notes.append(f"Gemini stopped after {self._failures} consecutive failed requests; "
                              "later songs were not enriched. Rerun to fill them.")
        return None, "not filled: Gemini request failed"


# --- Database -------------------------------------------------------------

def run(conn, documents, settings: Settings, *, http=urllib_http, now=None) -> RunResult:
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
            songs = [Song.from_row(row) for row in cur.fetchall()]

    enricher = Enricher(settings, genres=genres, vibes=vibes, http=http)
    outcomes = []
    for song in songs:
        outcome = enricher.lookup(song)
        if outcome.changed:
            try:
                _write(conn, outcome, enricher.genre_ids, enricher.vibe_ids, settings.max_genres)
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


def _write(conn, outcome: Outcome, genre_ids: dict, vibe_ids: dict, max_genres: int = 3) -> None:
    """One transaction per song. Never overwrites; adds Genres only while the song has no
    non-primary Genre (and never past max_genres in total), and Vibes only while it has none."""
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
            outcome.genres = _insert_labels(cur, outcome, "genres", "song_genres", "genre_id", "genres",
                                            genre_ids, max_genres)
            outcome.vibes = _insert_labels(cur, outcome, "vibes", "song_vibes", "vibe_id", "vibes", vibe_ids, None)
            if outcome.changed:
                # Also moves updated_at forward so devices pull the change.
                values = [outcome.filled.get(name, (None,))[0] for name in SCALARS]
                cur.execute("""UPDATE public.songs SET release_year = COALESCE(release_year, %s),
                                      original_key = COALESCE(original_key, %s), bpm = COALESCE(bpm, %s)
                                WHERE id = %s""", (*values, song_id))


def _insert_labels(cur, outcome: Outcome, name: str, table: str, column: str, definitions: str, ids: dict,
                   max_total: int | None) -> list:
    """Re-checks inside the transaction that the category is still open. Vibes: the song has
    none. Genres (max_total set): the song has no non-primary Genre and room under max_total."""
    labels = getattr(outcome, name)
    if not labels:
        return []
    if max_total is None:
        cur.execute(f"SELECT count(*), 0 FROM public.{table} WHERE song_id=%s", (outcome.song.id,))
        total, secondary = cur.fetchone()
        room = None if total == 0 else 0
    else:
        cur.execute(f"SELECT count(*), count(*) FILTER (WHERE NOT is_primary) FROM public.{table} WHERE song_id=%s",
                    (outcome.song.id,))
        total, secondary = cur.fetchone()
        room = 0 if secondary else max_total - total
    if room is not None and room <= 0:
        outcome.left[name] = "category filled by another change during the run"
        return []
    wanted = [ids[label] for label, _ in labels]
    cur.execute(f"SELECT id FROM public.{definitions} WHERE id = ANY(%s) FOR SHARE", (wanted,))
    present = {row[0] for row in cur.fetchall()}
    cur.execute(f"SELECT {column} FROM public.{table} WHERE song_id=%s", (outcome.song.id,))
    held = {row[0] for row in cur.fetchall()}
    kept = [(label, source) for label, source in labels if ids[label] in present and ids[label] not in held]
    if room is not None:
        kept = kept[:room]
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
