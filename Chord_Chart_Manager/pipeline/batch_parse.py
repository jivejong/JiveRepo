"""
batch_parse.py — Run chart_parser across a folder of genre .docx files.

Usage:
    python batch_parse.py INPUT_DIR [-o OUTPUT_DIR] [--per-doc] [--strict]

What it does:
  * Globs INPUT_DIR for *.docx (skips Word lock files like ~$foo.docx).
  * Parses each doc into songs. Genre resolution, in priority order:
        1. cover-page genre found inside the doc (parser captures it), else
        2. the filename stem  ("Classic Rock.docx" -> "Classic Rock").
    The resolved genre becomes each song's primary_genre.
  * Writes one combined songs.json (array of song dicts). With --per-doc it
    also writes one JSON per source doc.
  * Writes report.json + prints a human-readable summary flagging anything
    that needs review: docs that produced zero songs, songs missing a title/
    artist/performance key, low-confidence line volume, and raw passthroughs.

The report is the point: this is the checkpoint before the AI-fill and loader
stages, so it surfaces quirks this run's docs contain that the sample didn't.
"""

from __future__ import annotations

import os
import sys
import glob
import json
import argparse
import traceback
from collections import Counter

import chart_parser as cp


def resolve_genre(docx_path: str, songs) -> str | None:
    """Cover-page genre wins; fall back to the filename stem."""
    # If the parser captured a cover-page genre, every song already carries it.
    for s in songs:
        if s.primary_genre:
            return s.primary_genre
    stem = os.path.splitext(os.path.basename(docx_path))[0]
    return stem.strip() or None


def song_stats(song) -> dict:
    lines = [ln for sec in song.chart_content["sections"] for ln in sec["lines"]]
    tc = Counter(ln["type"] for ln in lines)
    low = sum(1 for ln in lines if ln.get("confidence") == "low")
    return {
        "sections": len(song.chart_content["sections"]),
        "lines": len(lines),
        "lyric": tc.get("lyric", 0),
        "progression": tc.get("progression", 0),
        "repeat": tc.get("repeat", 0),
        "raw": tc.get("raw", 0),
        "low_confidence": low,
    }


def missing_fields(song) -> list[str]:
    """Fields whose absence should be flagged for review."""
    miss = []
    if not song.title:
        miss.append("title")
    if not song.artist:
        miss.append("artist")
    if not song.performance_key:
        miss.append("performance_key")
    # A song with no lyric and no progression lines probably mis-parsed.
    st = song_stats(song)
    if st["lyric"] == 0 and st["progression"] == 0:
        miss.append("no_content")
    return miss


def run(input_dir: str, output_dir: str, per_doc: bool, strict: bool) -> int:
    docs = sorted(
        p for p in glob.glob(os.path.join(input_dir, "*.docx"))
        if not os.path.basename(p).startswith("~$")   # Word lock files
    )
    if not docs:
        print(f"No .docx files found in {input_dir!r}", file=sys.stderr)
        return 2

    os.makedirs(output_dir, exist_ok=True)

    all_songs: list[dict] = []
    doc_reports: list[dict] = []
    flagged: list[dict] = []
    failures: list[dict] = []

    corpus = Counter()

    for path in docs:
        name = os.path.basename(path)
        try:
            songs = cp.parse_docx(path)
        except Exception as e:  # a bad doc shouldn't kill the whole run
            failures.append({"doc": name, "error": f"{type(e).__name__}: {e}",
                             "trace": traceback.format_exc().splitlines()[-3:]})
            if strict:
                print(f"FAILED on {name} (strict mode):\n{traceback.format_exc()}",
                      file=sys.stderr)
                return 1
            doc_reports.append({"doc": name, "songs": 0, "error": True})
            continue

        genre = resolve_genre(path, songs)
        # Stamp resolved genre onto every song (fills the filename fallback case).
        for s in songs:
            if not s.primary_genre:
                s.primary_genre = genre

        doc_song_dicts = [cp.song_to_dict(s) for s in songs]
        all_songs.extend(doc_song_dicts)

        if per_doc:
            stem = os.path.splitext(name)[0]
            with open(os.path.join(output_dir, f"{stem}.json"), "w",
                      encoding="utf-8") as f:
                json.dump(doc_song_dicts, f, indent=2, ensure_ascii=False)

        # per-doc rollup
        agg = Counter()
        for s in songs:
            st = song_stats(s)
            for k, v in st.items():
                agg[k] += v
            miss = missing_fields(s)
            if miss:
                flagged.append({"doc": name, "title": s.title or "(untitled)",
                                "missing": miss})
        # fold this doc's totals into the corpus counter
        corpus.update(agg)
        corpus["songs"] += len(songs)

        doc_reports.append({
            "doc": name,
            "genre": genre,
            "songs": len(songs),
            **dict(agg),
        })

    report = {
        "input_dir": os.path.abspath(input_dir),
        "docs_processed": len(docs),
        "docs_failed": len(failures),
        "total_songs": corpus["songs"],
        "corpus_totals": {k: corpus[k] for k in
                          ("lyric", "progression", "repeat", "raw",
                           "low_confidence")},
        "docs": doc_reports,
        "flagged_songs": flagged,
        "failures": failures,
    }

    with open(os.path.join(output_dir, "songs.json"), "w",
              encoding="utf-8") as f:
        json.dump(all_songs, f, indent=2, ensure_ascii=False)
    with open(os.path.join(output_dir, "report.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    _print_summary(report)
    return 0


def _print_summary(report: dict) -> None:
    print("=" * 64)
    print(f"  BATCH PARSE SUMMARY")
    print("=" * 64)
    print(f"  Docs processed : {report['docs_processed']}"
          f"   (failed: {report['docs_failed']})")
    print(f"  Total songs    : {report['total_songs']}")
    ct = report["corpus_totals"]
    print(f"  Lines          : {ct['lyric']} lyric, {ct['progression']} prog, "
          f"{ct['repeat']} repeat, {ct['raw']} raw")
    print(f"  Low-confidence : {ct['low_confidence']} space-aligned lines "
          f"(review target)")
    print("-" * 64)
    print("  Per doc:")
    for d in report["docs"]:
        if d.get("error"):
            print(f"    ✗ {d['doc']:32} PARSE FAILED")
            continue
        print(f"    • {d['doc']:32} {d['songs']:3} songs  "
              f"genre={d.get('genre')!r}")
    if report["flagged_songs"]:
        print("-" * 64)
        print(f"  ⚠ {len(report['flagged_songs'])} songs flagged for review:")
        for f in report["flagged_songs"][:40]:
            print(f"      [{f['doc'][:20]:20}] {f['title'][:34]:34} "
                  f"missing: {', '.join(f['missing'])}")
        if len(report["flagged_songs"]) > 40:
            print(f"      … and {len(report['flagged_songs']) - 40} more "
                  f"(see report.json)")
    if report["failures"]:
        print("-" * 64)
        print(f"  ✗ {len(report['failures'])} docs failed to parse:")
        for fl in report["failures"]:
            print(f"      {fl['doc']}: {fl['error']}")
    print("=" * 64)


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-parse chord chart docs.")
    ap.add_argument("input_dir", help="Folder containing genre .docx files")
    ap.add_argument("-o", "--output-dir", default="parsed_out",
                    help="Where to write songs.json + report.json")
    ap.add_argument("--per-doc", action="store_true",
                    help="Also write one JSON file per source doc")
    ap.add_argument("--strict", action="store_true",
                    help="Abort on the first doc that fails to parse")
    args = ap.parse_args()
    return run(args.input_dir, args.output_dir, args.per_doc, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
