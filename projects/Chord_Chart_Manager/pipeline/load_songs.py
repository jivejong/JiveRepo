"""
load_songs.py — Load parsed/enriched song JSON into the Postgres schema.

Usage:
    export DATABASE_URL=postgresql://user:pass@host:5432/chords
    python load_songs.py songs.json [--truncate] [--dry-run]

Input: the songs.json produced by batch_parse.py (an array of song dicts).
The same loader also works AFTER the AI-fill step — it reads optional
`release_year` and `vibes` keys when present and ignores them when absent,
so you don't need a second loader once enrichment is added.

Design:
  * Lookup tables (artists, genres, vibes) are get-or-created idempotently
    and cached in-process, so re-runs don't create duplicates and we make
    O(distinct values) round-trips, not O(songs).
  * Songs are matched on a natural key — (lower(title), artist_id,
    source_document) — so a re-load UPDATES the existing row instead of
    inserting a duplicate. This preserves the song's id (and anything the
    app later attached to it) across re-migrations.
  * The doc's primary genre is written to song_genres with is_primary=true.
    Re-loading re-asserts the primary genre without disturbing any extra
    (non-primary) genres a user/AI added later.
  * Each song loads inside its own SAVEPOINT: one malformed song rolls back
    just itself and is reported, the rest of the batch still commits.
"""

from __future__ import annotations

import os
import sys
import json
import argparse

import psycopg
from psycopg.types.json import Jsonb


# ----------------------------------------------------------------------
# Lookup get-or-create with an in-process cache
# ----------------------------------------------------------------------

