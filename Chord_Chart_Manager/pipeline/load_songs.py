"""Insert-only loader for validated songs.json produced by batch_parse.py.

Matching is deliberately exact and case-insensitive for title and artist name,
with source_document compared exactly including NULL. One existing match is a
skip. Ambiguous or cross-source matches stop the whole batch for review.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

import chart_parser

DIAGNOSTICS_VERSION = 1
# The app locks Genre then Vibe names, matching definition rows, then writes
# artists, songs, or assignment rows. The loader follows that same order.
LOCK_TABLES = ("artists", "songs", "song_genres", "song_vibes")
SONG_COLUMNS = (
    "title", "artist_id", "original_key", "performance_key", "preferred_key",
    "alt_key", "capo_fret", "bpm", "release_year", "bb_structure",
    "chart_content", "chart_source", "source_document",
)


class LoadPreflightError(ValueError):
    """The batch has identity or chart-data blockers and was not written."""


class ParserValidationError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(
            "Input batch rejected before database access. Parser diagnostics or chart validation failed:\n  "
            + "\n  ".join(problems)
        )


def _strict_json_equal(left, right) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _strict_json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _strict_json_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def validate_parser_diagnostics(songs: list[dict]) -> None:
    """Keep the existing parser diagnostics and ordinary-source round trip gate."""
    if not isinstance(songs, list):
        raise ValueError("Input must be a JSON array of song objects.")
    problems = []
    for index, song in enumerate(songs):
        if not isinstance(song, dict):
            problems.append(f"[{index}] song entry is not an object")
            continue
        version = song.get("parser_diagnostics_version")
        if type(version) is not int or version != DIAGNOSTICS_VERSION:
            problems.append(
                f"[{index}] {song.get('title') or '(untitled)'}: missing, malformed, "
                "or unsupported parser_diagnostics_version; re-run batch_parse.py"
            )
            continue
        warnings = song.get("warnings")
        if not isinstance(warnings, list) or any(not isinstance(w, dict) for w in warnings):
            problems.append(f"[{index}] {song.get('title') or '(untitled)'}: malformed parser warnings")
            continue
        primary = song.get("primary_genre")
        identity = (f"{song.get('source_document') or '(unknown document)'} / "
                    f"{song.get('title') or '(untitled)'} / {song.get('artist') or '(unknown artist)'}")
        if primary and song.get("primary_genre_source") not in ("cover_page", "filename_fallback"):
            problems.append(
                f"[{index}] {identity}: primary Genre provenance is missing or invalid; "
                "reparse with the current parser"
            )
        for warning in warnings:
            if warning.get("code") in (
                    "word_tab_placement_unresolved", "plain_text_body_unresolved",
                    "opening_metadata_conflict", "ordered_chord_token_unresolved"):
                code = warning["code"]
                problems.append(
                    f"[{index}] {song.get('source_document') or '(unknown document)'} / "
                    f"{song.get('title') or '(untitled)'} section {warning.get('section', '?')} "
                    f"line {warning.get('line', '?')}: {code} ({warning.get('reason', 'unspecified_reason')})"
                )
        source = song.get("chart_source")
        if source is None:
            # FR-107: charts are stored only as ordinary text, so a song needs its source.
            problems.append(f"[{index}] {song.get('title') or '(untitled)'}: chart_source_missing")
            continue
        if not isinstance(source, str):
            problems.append(f"[{index}] {song.get('title') or '(untitled)'}: chart_source_content_mismatch (ordinary_source_not_text)")
            continue
        try:
            derived = chart_parser.parse_ordinary_chart_source(source)
        except Exception:
            problems.append(f"[{index}] {song.get('title') or '(untitled)'}: chart_source_content_mismatch (ordinary_source_parse_failed)")
            continue
        if not _strict_json_equal(derived, song.get("chart_content")):
            problems.append(
                f"[{index}] {song.get('title') or '(untitled)'}: chart_source_content_mismatch "
                "(ordinary_source_parse_back_mismatch; confidence included)"
            )
    if problems:
        raise ParserValidationError(problems)


def _normalized_identity(song: dict) -> tuple[str, str, str | None]:
    title, artist, source = song.get("title"), song.get("artist"), song.get("source_document")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be nonempty text")
    if not isinstance(artist, str) or not artist.strip():
        raise ValueError("artist must be nonempty text")
    if source is not None and not isinstance(source, str):
        raise ValueError("source_document must be text or null")
    # Match current semantics: title/artist use lower(), source_document is
    # exact and NULL-safe. The incoming artist name is trimmed as app writes do.
    return title.lower(), artist.strip().lower(), source


def _explicit_categories(song: dict) -> tuple[list[str], list[str], str | None]:
    # FR-109: the cover-page Genre, or else the filename Genre, is the primary.
    primary = song.get("primary_genre") or None
    genres = [primary] if primary else []
    extra = song.get("additional_genres", [])
    vibes = song.get("vibes", [])
    if not isinstance(extra, list) or not isinstance(vibes, list):
        raise ValueError("additional_genres and vibes must be arrays when present")
    genres.extend(extra)
    return genres, vibes, primary


def _validate_batch(songs: list[dict]) -> tuple[list[dict], list[dict]]:
    validate_parser_diagnostics(songs)
    prepared = []
    failures = []
    seen: dict[tuple[str, str, str | None], int] = {}
    seen_title_artist: dict[tuple[str, str], tuple[str | None, int]] = {}
    for index, song in enumerate(songs):
        try:
            title, artist, source = _normalized_identity(song)
            identity = (title, artist, source)
            title_artist = (title, artist)
            previous = seen_title_artist.get(title_artist)
            if previous is not None and previous[0] != source:
                raise ValueError(
                    f"cross_source_input_collision (also input index {previous[1]}; "
                    "same normalized title/artist appears with different source_document values)"
                )
            if previous is None:
                seen_title_artist[title_artist] = (source, index)
            if identity in seen:
                raise ValueError(f"duplicate_input_identity (also input index {seen[identity]})")
            seen[identity] = index
            content = song.get("chart_content")
            if not isinstance(content, dict) or not isinstance(content.get("sections"), list):
                raise ValueError("chart_content must contain a sections array")
            for field in ("bpm", "release_year", "capo_fret"):
                value = song.get(field)
                if value is not None and type(value) is not int:
                    raise ValueError(f"{field} must be an integer when present")
            if song.get("bpm") is not None and not 1 <= song["bpm"] <= 32767:
                raise ValueError("bpm must be from 1 to 32767 when present")
            if song.get("release_year") is not None and not 1 <= song["release_year"] <= 32767:
                raise ValueError("release_year must be from 1 to 32767 when present")
            if song.get("capo_fret") is not None and not 0 <= song["capo_fret"] <= 11:
                raise ValueError("capo_fret must be from 0 to 11 when present")
            if "primary_genre_source" in song and song["primary_genre_source"] not in (
                    None, "cover_page", "filename_fallback"):
                raise ValueError("primary_genre_source must be cover_page or filename_fallback")
            genres, vibes, primary_genre = _explicit_categories(song)
            for kind, values in (("Genre", genres), ("Vibe", vibes)):
                for value in values:
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(f"{kind} labels must be nonempty strings")
            prepared.append({"index": index, "song": song, "title": title, "artist": artist,
                             "source": source, "genres": genres, "vibes": vibes,
                             "primary_genre": primary_genre})
        except Exception as exc:
            failures.append({"index": index, "title": song.get("title", "(untitled)"),
                             "reason": str(exc)})
    return prepared, failures


def _resolve_artist(cur, artist_name: str):
    cur.execute("SELECT id, name FROM public.artists WHERE lower(name)=lower(%s) ORDER BY id", (artist_name.strip(),))
    rows = cur.fetchall()
    if len(rows) > 1:
        return {"status": "ambiguous", "matches": [{"id": row[0], "name": row[1]} for row in rows]}
    return {"status": "found", "id": rows[0][0], "name": rows[0][1]} if rows else {"status": "new"}


def _resolve_category(cur, category: str, label: str, *, lock=False):
    table = "genres" if category == "Genre" else "vibes"
    normalized = re.sub(r"\s+", " ", label.strip()).lower()
    query = (f"SELECT id, name FROM public.{table} "
             "WHERE lower(regexp_replace(btrim(name), '[[:space:]]+', ' ', 'g'))=lower(%s) ORDER BY id")
    if lock:
        query += " FOR SHARE"
    cur.execute(query, (normalized,))
    rows = cur.fetchall()
    if not rows:
        return {"status": "missing"}
    if len(rows) > 1:
        return {"status": "ambiguous", "matches": [{"id": row[0], "name": row[1]} for row in rows]}
    return {"status": "resolved", "id": rows[0][0], "name": rows[0][1]}


def _lock_matching_definitions(cur, prepared: list[dict]) -> None:
    """Lock matching catalog rows before app song/assignment locks.

    Locking every current matching row in id order mirrors the app's
    definition-row locks and keeps ambiguity stable while the loader takes
    its later table locks.
    """
    table_for = {"Genre": "genres", "Vibe": "vibes"}
    keys = set()
    for entry in prepared:
        for category, labels in (("Genre", entry["genres"]), ("Vibe", entry["vibes"])):
            for label in labels:
                normalized = re.sub(r"\s+", " ", label.strip()).lower()
                keys.add((category, normalized))
    for category, normalized in sorted(keys):
        table = table_for[category]
        cur.execute(
            f"SELECT id FROM public.{table} "
            "WHERE lower(regexp_replace(btrim(name), '[[:space:]]+', ' ', 'g'))=lower(%s) "
            "ORDER BY id",
            (normalized,),
        )
        ids = [row[0] for row in cur.fetchall()]
        if ids:
            cur.execute(f"SELECT id FROM public.{table} WHERE id=ANY(%s) ORDER BY id FOR SHARE",
                        (ids,))
            locked_ids = [row[0] for row in cur.fetchall()]
            if locked_ids != ids:
                raise RuntimeError(f"{category} definitions changed while acquiring catalog locks")


def _database_song_matches(cur, title: str, artist: str, source: str | None):
    cur.execute(
        """SELECT s.id, s.title, a.id, a.name, s.source_document
           FROM public.songs s JOIN public.artists a ON a.id=s.artist_id
           WHERE lower(s.title)=lower(%s) AND lower(a.name)=lower(%s)
           ORDER BY s.id""",
        (title, artist),
    )
    rows = cur.fetchall()
    exact = [row for row in rows if row[4] == source]
    other = [row for row in rows if row[4] != source]
    return rows, exact, other


def _describe_match(row):
    return {"id": row[0], "title": row[1], "artist_id": row[2],
            "artist": row[3], "source_document": row[4]}


def _plan(cur, prepared: list[dict], failures: list[dict], *, lock_definitions=False) -> dict:
    plan = {"proposed_inserts": [], "exact_skips": [], "blocking_collisions": [],
            "unresolved_categories": [], "parser_validation_failures": list(failures)}
    for entry in prepared:
        rows, exact, other = _database_song_matches(cur, entry["title"], entry["artist"].strip(), entry["source"])
        identity = {"index": entry["index"], "title": entry["song"].get("title"),
                    "artist": entry["song"].get("artist"), "source_document": entry["source"]}
        if len(exact) > 1:
            plan["blocking_collisions"].append({**identity, "reason": "multiple_exact_database_matches",
                                                 "matches": [_describe_match(row) for row in exact]})
            continue
        if other:
            plan["blocking_collisions"].append({**identity, "reason": "same_title_artist_other_source",
                                                 "matches": [_describe_match(row) for row in other]})
            continue
        if len(rows) > 1:
            plan["blocking_collisions"].append({**identity, "reason": "multiple_database_matches",
                                                 "matches": [_describe_match(row) for row in rows]})
            continue
        if exact:
            plan["exact_skips"].append({**identity, "song_id": exact[0][0]})
            continue
        artist_resolution = _resolve_artist(cur, entry["artist"])
        if artist_resolution["status"] == "ambiguous":
            plan["blocking_collisions"].append({**identity, "reason": "ambiguous_artist", "matches": artist_resolution["matches"]})
            continue
        cats = []
        primary_genre_id = None
        for kind, labels in (("Genre", entry["genres"]), ("Vibe", entry["vibes"])):
            resolved = []
            for label in labels:
                match = _resolve_category(cur, kind, label, lock=lock_definitions)
                if match["status"] == "resolved":
                    if kind == "Genre" and label == entry["primary_genre"] and primary_genre_id is None:
                        primary_genre_id = match["id"]
                    if all(row["id"] != match["id"] for row in resolved):
                        resolved.append(match)
                else:
                    plan["unresolved_categories"].append({**identity, "category": kind,
                                                          "label": label, "reason": match["status"]})
            cats.append(resolved)
        plan["proposed_inserts"].append({**identity, "entry": entry,
                                        "artist_resolution": artist_resolution,
                                        "genres": cats[0], "vibes": cats[1],
                                        "primary_genre_id": primary_genre_id})
    return plan


def _blocked(plan):
    return bool(plan["blocking_collisions"] or plan["parser_validation_failures"])


def _locked_apply_plan(cur, prepared: list[dict], failures: list[dict]) -> dict:
    """Acquire app-compatible locks, then resolve identities/catalog labels."""
    _lock_matching_definitions(cur, prepared)
    cur.execute("LOCK TABLE " + ", ".join(f"public.{table}" for table in LOCK_TABLES)
                + " IN SHARE ROW EXCLUSIVE MODE")
    return _plan(cur, prepared, failures, lock_definitions=True)


def _report(plan, *, state: str) -> dict:
    proposed = [
        {"index": item["index"], "title": item["title"], "artist": item["artist"],
         "source_document": item["source_document"], "artist_action": item["artist_resolution"]["status"],
         "genres": [row["name"] for row in item["genres"]],
         "vibes": [row["name"] for row in item["vibes"]]}
        for item in plan["proposed_inserts"]
    ]
    return {
        "state": state,
        "counts": {key: len(plan[key]) for key in (
            "proposed_inserts", "exact_skips", "blocking_collisions",
            "unresolved_categories", "parser_validation_failures")},
        "proposed_inserts": proposed,
        "exact_skips": plan["exact_skips"],
        "blocking_collisions": plan["blocking_collisions"],
        "unresolved_categories": plan["unresolved_categories"],
        "parser_validation_failures": plan["parser_validation_failures"],
    }


def _insert_song(cur, item: dict) -> int:
    song = item["entry"]["song"]
    artist_id = item["artist_resolution"].get("id")
    if artist_id is None:
        # Multiple new songs may share a new artist in one document. Reuse the
        # row created by the earlier planned insert without an upsert/rename.
        cur.execute("SELECT id FROM public.artists WHERE lower(name)=lower(%s) ORDER BY id", (song["artist"].strip(),))
        rows = cur.fetchall()
        if len(rows) > 1:
            raise LoadPreflightError("artist became ambiguous while inserting")
        if rows:
            artist_id = rows[0][0]
        else:
            cur.execute("INSERT INTO public.artists(name) VALUES (%s) RETURNING id", (song["artist"].strip(),))
            artist_id = cur.fetchone()[0]
    values = {
        "title": song["title"], "artist_id": artist_id,
        "original_key": song.get("original_key"),
        "performance_key": song.get("performance_key"),
        "preferred_key": song.get("preferred_key"), "alt_key": song.get("alt_key"),
        "bpm": song.get("bpm"),
        "release_year": song.get("release_year"),
        "bb_structure": song.get("bb_structure"),
        "chart_content": Jsonb(song["chart_content"]),
        "chart_source": song.get("chart_source"),
        "source_document": item["source_document"],
    }
    columns_ordered = [column for column in SONG_COLUMNS
                       if column != "capo_fret" or "capo_fret" in song]
    if "capo_fret" in song:
        values["capo_fret"] = song["capo_fret"]
    columns = ", ".join(columns_ordered)
    placeholders = ", ".join(f"%({column})s" for column in columns_ordered)
    cur.execute(f"INSERT INTO public.songs ({columns}) VALUES ({placeholders}) RETURNING id", values)
    song_id = cur.fetchone()[0]
    for genre in item["genres"]:
        cur.execute("INSERT INTO public.song_genres(song_id,genre_id,is_primary) VALUES (%s,%s,%s)",
                    (song_id, genre["id"], genre["id"] == item["primary_genre_id"]))
    for vibe in item["vibes"]:
        cur.execute("INSERT INTO public.song_vibes(song_id,vibe_id) VALUES (%s,%s)",
                    (song_id, vibe["id"]))
    return song_id


def preview(conn, songs: list[dict]) -> dict:
    prepared, failures = _validate_batch(songs)
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("preview requires an idle connection")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            plan = _plan(cur, prepared, failures)
    report = _report(plan, state="blocked" if _blocked(plan) else "preview")
    if _blocked(plan):
        raise LoadPreflightError(json.dumps(report, ensure_ascii=False))
    return report


def load(conn, songs: list[dict], *, dry_run=False) -> dict:
    prepared, failures = _validate_batch(songs)
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("load requires an idle connection")
    if dry_run:
        return preview(conn, songs)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            # Match app order: Genre name keys, Vibe name keys, matching
            # definition rows, then artists/songs/assignments. READ COMMITTED
            # makes the planner's next statement see commits made while waiting.
            plan = _locked_apply_plan(cur, prepared, failures)
            if _blocked(plan):
                raise LoadPreflightError(json.dumps(_report(plan, state="blocked"), ensure_ascii=False))
            inserted = []
            for item in plan["proposed_inserts"]:
                inserted.append({"song_id": _insert_song(cur, item),
                                 "title": item["title"], "artist": item["artist"],
                                 "source_document": item["source_document"]})
    report = _report(plan, state="committed")
    report["inserted"] = inserted
    return report


def _print_report(report: dict, *, dry_run=False):
    counts = report["counts"]
    label = "DRY RUN" if dry_run else "LOAD"
    print(f"{label} {report['state']}")
    for key, title in (("proposed_inserts", "proposed inserts"), ("exact_skips", "exact existing-song skips"),
                       ("blocking_collisions", "blocking identity collisions"),
                       ("unresolved_categories", "unresolved categories"),
                       ("parser_validation_failures", "parser/validation failures")):
        print(f"  {title}: {counts[key]}")
    for key in ("proposed_inserts", "exact_skips", "blocking_collisions", "unresolved_categories",
                "parser_validation_failures"):
        for row in report[key]:
            print("  " + json.dumps(row, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or insert new songs from validated songs.json.")
    parser.add_argument("songs_json", help="Path to songs.json from batch_parse")
    parser.add_argument("--dry-run", action="store_true", help="Read-only plan; no rows or sequences are changed")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"),
                        help="Postgres URL (or set DATABASE_URL)")
    args = parser.parse_args()
    try:
        with open(args.songs_json, encoding="utf-8") as handle:
            songs = json.load(handle)
        prepared, failures = _validate_batch(songs)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print("PREVIEW blocked" if args.dry_run else "LOAD blocked", file=sys.stderr)
        problems = exc.problems if isinstance(exc, ParserValidationError) else [str(exc)]
        print(f"  parser/validation failures: {len(problems)}", file=sys.stderr)
        for problem in problems:
            print("  " + problem.replace("\n", "\n  "), file=sys.stderr)
        if args.dry_run:
            print("  database connection: not opened", file=sys.stderr)
        return 1
    if not args.database_url:
        print("Set DATABASE_URL or pass --database-url", file=sys.stderr)
        return 2
    conn = psycopg.connect(args.database_url, autocommit=False)
    try:
        report = load(conn, songs, dry_run=args.dry_run)
        _print_report(report, dry_run=args.dry_run)
        return 0
    except LoadPreflightError as exc:
        try:
            report = json.loads(str(exc))
            _print_report(report, dry_run=args.dry_run)
        except json.JSONDecodeError:
            print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        conn.rollback()
        sqlstate = getattr(exc, "sqlstate", None)
        if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
            print(f"Load rolled back: database error (SQLSTATE {sqlstate}); details suppressed.",
                  file=sys.stderr)
        else:
            print(f"Load rolled back: {type(exc).__name__}; details suppressed.", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
