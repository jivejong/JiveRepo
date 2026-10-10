from __future__ import annotations

import html
import json
import os
from pathlib import Path
import zipfile
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg.types.json import Jsonb

import chart_parser
from load_songs import load


def _paragraph(*parts: str | None) -> str:
    runs = []
    for part in parts:
        if part is None:
            runs.append("<w:r><w:tab/></w:r>")
        else:
            runs.append(
                '<w:r><w:t xml:space="preserve">'
                f"{html.escape(part)}"
                "</w:t></w:r>"
            )
    return f"<w:p>{''.join(runs)}</w:p>"


def _synthetic_docx(path: Path) -> None:
    paragraphs = [
        _paragraph("Synthetic One – G", None, "Example Band", None, "D"),
        _paragraph("BB: Intro-V-C | capo 2 | D-118"),
        _paragraph("[Verse 1]"),
        _paragraph("{G}Invented phrase one."),
        _paragraph("C   G"),
        _paragraph("Invented phrase two."),
        _paragraph("Synthetic Two – Am", None, "Another Ensemble"),
        _paragraph("[Intro] Am C"),
        _paragraph("Invented refrain line."),
    ]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("word/document.xml", xml.encode("utf-8"))


def test_parser_extracts_metadata_and_song_boundaries(tmp_path: Path) -> None:
    document = tmp_path / "synthetic.docx"
    _synthetic_docx(document)

    songs = chart_parser.parse_docx(str(document))

    assert len(songs) == 2
    first, second = songs
    assert (first.title, first.artist, first.performance_key) == (
        "Synthetic One", "Example Band", "G"
    )
    assert first.original_key == "D"
    assert (first.capo_fret, first.bpm) == (2, 118)
    assert first.bb_structure.startswith("Intro-V-C")
    assert [section["label"] for section in first.chart_content["sections"]] == [
        "Verse 1"
    ]
    first_lines = first.chart_content["sections"][0]["lines"]
    assert [line["type"] for line in first_lines] == ["lyric", "lyric"]
    assert first_lines[0]["text"] == "Invented phrase one."
    assert first_lines[0]["chords"] == [{"sym": "G", "pos": 0}]
    assert first_lines[1]["confidence"] == "low"
    assert (second.title, second.artist, second.performance_key) == (
        "Synthetic Two", "Another Ensemble", "Am"
    )
    second_lines = second.chart_content["sections"][0]["lines"]
    assert second_lines[0]["type"] == "progression"
    assert second_lines[0]["chords"] == ["Am", "C"]
    assert second_lines[1]["text"] == "Invented refrain line."