class LookupCache:
    """Idempotent get-or-create for a (id, name) lookup table with a
    UNIQUE index on LOWER(name). Caches by lowercased name."""

    def __init__(self, conn, table: str):
        self.conn = conn
        self.table = table
        self._cache: dict[str, int] = {}

    def get_or_create(self, name: str | None) -> int | None:
        if not name:
            return None
        key = name.strip().lower()
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]
        # ON CONFLICT against the LOWER(name) expression index makes this
        # safe to call concurrently and on re-runs.
        with self.conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {self.table} (name) VALUES (%s)
                    ON CONFLICT (LOWER(name)) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id""",
                (name.strip(),),
            )
            row = cur.fetchone()
            if row is None:  # DO NOTHING path fallback (shouldn't hit here)
                cur.execute(
                    f"SELECT id FROM {self.table} WHERE LOWER(name) = %s",
                    (key,),
                )
                row = cur.fetchone()
        self._cache[key] = row[0]
        return row[0]


# ----------------------------------------------------------------------
# Song upsert
# ----------------------------------------------------------------------

SONG_COLS = (
    "title", "artist_id", "original_key", "performance_key", "alt_key",
    "capo_fret", "bpm", "release_year", "bb_structure", "chart_content",
    "source_document",
)


def find_song_id(cur, title, artist_id, source_document):
    cur.execute(
        """SELECT id FROM songs
           WHERE LOWER(title) = LOWER(%s)
             AND artist_id IS NOT DISTINCT FROM %s
             AND source_document IS NOT DISTINCT FROM %s""",
        (title, artist_id, source_document),
    )
    row = cur.fetchone()
    return row[0] if row else None


def upsert_song(cur, song: dict, artist_id: int | None) -> tuple[int, bool]:
    """Insert or update one song row. Returns (song_id, created?)."""
    values = {
        "title": song.get("title"),
        "artist_id": artist_id,
        "original_key": song.get("original_key"),
        "performance_key": song.get("performance_key"),
        "alt_key": song.get("alt_key"),
        "capo_fret": song.get("capo_fret") or 0,
        "bpm": song.get("bpm"),
        "release_year": song.get("release_year"),        # present only post-AI-fill
        "bb_structure": song.get("bb_structure"),
        "chart_content": Jsonb(song.get("chart_content") or {"sections": []}),
        "source_document": song.get("source_document"),
    }

    existing = find_song_id(cur, values["title"], artist_id,
                            values["source_document"])
    if existing is not None:
        set_clause = ", ".join(f"{c} = %({c})s" for c in SONG_COLS)
        cur.execute(
            f"UPDATE songs SET {set_clause} WHERE id = %(id)s",
            {**values, "id": existing},
        )
        return existing, False

    col_list = ", ".join(SONG_COLS)
    ph_list = ", ".join(f"%({c})s" for c in SONG_COLS)
    cur.execute(
        f"INSERT INTO songs ({col_list}) VALUES ({ph_list}) RETURNING id",
        values,
    )
    return cur.fetchone()[0], True


def set_primary_genre(cur, song_id: int, genre_id: int | None):
    if genre_id is None:
        return
    # Re-assert primary genre without clobbering extra non-primary genres.
    cur.execute(
        """INSERT INTO song_genres (song_id, genre_id, is_primary)
           VALUES (%s, %s, true)
           ON CONFLICT (song_id, genre_id)
           DO UPDATE SET is_primary = true""",
        (song_id, genre_id),
    )
    # Ensure no OTHER row for this song is marked primary.
    cur.execute(
        """UPDATE song_genres SET is_primary = false
           WHERE song_id = %s AND genre_id <> %s AND is_primary""",
        (song_id, genre_id),
    )


def set_additional_genres(cur, song_id: int, genre_ids: list[int]):
    """Attach secondary genres (from AI enrichment) as is_primary=false.
    Non-destructive: never touches the primary row, never demotes an existing
    genre, only adds missing links. ON CONFLICT guards re-runs."""
    for gid in genre_ids:
        cur.execute(
            """INSERT INTO song_genres (song_id, genre_id, is_primary)
               VALUES (%s, %s, false)
               ON CONFLICT (song_id, genre_id) DO NOTHING""",
            (song_id, gid),
        )


def set_vibes(cur, song_id: int, vibe_ids: list[int]):
    """Attach vibes (from AI-fill). Adds any missing links; leaves existing
    ones intact. Does not remove vibes not in the list (non-destructive)."""
    for vid in vibe_ids:
        cur.execute(
            """INSERT INTO song_vibes (song_id, vibe_id) VALUES (%s, %s)
               ON CONFLICT DO NOTHING""",
            (song_id, vid),
        )


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------

def load(conn, songs: list[dict], truncate: bool, dry_run: bool = False) -> dict:
    artists = LookupCache(conn, "artists")
    genres = LookupCache(conn, "genres")
    vibes = LookupCache(conn, "vibes")

    stats = {"inserted": 0, "updated": 0, "errors": []}

    # Keep the entire load in one outer transaction. The per-song
    # conn.transaction() blocks below then become SAVEPOINTs, so one bad song
    # can be rolled back without losing the rest of the batch. This also makes
    # --dry-run real: neither a truncate nor any song writes can escape the
    # final rollback.
    with conn.cursor() as cur:
        cur.execute("SELECT 1")

    if truncate:
        with conn.cursor() as cur:
            # RESTART IDENTITY resets serials; CASCADE clears junctions.
            cur.execute("TRUNCATE songs, song_genres, song_vibes "
                        "RESTART IDENTITY CASCADE")

    for i, song in enumerate(songs):
        try:
            with conn.transaction():          # per-song SAVEPOINT
                with conn.cursor() as cur:
                    artist_id = artists.get_or_create(song.get("artist"))
                    genre_id = genres.get_or_create(song.get("primary_genre"))
                    vibe_ids = [vibes.get_or_create(v)
                                for v in (song.get("vibes") or [])]
                    vibe_ids = [v for v in vibe_ids if v is not None]
                    add_genre_ids = [genres.get_or_create(g)
                                     for g in (song.get("additional_genres") or [])]
                    # don't re-add the primary as a secondary
                    add_genre_ids = [g for g in add_genre_ids
                                     if g is not None and g != genre_id]

                    song_id, created = upsert_song(cur, song, artist_id)
                    set_primary_genre(cur, song_id, genre_id)
                    set_additional_genres(cur, song_id, add_genre_ids)
                    set_vibes(cur, song_id, vibe_ids)
            stats["inserted" if created else "updated"] += 1
        except Exception as e:
            stats["errors"].append({
                "index": i,
                "title": song.get("title", "(untitled)"),
                "error": f"{type(e).__name__}: {e}",
            })
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Load songs.json into Postgres.")
    ap.add_argument("songs_json", help="Path to songs.json from batch_parse")
    ap.add_argument("--truncate", action="store_true",
                    help="Wipe song tables first (clean rebuild)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse + connect but roll back instead of committing")
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"),
                    help="Postgres URL (or set DATABASE_URL)")
    args = ap.parse_args()

    if not args.database_url:
        print("Set DATABASE_URL or pass --database-url", file=sys.stderr)
        return 2

    with open(args.songs_json, encoding="utf-8") as f:
        songs = json.load(f)

    conn = psycopg.connect(args.database_url, autocommit=False)
    try:
        stats = load(conn, songs, truncate=args.truncate, dry_run=args.dry_run)
        if args.dry_run:
            print("[dry-run] rolled back — no changes committed")
    finally:
        conn.close()

    print("=" * 56)
    print(f"  LOAD COMPLETE")
    print(f"    inserted : {stats['inserted']}")
    print(f"    updated  : {stats['updated']}")
    print(f"    errors   : {len(stats['errors'])}")
    for e in stats["errors"][:20]:
        print(f"      [{e['index']}] {e['title'][:34]:34} {e['error']}")
    print("=" * 56)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
