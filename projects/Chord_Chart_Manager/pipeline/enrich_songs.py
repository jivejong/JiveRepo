"""
enrich_songs.py — Fill song metadata from MusicBrainz + GetSongBPM.

Replaces the earlier LLM-based enrichment with factual data sources:
    - MusicBrainz  -> release year (era), genres, best-effort mood tags (vibes)
    - GetSongBPM   -> BPM (tempo) and musical key

Same place in the pipeline and same input/output contract as before, so the
loader is unchanged:
    docs -> parse -> batch -> [ENRICH] -> load
Reads songs.json, writes songs.enriched.json with `release_year`, `vibes`,
`additional_genres`, and (only when the parser didn't already find them)
`bpm` / `original_key`.

Key properties (kept from the previous design):
  * PARSED VALUES ALWAYS WIN. bpm/original_key found in the docs are never
    overwritten by an API value.
  * RESUMABLE. Raw API results are cached to disk keyed by (title, artist),
    so an interrupted run doesn't re-hit the network. This matters far more
    now: MusicBrainz is capped at ~1 req/sec, so a full ~2000-song run takes
    over an hour of wall-clock even though it's cheap.
  * FAILURE-ISOLATED. A song with no match passes through un-enriched and
    is flagged; the batch continues.
  * TESTABLE OFFLINE. Query building, response parsing, and merge are pure
    functions; the two HTTP callers are the only impure pieces and are
    injected, so tests run without network.

Operational requirements (READ THIS):
  * MusicBrainz REQUIRES a descriptive User-Agent with contact info, or it
    throttles/blocks you. Set MUSICBRAINZ_CONTACT to an email or URL.
  * GetSongBPM REQUIRES an API key (GETSONGBPM_API_KEY) AND a visible backlink
    to getsongbpm.com in the app (their terms — the app footer carries it).

Env:
    MUSICBRAINZ_CONTACT   e.g. "you@example.com" or "https://your.site"
    GETSONGBPM_API_KEY    from getsongbpm.com/api  (optional; without it, BPM
                          and key are skipped and only MusicBrainz runs)

Usage:
    python enrich_songs.py songs.json -o songs.enriched.json [--limit N]
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Callable, Optional


MB_BASE = "https://musicbrainz.org/ws/2"
GSB_BASE = "https://api.getsongbpm.com"

APP_NAME = "ChordChartApp"
APP_VERSION = "1.0"

# MusicBrainz: 1 req/sec. Use a hair over 1s. GetSongBPM: 3000/hr (~1 req/1.2s).
MB_MIN_INTERVAL = 1.1
GSB_MIN_INTERVAL = 1.3

# MusicBrainz search score below which we treat a match as unreliable and flag
# the song for review rather than trusting the metadata.
MB_SCORE_THRESHOLD = 90

# Best-effort vibe harvest: MusicBrainz folksonomy tags that are mood-ish.
# Anything here found among a recording's tags becomes a vibe. This is
# intentionally small and conservative — vibe is not something these factual
# APIs model well, so coverage is partial and the rest is manual in the app.
MOOD_TAGS = {
    "upbeat", "mellow", "chill", "energetic", "melancholic", "melancholy",
    "romantic", "dark", "happy", "sad", "aggressive", "dreamy", "funky",
    "groovy", "driving", "anthemic", "laid-back", "relaxed", "moody",
    "uplifting", "haunting", "sombre", "somber", "joyful", "wistful",
    "atmospheric", "epic", "gentle", "raw", "soulful", "hypnotic",
}


# ----------------------------------------------------------------------
# HTTP (impure; injected so tests can mock it)
# ----------------------------------------------------------------------

class Throttle:
    """Enforce a minimum interval between calls (per API)."""
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self):
        dt = time.monotonic() - self._last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last = time.monotonic()


def http_get_json(url: str, headers: dict, retries: int = 3) -> Any:
    """GET with retry/backoff on 503 (MusicBrainz) and transient errors."""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (503, 429) and attempt < retries - 1:
                time.sleep(2 * (2 ** attempt))
                continue
            if e.code == 404:
                return None
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 * (2 ** attempt))
                continue
            raise
    if last_err:
        raise last_err
    return None


def make_musicbrainz_caller(contact: str) -> Callable[[str], Any]:
    """User-Agent MUST identify the app + a contact, per MB policy."""
    ua = f"{APP_NAME}/{APP_VERSION} ( {contact} )"
    throttle = Throttle(MB_MIN_INTERVAL)

    def call(path_and_query: str) -> Any:
        throttle.wait()
        url = f"{MB_BASE}/{path_and_query}"
        return http_get_json(url, {"User-Agent": ua, "Accept": "application/json"})
    return call


def make_getsongbpm_caller(api_key: str) -> Callable[[str], Any]:
    ua = f"{APP_NAME}/{APP_VERSION}"
    throttle = Throttle(GSB_MIN_INTERVAL)

    def call(path_and_query: str) -> Any:
        throttle.wait()
        sep = "&" if "?" in path_and_query else "?"
        url = f"{GSB_BASE}/{path_and_query}{sep}api_key={urllib.parse.quote(api_key)}"
        return http_get_json(url, {"User-Agent": ua, "Accept": "application/json"})
    return call


# ----------------------------------------------------------------------
# Pure: MusicBrainz query building + parsing
# ----------------------------------------------------------------------

def mb_recording_query(title: str, artist: Optional[str]) -> str:
    """Lucene query for a recording search, artist-constrained when known."""
    def esc(s: str) -> str:
        return s.replace('"', '\\"')
    q = f'recording:"{esc(title)}"'
    if artist:
        q += f' AND artist:"{esc(artist)}"'
    return "recording/?query=" + urllib.parse.quote(q) + "&fmt=json&limit=5"


def parse_mb_search(data: Any) -> Optional[dict]:
    """Pick the best-scored recording. Return its mbid, release year, score."""
    if not data or "recordings" not in data or not data["recordings"]:
        return None
    best = max(data["recordings"], key=lambda r: r.get("score", 0))
    year = None
    frd = best.get("first-release-date") or ""
    if len(frd) >= 4 and frd[:4].isdigit():
        year = int(frd[:4])
    return {
        "mbid": best.get("id"),
        "release_year": year,
        "score": best.get("score", 0),
    }


def mb_genre_lookup(mbid: str) -> str:
    return f"recording/{mbid}?inc=genres+tags&fmt=json"


def parse_mb_genres_and_moods(data: Any) -> dict:
    """From a recording lookup, split genres (by count) from mood-ish tags."""
    genres, moods = [], []
    if isinstance(data, dict):
        for g in sorted(data.get("genres", []),
                        key=lambda x: x.get("count", 0), reverse=True):
            name = (g.get("name") or "").strip()
            if name:
                genres.append(_titlecase_genre(name))
        for t in data.get("tags", []):
            name = (t.get("name") or "").strip().lower()
            if name in MOOD_TAGS:
                moods.append(_titlecase_genre(name))
    return {"genres": genres, "moods": moods}


def _titlecase_genre(name: str) -> str:
    # "heartland rock" -> "Heartland Rock"; leave things like "r&b" mostly alone
    return " ".join(w.capitalize() if w.isalpha() else w for w in name.split())


# ----------------------------------------------------------------------
# Pure: GetSongBPM query building + parsing
# ----------------------------------------------------------------------

def gsb_search_query(title: str) -> str:
    # Search by title; we match the artist among results ourselves (robust to
    # GSB's combined-query quirks).
    return "search/?type=song&lookup=" + urllib.parse.quote(title)


def parse_gsb(data: Any, artist: Optional[str]) -> Optional[dict]:
    """From a GSB search response, choose the result whose artist matches
    (case-insensitive); fall back to the first result. Extract bpm/key/genres."""
    songs = _gsb_songs(data)
    if not songs:
        return None
    chosen = None
    if artist:
        al = artist.strip().lower()
        for s in songs:
            aname = (((s.get("artist") or {}).get("name")) or "").strip().lower()
            if aname == al or al in aname or aname in al:
                chosen = s
                break
    chosen = chosen or songs[0]

    bpm = _to_int(chosen.get("tempo"))
    key_of = (chosen.get("key_of") or "").strip() or None
    genres = [(_titlecase_genre(g)) for g in
              ((chosen.get("artist") or {}).get("genres") or []) if g]
    matched_artist = ((chosen.get("artist") or {}).get("name") or "").strip() or None
    return {"bpm": bpm, "key_of": key_of, "genres": genres,
            "matched_artist": matched_artist}


def _gsb_songs(data: Any) -> list:
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("search"), list):
        return data["search"]
    if isinstance(data.get("song"), dict):
        return [data["song"]]
    # some error responses look like {"search": {"error": "..."}}
    return []


def _to_int(v: Any) -> Optional[int]:
    try:
        n = int(str(v).strip())
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# Pure: merge with PARSED-VALUES-WIN precedence
# ----------------------------------------------------------------------

def _dedupe(seq):
    out, seen = [], set()
    for x in seq:
        k = x.lower()
        if x and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def merge_enrichment(song: dict, mb: Optional[dict], mb_genres: Optional[dict],
                     gsb: Optional[dict]) -> dict:
    """Apply MB + GSB results to a song under existing-wins precedence."""
    out = dict(song)

    # release_year: only MusicBrainz supplies it; parser never does.
    if mb and mb.get("release_year"):
        out["release_year"] = mb["release_year"]
    else:
        out.setdefault("release_year", None)

    # genres: MB recording genres preferred; GSB artist genres as fallback.
    genres = list((mb_genres or {}).get("genres") or [])
    if not genres and gsb:
        genres = list(gsb.get("genres") or [])
    out["additional_genres"] = _dedupe(genres)

    # vibes: best-effort mood tags from MB; often empty (then manual in-app).
    out["vibes"] = _dedupe((mb_genres or {}).get("moods") or [])

    # bpm: parsed value wins; else GetSongBPM tempo.
    if not out.get("bpm") and gsb and gsb.get("bpm"):
        out["bpm"] = gsb["bpm"]
    else:
        out.setdefault("bpm", song.get("bpm"))

    # original_key: parsed value wins; else GetSongBPM key.
    if not out.get("original_key") and gsb and gsb.get("key_of"):
        out["original_key"] = gsb["key_of"]
    else:
        out.setdefault("original_key", song.get("original_key"))

    return out


# ----------------------------------------------------------------------
# Per-song enrichment (orchestrates the callers; still mockable)
# ----------------------------------------------------------------------

def enrich_one(song: dict, mb_call: Optional[Callable], gsb_call: Optional[Callable],
               want_mb_genres: bool = True) -> dict:
    """Fetch from both sources and return the raw pieces (for caching).
    Returns {"mb":..., "mb_genres":..., "gsb":..., "matched": bool, "low_conf": bool}."""
    title = song.get("title") or ""
    artist = song.get("artist")

    mb = mb_genres = gsb = None
    low_conf = False

    if mb_call and title:
        mb_raw = mb_call(mb_recording_query(title, artist))
        mb = parse_mb_search(mb_raw)
        if mb:
            if mb.get("score", 0) < MB_SCORE_THRESHOLD:
                low_conf = True
            if want_mb_genres and mb.get("mbid"):
                g_raw = mb_call(mb_genre_lookup(mb["mbid"]))
                mb_genres = parse_mb_genres_and_moods(g_raw)

    if gsb_call and title:
        gsb_raw = gsb_call(gsb_search_query(title))
        gsb = parse_gsb(gsb_raw, artist)

    matched = bool(mb or gsb)
    return {"mb": mb, "mb_genres": mb_genres, "gsb": gsb,
            "matched": matched, "low_conf": low_conf}


# ----------------------------------------------------------------------
# Cache + run loop
# ----------------------------------------------------------------------

def cache_key(song: dict) -> str:
    return f"{(song.get('title') or '').strip().lower()}|" \
           f"{(song.get('artist') or '').strip().lower()}"


def load_cache(path: str) -> dict:
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(path: str, cache: dict) -> None:
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, path)


def run(songs: list[dict], mb_call: Optional[Callable], gsb_call: Optional[Callable],
        cache_path: Optional[str] = None, limit: Optional[int] = None,
        want_mb_genres: bool = True, flush_every: int = 10):
    cache = load_cache(cache_path) if cache_path else {}
    stats = {"enriched": 0, "from_cache": 0, "no_match": [], "low_confidence": [],
             "errors": []}

    enriched: list[dict] = []
    processed = 0
    for song in songs:
        key = cache_key(song)

        if key in cache:
            c = cache[key]
            enriched.append(merge_enrichment(song, c.get("mb"),
                                             c.get("mb_genres"), c.get("gsb")))
            stats["from_cache"] += 1
            if not c.get("matched"):
                stats["no_match"].append(song.get("title"))
            continue

        if limit is not None and processed >= limit:
            enriched.append(song)     # pass through un-enriched
            continue

        try:
            pieces = enrich_one(song, mb_call, gsb_call, want_mb_genres)
            cache[key] = pieces
            enriched.append(merge_enrichment(song, pieces.get("mb"),
                                             pieces.get("mb_genres"),
                                             pieces.get("gsb")))
            stats["enriched"] += 1
            if not pieces["matched"]:
                stats["no_match"].append(song.get("title"))
            elif pieces["low_conf"]:
                stats["low_confidence"].append(song.get("title"))
        except Exception as e:  # noqa: BLE001
            stats["errors"].append({"title": song.get("title", "(untitled)"),
                                    "error": f"{type(e).__name__}: {e}"})
            enriched.append(song)
        processed += 1

        if cache_path and stats["enriched"] % flush_every == 0:
            save_cache(cache_path, cache)

    if cache_path:
        save_cache(cache_path, cache)
    return enriched, stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Enrich songs.json from MusicBrainz + GetSongBPM.")
    ap.add_argument("songs_json")
    ap.add_argument("-o", "--output", default="songs.enriched.json")
    ap.add_argument("--cache", default="enrich_cache.json")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only hit the network for the first N uncached songs")
    ap.add_argument("--no-mb-genres", action="store_true",
                    help="Skip the MusicBrainz genre lookup (1 fewer call/song; "
                         "genres then come only from GetSongBPM artist tags)")
    args = ap.parse_args()

    contact = os.environ.get("MUSICBRAINZ_CONTACT")
    gsb_key = os.environ.get("GETSONGBPM_API_KEY")

    if not contact:
        print("WARNING: MUSICBRAINZ_CONTACT not set. MusicBrainz requires a "
              "contact in the User-Agent or it will throttle/block you.",
              file=sys.stderr)
        contact = "unset-contact@example.com"
    mb_call = make_musicbrainz_caller(contact)

    gsb_call = None
    if gsb_key:
        gsb_call = make_getsongbpm_caller(gsb_key)
    else:
        print("WARNING: GETSONGBPM_API_KEY not set — BPM and key will be "
              "skipped (MusicBrainz-only run).", file=sys.stderr)

    with open(args.songs_json, encoding="utf-8") as f:
        songs = json.load(f)

    enriched, stats = run(songs, mb_call, gsb_call, cache_path=args.cache,
                          limit=args.limit, want_mb_genres=not args.no_mb_genres)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    print("=" * 60)
    print("  ENRICHMENT COMPLETE (MusicBrainz + GetSongBPM)")
    print(f"    newly enriched : {stats['enriched']}")
    print(f"    from cache     : {stats['from_cache']}")
    print(f"    no match       : {len(stats['no_match'])}")
    print(f"    low confidence : {len(stats['low_confidence'])} (MB score < "
          f"{MB_SCORE_THRESHOLD} — review these)")
    print(f"    errors         : {len(stats['errors'])}")
    for t in stats["no_match"][:15]:
        print(f"      no match: {t}")
    for e in stats["errors"][:15]:
        print(f"      error: {e['title']} — {e['error']}")
    print(f"    output         : {args.output}")
    print("=" * 60)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