def test_generated_chart_source_matches_the_app_line_contract(tmp_path: Path) -> None:
    generated = [
        "[[CCM-CHART:2]]",
        "[[CCM-SECTION]]Verse \\[one]",
        "[[CCM-PROGRESSION]]C G [[CCM-PROGRESSION-REPEAT:4]][[CCM-NOTE]]invented \\[\\[note]] cue",
        "[[CCM-REPEAT:2]]Verse 1",
        "[[CCM-RAW]]tab|--invented",
        "[[CCM-LYRIC:low]]\\[\\[literal]] phrase[[CCM-CHORDS]][[C:0:C%23]][[C:0:G]][[C:18:Am]]",
    ]
    expected = {"sections": [{"label": "Verse [one]", "lines": [
        {"type": "progression", "chords": ["C", "G"], "repeat": 4,
         "note": "invented [[note]] cue"},
        {"type": "repeat", "ref": "Verse 1", "times": 2},
        {"type": "raw", "text": "tab|--invented"},
        {"type": "lyric", "text": "[[literal]] phrase",
         "chords": [{"sym": "C#", "pos": 0}, {"sym": "G", "pos": 0},
                    {"sym": "Am", "pos": 18}], "confidence": "low"},
    ]}]}

    assert chart_parser.parse_generated_chart_body("\n".join(generated)) == expected
    assert chart_parser.parse_generated_chart_body(
        chart_parser.serialize_generated_chart(expected)
    ) == expected

    document = tmp_path / "generated.docx"
    paragraphs = [
        _paragraph("Generated Song – C", None, "Example Band", None, "C"),
        *[_paragraph(line) for line in generated],
    ]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>"
    )
    with zipfile.ZipFile(document, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("word/document.xml", xml.encode("utf-8"))
    parsed_song = chart_parser.parse_docx(str(document))[0]
    assert parsed_song.chart_content == expected


def test_blank_paragraphs_and_hard_breaks_keep_chart_spacing_separate(tmp_path: Path) -> None:
    document = tmp_path / "spacing.docx"
    paragraphs = [
        _paragraph("Spacing Song – C", None, "Test Group"),
        _paragraph("[Verse 1]"),
        _paragraph("Invented first verse line."),
        _paragraph(""),
        _paragraph("Invented second verse line."),
        _paragraph(""),
        _paragraph("[Chorus]"),
        _paragraph("Invented refrain."),
        _paragraph(""),
        _paragraph("Next Song – D", None, "Other Group"),
        _paragraph(""),
    ]
    paragraphs[1] = paragraphs[1].replace("</w:p>", '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:p>')
    break_para = ('<w:p><w:r><w:t>Invented before</w:t><w:br/></w:r>'
                  '<w:r><w:t>and after a hard break.</w:t></w:r></w:p>')
    paragraphs.insert(5, break_para)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>")
    with zipfile.ZipFile(document, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("word/document.xml", xml.encode("utf-8"))

    song = chart_parser.parse_docx(str(document))[0]
    sections = song.chart_content["sections"]
    assert [section["label"] for section in sections] == ["Verse 1", "Chorus"]
    assert [line["type"] for line in sections[0]["lines"]] == [
        "lyric", "spacer", "lyric", "lyric", "lyric",
    ]
    assert [line["type"] for line in sections[1]["lines"]] == ["lyric"]


def test_docx_text_breaks_preserve_terminal_separators_but_ignore_page_controls(tmp_path: Path) -> None:
    document = tmp_path / "break-boundaries.docx"
    paragraphs = [
        _paragraph("Break Song – C", None, "Test Group"),
        _paragraph("[Verse]"),
        _paragraph("Invented opening."),
        ('<w:p><w:r><w:t>Invented interior one.</w:t><w:br/>'
         '<w:t>Invented interior two.</w:t></w:r></w:p>'),
        ('<w:p><w:r><w:t>Invented carriage interior one.</w:t><w:cr/>'
         '<w:t>Invented carriage interior two.</w:t></w:r></w:p>'),
        ('<w:p><w:r><w:t>Invented consecutive one.</w:t><w:br/><w:br/>'
         '<w:t>Invented consecutive two.</w:t></w:r></w:p>'),
        ('<w:p><w:r><w:t>Invented terminal text break.</w:t><w:br/></w:r></w:p>'),
        _paragraph(""),
        _paragraph("Invented after terminal text break."),
        ('<w:p><w:r><w:t>Invented terminal carriage break.</w:t><w:cr/></w:r></w:p>'),
        _paragraph("Invented after carriage break."),
        ('<w:p><w:r><w:t>Page control before</w:t><w:br w:type="page"/>'
         '<w:t>page control after</w:t><w:br w:type="column"/>'
         '<w:t>column control after</w:t></w:r></w:p>'),
        _paragraph(""),
        _paragraph("Next Song – D", None, "Other Group"),
    ]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>")
    with zipfile.ZipFile(document, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("word/document.xml", xml.encode("utf-8"))

    song = chart_parser.parse_docx(str(document))[0]
    assert song.chart_content["sections"][0]["lines"] == [
        {"type": "lyric", "text": "Invented opening.", "chords": [], "confidence": "high"},
        {"type": "lyric", "text": "Invented interior one.", "chords": [], "confidence": "high"},
        {"type": "lyric", "text": "Invented interior two.", "chords": [], "confidence": "high"},
        {"type": "lyric", "text": "Invented carriage interior one.", "chords": [], "confidence": "high"},
        {"type": "lyric", "text": "Invented carriage interior two.", "chords": [], "confidence": "high"},
        {"type": "lyric", "text": "Invented consecutive one.", "chords": [], "confidence": "high"},
        {"type": "spacer"},
        {"type": "lyric", "text": "Invented consecutive two.", "chords": [], "confidence": "high"},
        {"type": "lyric", "text": "Invented terminal text break.", "chords": [], "confidence": "high"},
        {"type": "spacer"},
        {"type": "lyric", "text": "Invented after terminal text break.", "chords": [], "confidence": "high"},
        {"type": "lyric", "text": "Invented terminal carriage break.", "chords": [], "confidence": "high"},
        {"type": "spacer"},
        {"type": "lyric", "text": "Invented after carriage break.", "chords": [], "confidence": "high"},
        {"type": "lyric", "text": "Page control beforepage control aftercolumn control after",
         "chords": [], "confidence": "high"},
    ]


def test_ccm_v1_v2_docx_and_python_roundtrip_preserve_columns_and_raw_spaces(tmp_path: Path) -> None:
    assert chart_parser.pair_space_aligned("C          G", "x")["chords"] == [
        {"sym": "C", "pos": 0}, {"sym": "G", "pos": 11},
    ]
    assert chart_parser.parse_inline_brace_line("{C}🎸{G}x")["chords"] == [
        {"sym": "C", "pos": 0}, {"sym": "G", "pos": 2},
    ]
    v1 = ["[[CCM-CHART:1]]", "[[CCM-SECTION]]Verse",
          "[[CCM-RAW]]raw text   ", "[[CCM-LYRIC:low]]A[[C:G]]B"]
    v1_path = tmp_path / "legacy.docx"
    _write_generated_docx(v1_path, v1)
    parsed_v1 = chart_parser.parse_docx(str(v1_path))[0].chart_content
    assert parsed_v1["sections"][0]["lines"] == [
        {"type": "raw", "text": "raw text   "},
        {"type": "lyric", "text": "AB", "chords": [{"sym": "G", "pos": 1}], "confidence": "low"},
    ]

    chart = {"sections": [{"label": "Unicode", "lines": [
        {"type": "lyric", "text": "🎸x", "chords": [{"sym": "Am", "pos": 4}], "confidence": "high"},
        {"type": "spacer"},
        {"type": "raw", "text": "trailing   "},
    ]}, {"label": "Chorus", "lines": [
        {"type": "spacer"},
        {"type": "raw", "text": "section content"},
    ]}]}
    source = chart_parser.serialize_generated_chart(chart)
    assert chart_parser.parse_generated_chart_body(source) == chart
    v2_path = tmp_path / "current.docx"
    _write_generated_docx(v2_path, source.splitlines())
    assert chart_parser.parse_docx(str(v2_path))[0].chart_content == chart


def _write_generated_docx(path: Path, generated: list[str]) -> None:
    paragraphs = [_paragraph("Generated Song – C", None, "Example Band")]
    paragraphs.extend(_paragraph(line) for line in generated)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("word/document.xml", xml.encode("utf-8"))


APP_TABLES = (
    "artists",
    "genres",
    "vibes",
    "songs",
    "song_vibes",
    "song_genres",
    "setlists",
    "setlist_songs",
)


def _scratch_database_url() -> str:
    if os.environ.get("PIPELINE_TEST_DATABASE") != "ccm_pipelinetest":
        raise RuntimeError(
            "Set PIPELINE_TEST_DATABASE=ccm_pipelinetest after initializing it "
            "with the app migration runner."
        )
    base = os.environ.get("DATABASE_URL")
    if not base:
        raise RuntimeError("Compose DATABASE_URL is required for the scratch test.")
    parts = urlsplit(base)
    return urlunsplit((parts.scheme, parts.netloc, "/ccm_pipelinetest", parts.query, parts.fragment))


def _ensure_seed_rows(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO artists(name) VALUES (%s) "
            "ON CONFLICT (lower(name)) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            ("T13 pytest seed artist",),
        )
        artist_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO genres(name) VALUES (%s) "
            "ON CONFLICT (lower(name)) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            ("T13 pytest seed genre",),
        )
        genre_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO vibes(name) VALUES (%s) "
            "ON CONFLICT (lower(name)) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            ("T13 pytest seed vibe",),
        )
        vibe_id = cur.fetchone()[0]
        cur.execute(
            """SELECT id FROM songs WHERE lower(title)=lower(%s)
               AND artist_id=%s AND source_document=%s""",
            ("T13 pytest existing song", artist_id, "t13-pytest-seed.docx"),
        )
        row = cur.fetchone()
        if row:
            song_id = row[0]
        else:
            cur.execute(
                """INSERT INTO songs(title,artist_id,performance_key,chart_content,source_document)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                (
                    "T13 pytest existing song",
                    artist_id,
                    "C",
                    Jsonb({"sections": [{"label": "seed", "lines": []}]}),
                    "t13-pytest-seed.docx",
                ),
            )
            song_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO song_genres(song_id,genre_id,is_primary) VALUES (%s,%s,true) "
            "ON CONFLICT (song_id,genre_id) DO UPDATE SET is_primary=true",
            (song_id, genre_id),
        )
        cur.execute(
            "INSERT INTO song_vibes(song_id,vibe_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (song_id, vibe_id),
        )
        cur.execute("SELECT id FROM setlists WHERE name=%s", ("T13 pytest seed setlist",))
        row = cur.fetchone()
        if row:
            setlist_id = row[0]
        else:
            cur.execute(
                "INSERT INTO setlists(name) VALUES (%s) RETURNING id",
                ("T13 pytest seed setlist",),
            )
            setlist_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO setlist_songs(setlist_id,song_id,position)
               VALUES (%s,%s,1) ON CONFLICT (setlist_id,position) DO NOTHING""",
            (setlist_id, song_id),
        )
    conn.commit()


def _table_contents(conn: psycopg.Connection) -> dict[str, list[str]]:
    snapshot = {}
    with conn.cursor() as cur:
        for table in APP_TABLES:
            cur.execute(f"SELECT * FROM public.{table}")
            rows = [json.dumps(row, default=str, sort_keys=True) for row in cur.fetchall()]
            snapshot[table] = sorted(rows)
    return snapshot


def test_loader_dry_run_preserves_scratch_table_contents() -> None:
    with psycopg.connect(_scratch_database_url(), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            assert cur.fetchone()[0] == "ccm_pipelinetest"
        _ensure_seed_rows(conn)
        before = _table_contents(conn)
        batch = [
            {
                "title": "T13 pytest existing song",
                "artist": "T13 pytest seed artist",
                "performance_key": "D",
                "primary_genre": "T13 pytest seed genre",
                "additional_genres": ["T13 pytest temporary secondary genre"],
                "vibes": ["T13 pytest replacement vibe"],
                "source_document": "t13-pytest-seed.docx",
                "chart_content": {"sections": [{"label": "changed", "lines": []}]},
            },
            {
                "title": "T13 pytest new song",
                "artist": "T13 pytest new artist",
                "performance_key": "G",
                "primary_genre": "T13 pytest new genre",
                "source_document": "t13-pytest-new.docx",
                "chart_content": {"sections": [{"label": "new", "lines": []}]},
            },
        ]

        result = load(conn, batch, truncate=False, dry_run=True)
        after = _table_contents(conn)

    assert result == {"inserted": 1, "updated": 1, "errors": []}
    assert after == before
