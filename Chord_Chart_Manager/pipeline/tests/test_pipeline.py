from __future__ import annotations

import html
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
import zipfile
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
import pytest
from psycopg.types.json import Jsonb

import chart_parser
from load_songs import load, validate_parser_diagnostics
import load_songs as loader
import batch_parse


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


def _styled_paragraph(paragraph: str, style: str, extra_ppr: str = "") -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/>{extra_ppr}</w:pPr>'
    return paragraph.replace("<w:p>", f"<w:p>{ppr}", 1)


def _heading(*parts: str | None) -> str:
    return _paragraph(*parts).replace(
        "<w:p>", '<w:p><w:pPr><w:outlineLvl w:val="1"/></w:pPr>', 1)


def _bold_paragraph(paragraph: str) -> str:
    return paragraph.replace("<w:r>", "<w:r><w:rPr><w:b/></w:rPr>", 1)


_HEADING_STYLES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:style w:type="paragraph" w:styleId="Normal"/>'
    '<w:style w:type="paragraph" w:styleId="Heading2">'
    '<w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr>'
    '</w:style></w:styles>'
)


def _with_paragraph_tab_stop(paragraph: str) -> str:
    definition = ('<w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/>'
                  '</w:tabs></w:pPr>')
    return paragraph.replace("<w:p>", "<w:p>" + definition, 1)


def _write_docx(path: Path, paragraphs: list[str], styles_xml: str | None = None) -> None:
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("word/document.xml", xml.encode("utf-8"))
        docx.writestr("word/styles.xml", (styles_xml or _HEADING_STYLES).encode("utf-8"))


def _word_tab_paragraph(*parts: str | None, font_size: int = 16) -> str:
    runs = []
    for part in parts:
        rpr = ('<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
               f'<w:sz w:val="{font_size}"/><w:b/></w:rPr>')
        if part is None:
            runs.append(f"<w:r>{rpr}<w:tab/></w:r>")
        else:
            runs.append(f'<w:r>{rpr}<w:t xml:space="preserve">{html.escape(part)}</w:t></w:r>')
    return f"<w:p>{''.join(runs)}</w:p>"


def _synthetic_docx(path: Path) -> None:
    paragraphs = [
        _heading("Synthetic One – G", None, "Example Band", None, "D"),
        _paragraph("BB: Intro-V-C | capo 2 | D-118"),
        _paragraph("[Verse 1]"),
        _paragraph("{G}Invented phrase one."),
        _paragraph("C   G"),
        _paragraph("Invented phrase two."),
        _heading("Synthetic Two – Am", None, "Another Ensemble"),
        _paragraph("[Intro] Am C"),
        _paragraph("Invented refrain line."),
    ]
    _write_docx(path, paragraphs)


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
    assert first_lines[0]["confidence"] == "low"
    assert first_lines[1]["confidence"] == "low"
    assert "[[CCM-" not in first.chart_source
    assert first.chart_source == (
        "[Verse 1]\nG\nInvented phrase one.\nC   G\nInvented phrase two.\n"
    )
    assert (second.title, second.artist, second.performance_key) == (
        "Synthetic Two", "Another Ensemble", "Am"
    )
    second_lines = second.chart_content["sections"][0]["lines"]
    assert second_lines[0]["type"] == "progression"
    assert second_lines[0]["chords"] == ["Am", "C"]
    assert second_lines[1]["text"] == "Invented refrain line."


def test_docx_pipe_chord_row_retains_literal_source_order(tmp_path: Path) -> None:
    document = tmp_path / "synthetic_pipe_chord.docx"
    _write_docx(document, [
        _heading("Synthetic Pipe Song – G", None, "Example Band"),
        _paragraph("[Verse]"),
        _paragraph("G  D | C G | Em F | G"),
        _paragraph("Invented lyric line."),
    ])

    songs = chart_parser.parse_docx(str(document))

    assert len(songs) == 1
    song = songs[0]
    # A literal chord row directly above a lyric keeps its text and gains
    # ordered chord spans for rendering (same shape as the JS parser).
    assert song.chart_content["sections"][0]["lines"][:2] == [
        {"type": "raw", "text": "G  D | C G | Em F | G", "paired_to_next": True,
         "chord_spans": [
             {"symbol": "G", "start": 0, "end": 1}, {"symbol": "D", "start": 3, "end": 4},
             {"symbol": "C", "start": 7, "end": 8}, {"symbol": "G", "start": 9, "end": 10},
             {"symbol": "Em", "start": 13, "end": 15}, {"symbol": "F", "start": 16, "end": 17},
             {"symbol": "G", "start": 20, "end": 21},
         ]},
        {"type": "lyric", "text": "Invented lyric line.", "chords": [],
         "confidence": "high"},
    ]
    assert song.chart_source == (
        "[Verse]\nG  D | C G | Em F | G\nInvented lyric line.\n"
    )
    assert "|" in song.chart_source
    assert "[[CCM-" not in song.chart_source


def test_ordinary_source_keeps_pipe_rows_raw_and_plain_body_round_trips() -> None:
    source = "[Verse]\nG D (hold Am) | Em F x2 | C\n"
    chart = chart_parser.parse_ordinary_chart_source(source)
    assert chart["sections"][0]["lines"] == [
        {"type": "raw", "text": "G D (hold Am) | Em F x2 | C"},
    ]
    serialized, error = chart_parser.plain_text_chart_body(chart)
    assert error is None
    assert serialized == source

    ambiguous = chart_parser.parse_ordinary_chart_source("G and D | C")
    assert ambiguous["sections"][0]["lines"] == [
        {"type": "raw", "text": "G and D | C"},
    ]
    assert not chart_parser.is_decorated_chord_line("e|--0-2-3--|")


def test_ordered_chord_rows_match_shared_fixture_and_preserve_literal_source() -> None:
    cases = json.loads((Path(__file__).parent / "fixtures" / "ordered_chord_rows.json").read_text())
    for case in cases:
        analyzed_text = case.get("analysis_text", case["text"])
        analysis = chart_parser.ordered_chord_analysis(analyzed_text)
        assert bool(analysis["literal"]) is case["literal"], case["text"]
        spans = [[item["start"], item["end"], item["symbol"]] for item in analysis["spans"]]
        assert spans == case["spans"], case["text"]
        if "partial_transposition" in case:
            assert analysis["partial_transposition"] is case["partial_transposition"]
            assert [item["text"] for item in analysis["unresolved_tokens"]] == case.get(
                "unresolved_tokens", [])
        if "ambiguous" in case:
            assert analysis["ambiguous"] is case["ambiguous"]

        heading = chart_parser.Paragraph("Shared Fixture – G\x01Artist", None, outline_level=1)
        body = chart_parser.Paragraph(case["text"], None, source_index=2)
        song = chart_parser.parse_songs([heading, body])[0]
        if "expected_type" not in case:
            assert song.chart_source.rstrip("\n") == case["text"]
        round_trip = chart_parser.parse_ordinary_chart_source(song.chart_source)
        assert round_trip == song.chart_content
        first = song.chart_content["sections"][0]["lines"][0]
        if case["literal"]:
            assert first == {"type": "raw", "text": case.get("section_tail", case["text"])}
        elif "expected_type" in case:
            assert first["type"] == case["expected_type"]
            assert first["chords"] == []
            assert first["text"] == case.get("analysis_text", case["text"])
            if case["text"] == "A 50% C":
                assert song.chart_source == "A 50% C\n"
        else:
            assert first == {"type": "progression", "chords": ["G", "C", "G"]}


def test_bracketed_section_tail_preserves_interleaved_bars_and_transposes_spans() -> None:
    source = "[Intro] | G | % | Em | % | 2X"
    heading = chart_parser.Paragraph("Section Tail Fixture – G\x01Artist", None,
                                     outline_level=1)
    song = chart_parser.parse_songs([heading, chart_parser.Paragraph(source, None)])[0]
    expected_line = {"type": "raw", "text": " | G | % | Em | % | 2X"}
    assert song.chart_content["sections"][0]["lines"] == [expected_line]
    assert song.chart_source == source + "\n"
    assert chart_parser.parse_ordinary_chart_source(song.chart_source) == song.chart_content
    analysis = chart_parser.ordered_chord_analysis(expected_line["text"])
    assert [(span["symbol"], span["start"], span["end"]) for span in analysis["spans"]] == [
        ("G", 3, 4), ("Em", 11, 13),
    ]


def test_tabbed_decorated_chord_rows_keep_literal_order_and_round_trip() -> None:
    heading = chart_parser.Paragraph("Tabbed Ordered Row – G\x01Artist", None,
                                     outline_level=1)
    row = chart_parser.Paragraph("G -\x01C\x01(4x)", None)
    song = chart_parser.parse_songs([heading, row])[0]

    assert song.chart_content["sections"][0]["lines"] == [
        {"type": "raw", "text": row.text_spaces},
    ]
    assert song.chart_source == row.text_spaces + "\n"
    assert chart_parser.parse_ordinary_chart_source(song.chart_source) == song.chart_content


def test_opening_metadata_is_bounded_and_separate_from_performance_key() -> None:
    heading = chart_parser.Paragraph("Go Your Own Way – G (Bb)\x01Fleetwood Mac", None,
                                     outline_level=1, source_index=1)
    metadata = chart_parser.Paragraph("Capo 3\x01F-135", None, source_index=2)
    chart = chart_parser.Paragraph("[Verse] G - C", None, source_index=3)
    song = chart_parser.parse_songs([heading, metadata, chart])[0]
    assert (song.performance_key, song.alt_key, song.original_key, song.capo_fret, song.bpm) == (
        "G", "Bb", "F", 3, 135)
    assert song.chart_source == "[Verse] G - C\n"
    assert song.chart_content["sections"][0]["lines"] == [
        {"type": "raw", "text": " G - C"},
    ]

    original_heading = chart_parser.Paragraph("Weather – G\x01Band", None,
                                               outline_level=1, source_index=4)
    original = chart_parser.Paragraph("orig Db", None, source_index=5)
    source = chart_parser.Paragraph("[Verse] C", None, source_index=6)
    weather = chart_parser.parse_songs([original_heading, original, source])[0]
    assert (weather.performance_key, weather.original_key, weather.bpm, weather.capo_fret) == (
        "G", "Db", None, 0)
    assert weather.chart_source == "[Verse] C\n"

    lookalike = chart_parser.Paragraph("Capo 2 is a lyric-like body phrase", None, source_index=8)
    later = chart_parser.parse_songs([original_heading, source, lookalike])[0]
    assert later.capo_fret == 0
    assert "Capo 2 is a lyric-like body phrase" in later.chart_source


@pytest.mark.parametrize("annotation", ["A-120", "A - 120", "A \u2013 120"])
def test_opening_original_key_bpm_accepts_bounded_separator_spacing(annotation: str) -> None:
    heading = chart_parser.Paragraph("Metadata spacing \u2013 G\x01Band", None,
                                     outline_level=1, source_index=41)
    opening = chart_parser.Paragraph(annotation, None, source_index=42)
    chart = chart_parser.Paragraph("[Verse] C - G", None, source_index=43)
    body_lookalike = chart_parser.Paragraph("A - 120", None, source_index=44)
    song = chart_parser.parse_songs([heading, opening, chart, body_lookalike])[0]

    assert (song.performance_key, song.original_key, song.bpm) == ("G", "A", 120)
    assert song.chart_source == "[Verse] C - G\nA - 120\n"
    assert song.chart_content["sections"][0]["lines"][0]["text"] == " C - G"
    assert song.chart_content["sections"][0]["lines"][1]["text"] == "A - 120"
    assert not song.warnings


def test_opening_key_bpm_conflict_preserves_annotation_and_blocks_loader() -> None:
    heading = chart_parser.Paragraph("Metadata conflict \u2013 G\x01Band\x01E", None,
                                     outline_level=1, source_index=51)
    opening = chart_parser.Paragraph(" A \u2013 120 ", None, source_index=52)
    song = chart_parser.parse_songs([heading, opening])[0]

    assert song.performance_key == "G"
    assert song.original_key == "E"
    assert song.bpm is None
    assert song.chart_source == " A \u2013 120 \n"
    assert song.chart_content["sections"][0]["lines"][0] == {
        "type": "lyric", "text": " A \u2013 120 ", "chords": [], "confidence": "high",
    }
    assert song.warnings == [{
        "code": "opening_metadata_conflict", "section": "?", "line": "?",
        "reason": "conflicting_opening_metadata:original_key", "paragraph": 52, "span": 1,
    }]
    with pytest.raises(ValueError, match="opening_metadata_conflict"):
        validate_parser_diagnostics([chart_parser.song_to_dict(song)])


def test_mixed_raw_chord_row_pairs_with_following_lyric_and_reports_partial_token() -> None:
    heading = chart_parser.Paragraph("Literal Pair \u2013 G\x01Band", None,
                                     outline_level=1, source_index=60)
    row = chart_parser.Paragraph("  C - G - D/G5x  ", None, source_index=61)
    lyric = chart_parser.Paragraph("  lyric placeholder  ", None, source_index=62)
    song = chart_parser.parse_songs([heading, row, lyric])[0]

    lines = song.chart_content["sections"][0]["lines"]
    assert lines[0] == {
        "type": "raw", "text": "  C - G - D/G5x  ", "paired_to_next": True,
        "chord_spans": [
            {"symbol": "C", "start": 2, "end": 3},
            {"symbol": "G", "start": 6, "end": 7},
        ],
    }
    assert lines[1] == {
        "type": "lyric", "text": "  lyric placeholder  ", "chords": [], "confidence": "high",
    }
    assert song.chart_source == "  C - G - D/G5x  \n  lyric placeholder  \n"
    assert chart_parser.parse_ordinary_chart_source(song.chart_source) == song.chart_content
    assert song.warnings == [{
        "code": "ordered_chord_token_unresolved", "section": 1, "line": 1,
        "reason": "unsupported_chord_like_token:D/G5x", "paragraph": 61, "span": 1,
    }]
    assert batch_parse.song_stats(song)["unresolved_ordered_chord_tokens"] == 1
    with pytest.raises(ValueError, match="ordered_chord_token_unresolved"):
        validate_parser_diagnostics([chart_parser.song_to_dict(song)])


def test_chord_like_substring_inside_unsupported_token_is_not_a_span() -> None:
    analysis = chart_parser.ordered_chord_analysis("C - G - D/Guitar5x")
    assert [span["symbol"] for span in analysis["spans"]] == ["C", "G"]
    assert analysis["unresolved_tokens"] == []
    assert analysis["prose_tokens"] == [{"start": 8, "end": 18, "text": "D/Guitar5x"}]
    assert not analysis["partial_transposition"]


def test_conflicting_opening_metadata_stays_visible_and_blocks_loader() -> None:
    heading = chart_parser.Paragraph("Conflict – G (Bb)\x01Band", None,
                                     outline_level=1, source_index=11)
    metadata = chart_parser.Paragraph("Capo 2\x01F-135", None, source_index=12)
    song = chart_parser.parse_songs([heading, metadata])[0]
    assert song.capo_fret == 3
    assert song.chart_source == "Capo 2 F-135\n"
    assert song.warnings == [{
        "code": "opening_metadata_conflict", "section": "?", "line": "?",
        "reason": "conflicting_opening_metadata:capo", "paragraph": 12, "span": 1,
    }]
    with pytest.raises(ValueError, match="opening_metadata_conflict"):
        validate_parser_diagnostics([chart_parser.song_to_dict(song)])


@pytest.mark.parametrize(("heading", "bb_value", "expected_capo", "expected_key", "expected_bpm",
                          "reason", "paragraph"), [
    ("Capo Conflict – G (A)\x01Band", " Intro-V-C | capo 3 | E-120",
     2, "E", 120, "conflicting_opening_metadata:capo", 22),
    ("Key Conflict – G\x01Band\x01E", " Intro-V-C | F-120",
     0, "E", 120, "conflicting_opening_metadata:original_key", 32),
])
def test_conflicting_bb_metadata_retains_complete_value_and_blocks_loader(
        heading: str, bb_value: str, expected_capo: int, expected_key: str,
        expected_bpm: int | None, reason: str, paragraph: int) -> None:
    song = chart_parser.parse_songs([
        chart_parser.Paragraph(heading, None, outline_level=1, source_index=21),
        chart_parser.Paragraph("BB:" + bb_value, None, source_index=paragraph),
        chart_parser.Paragraph("[Verse] G - C", None, source_index=paragraph + 1),
    ])[0]

    assert song.bb_structure == bb_value
    assert (song.capo_fret, song.original_key, song.bpm) == (
        expected_capo, expected_key, expected_bpm)
    assert song.chart_source == "[Verse] G - C\n"
    assert chart_parser.parse_ordinary_chart_source(song.chart_source) == song.chart_content
    assert song.warnings == [{
        "code": "opening_metadata_conflict", "section": "?", "line": "?",
        "reason": reason, "paragraph": paragraph, "span": 1,
    }]
    with pytest.raises(ValueError, match="opening_metadata_conflict"):
        validate_parser_diagnostics([chart_parser.song_to_dict(song)])


def test_only_effective_outline_headings_start_songs(tmp_path: Path) -> None:
    document = tmp_path / "heading-only-boundaries.docx"
    nonheading = _styled_paragraph(
        _bold_paragraph(_paragraph("  Body Title - C  ", None, "Looks Like Artist")),
        "Normal", "<w:pageBreakBefore/>")
    blank_heading = _styled_paragraph(_paragraph(""), "Heading2")
    rendered = _paragraph("Rendered Title - D", None, "Looks Like Artist").replace(
        "</w:p>", "<w:r><w:lastRenderedPageBreak/></w:r></w:p>")
    _write_docx(document, [
        _heading("Opening Song - G", None, "First Band"),
        blank_heading,
        nonheading,
        _paragraph("  G - C  "),
        _paragraph("[Bridge] G - C - D"),
        rendered,
        _heading("Closing Song - E", None, "Last Band"),
    ])

    paragraphs = chart_parser.load_paragraphs(str(document))
    songs = chart_parser.parse_docx(str(document))

    assert [(song.title, song.artist) for song in songs] == [
        ("Opening Song", "First Band"), ("Closing Song", "Last Band"),
    ]
    assert paragraphs[1].stripped == ""
    assert paragraphs[1].outline_level == 1
    assert paragraphs[2].outline_level is None
    assert paragraphs[2].page_break_before is True
    assert paragraphs[2].text == "  Body Title - C  \x01Looks Like Artist"
    assert paragraphs[2].text_spaces == "  Body Title - C   Looks Like Artist"
    assert paragraphs[5].rendered_page_break_count == 1
    body = songs[0].chart_content
    located_lines = [
        (section["label"], line)
        for section in body["sections"] for line in section["lines"]
    ]
    assert located_lines[0] == (
        None, {"type": "lyric", "text": "  Body Title - C   Looks Like Artist",
               "chords": [], "confidence": "high"})
    assert located_lines[1] == (
        None, {"type": "raw", "text": "  G - C  "})
    assert songs[0].chart_source is not None
    assert chart_parser.parse_ordinary_chart_source(songs[0].chart_source) == body
    assert located_lines[2][0] == "Bridge"
    # Paired to the lyric that follows it; "  G - C  " above ends its section, so it is not.
    assert located_lines[2][1] == {
        "type": "raw", "text": " G - C - D", "paired_to_next": True,
        "chord_spans": [{"symbol": "G", "start": 1, "end": 2},
                        {"symbol": "C", "start": 5, "end": 6},
                        {"symbol": "D", "start": 9, "end": 10}]}
    assert located_lines[-1] == (
        "Bridge", {"type": "lyric", "text": paragraphs[5].text_spaces, "chords": [],
                   "confidence": "high"})
    assert [(song.title, song.artist) for song in songs] == [
        ("Opening Song", "First Band"), ("Closing Song", "Last Band"),
    ]
    assert songs[0].chart_source is not None
    assert chart_parser.parse_ordinary_chart_source(songs[0].chart_source) == body


def test_nonheading_bold_and_tabs_do_not_create_song_boundaries(tmp_path: Path) -> None:
    document = tmp_path / "nonheading-formatting.docx"
    bold_title = _styled_paragraph(
        _bold_paragraph(_paragraph("Bold Title - C")), "Normal")
    inherited_bold = _styled_paragraph(
        _paragraph("Inherited Bold Title - D"), "Normal")
    mark_bold = _styled_paragraph(
        _paragraph("Paragraph Mark - D", None, "Possible Band"), "Normal",
        "<w:rPr><w:b/></w:rPr>")
    styles = _HEADING_STYLES.replace(
        '<w:style w:type="paragraph" w:styleId="Normal"/>',
        '<w:style w:type="paragraph" w:styleId="Normal"><w:rPr><w:b/></w:rPr></w:style>')
    _write_docx(document, [
        _heading("Opening - G", None, "First Band"), bold_title, inherited_bold, mark_bold,
        _paragraph("Tabbed Title - A", None, "Possible Band"),
        _heading("Closing - E", None, "Last Band"),
    ], styles)

    songs = chart_parser.parse_docx(str(document))

    assert [(song.title, song.artist) for song in songs] == [
        ("Opening", "First Band"), ("Closing", "Last Band"),
    ]
    chart_json = json.dumps(songs[0].chart_content, ensure_ascii=False)
    assert all(label in chart_json for label in (
        "Bold Title", "Inherited Bold Title", "Paragraph Mark", "Tabbed Title", "Possible Band"))


def test_outline_heading_levels_and_direct_overrides_define_boundaries(tmp_path: Path) -> None:
    document = tmp_path / "word-outline-boundaries.docx"
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="Normal" w:default="1">'
        '<w:pPr><w:outlineLvl w:val="9"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Base">'
        '<w:pPr><w:outlineLvl w:val="2"/><w:pageBreakBefore/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="NearBody"><w:basedOn w:val="Base"/>'
        '<w:pPr><w:outlineLvl w:val="9"/><w:pageBreakBefore w:val="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="NearHeading"><w:basedOn w:val="NearBody"/>'
        '<w:pPr><w:outlineLvl w:val="8"/></w:pPr></w:style>'
        '</w:styles>')
    _write_docx(document, [
        _styled_paragraph(_paragraph("Direct Heading - C", None, "Band"), "Normal",
                          "<w:outlineLvl w:val=\"0\"/>"),
        _styled_paragraph(_paragraph("Inherited Body - D", None, "Band"), "NearBody"),
        _styled_paragraph(_paragraph("Direct Body - A", None, "Band"), "Base",
                          "<w:outlineLvl w:val=\"9\"/><w:pageBreakBefore w:val=\"false\"/>"),
        _styled_paragraph(_paragraph("Inherited Heading - G", None, "Band"), "NearHeading"),
        _styled_paragraph(_paragraph("Inherited Break Heading – D", None, "Band"), "Base"),
        _styled_paragraph(_paragraph("Direct Break Heading – E", None, "Band"), "NearBody",
                          "<w:outlineLvl w:val=\"1\"/><w:pageBreakBefore/>"),
    ], styles)

    paragraphs = chart_parser.load_paragraphs(str(document))
    songs = chart_parser.parse_docx(str(document))

    assert [p.outline_level for p in paragraphs] == [0, 9, 9, 8, 2, 1]
    assert [p.page_break_before for p in paragraphs] == [
        False, False, False, False, True, True,
    ]
    assert [song.title for song in songs] == [
        "Direct Heading", "Inherited Heading", "Inherited Break Heading",
        "Direct Break Heading",
    ]
    assert "Inherited Body - D" in json.dumps(songs[0].chart_content, ensure_ascii=False)
    assert "Direct Body - A" in json.dumps(songs[0].chart_content, ensure_ascii=False)


def test_default_paragraph_style_can_supply_heading_without_pstyle(tmp_path: Path) -> None:
    document = tmp_path / "default-heading-no-pstyle.docx"
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="DefaultHeading" w:default="1">'
        '<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Body"><w:pPr>'
        '<w:outlineLvl w:val="9"/></w:pPr></w:style></w:styles>')
    implicit_heading = _paragraph("Implicit Heading – C", None, "First Band")
    body = _styled_paragraph(_paragraph("following chart content"), "Body")
    _write_docx(document, [implicit_heading, body], styles)

    paragraphs = chart_parser.load_paragraphs(str(document))
    songs = chart_parser.parse_docx(str(document))

    assert paragraphs[0].style is None
    assert paragraphs[0].outline_level == 0
    assert [(song.title, song.artist) for song in songs] == [
        ("Implicit Heading", "First Band"),
    ]
    assert "following chart content" in json.dumps(
        songs[0].chart_content, ensure_ascii=False)


def test_chart_like_content_before_first_heading_blocks_with_location(tmp_path: Path) -> None:
    document = tmp_path / "preheading-chart.docx"
    _write_docx(document, [_paragraph("[Verse] C G"), _heading("Song - C", None, "Band")])

    with pytest.raises(ValueError, match=r"pre_heading_chart_content_unresolved paragraph=1 span=1"):
        chart_parser.parse_docx(str(document))


def test_explicit_page_break_splits_spans_without_creating_song_boundaries(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    document = tmp_path / "interior-page-break.docx"
    after_break = '<w:t>Next Song – D</w:t><w:tab/><w:t>Next Band</w:t>'
    body_and_title = (
        '<w:p><w:r><w:t>continuing chart span</w:t><w:br w:type="page"/>'
        f'{after_break}</w:r></w:p>'
    )
    continuing = (
        '<w:p><w:r><w:t>line before break</w:t><w:br w:type="page"/>'
        '<w:t>line after break</w:t></w:r></w:p>'
    )
    _write_docx(document, [
        _styled_paragraph(_paragraph("First Song – C", None, "First Band"), "Heading2"),
        continuing,
        body_and_title,
        _paragraph("next chart content"),
    ], _HEADING_STYLES)

    original_boundary = chart_parser._is_song_boundary_candidate
    original_docx_title_spans: list[tuple[int | None, int | None]] = []
    internal_round_trip_title_spans: list[tuple[int | None, int | None]] = []

    def observe_spans(paragraph):
        if paragraph.stripped.startswith("Next Song"):
            location = (paragraph.source_index, paragraph.source_span_index)
            if paragraph.source_index is None:
                internal_round_trip_title_spans.append(location)
            else:
                original_docx_title_spans.append(location)
        return original_boundary(paragraph)

    monkeypatch.setattr(chart_parser, "_is_song_boundary_candidate", observe_spans)
    songs = chart_parser.parse_docx(str(document))

    assert [(song.title, song.artist) for song in songs] == [
        ("First Song", "First Band"),
    ]
    assert songs[0].chart_source is not None
    assert songs[0].chart_content == chart_parser.parse_ordinary_chart_source(
        songs[0].chart_source)
    serialized = json.dumps(songs[0].chart_content, ensure_ascii=False)
    assert all(text in serialized for text in (
        "line before break", "line after break", "continuing chart span",
        "Next Song", "Next Band", "next chart content"))
    assert original_docx_title_spans == [(3, 2)]
    assert internal_round_trip_title_spans
    assert all(source_index is None for source_index, _ in internal_round_trip_title_spans)


def test_section_tail_separate_fallback_checks_full_chart_including_confidence(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    document = tmp_path / "section-tail-fallback.docx"
    _write_docx(document, [
        _heading("Tail Song – C", None, "Example Band"),
        _paragraph("[Verse] e|--0--|"),
        _paragraph("ordinary lyric line"),
    ])
    original_parse_songs = chart_parser.parse_songs
    internal_round_trips = 0

    def force_inline_comparison_mismatch(paragraphs, *args, **kwargs):
        nonlocal internal_round_trips
        result = original_parse_songs(paragraphs, *args, **kwargs)
        if kwargs.get("_derive_plain_body") is False:
            internal_round_trips += 1
            if internal_round_trips == 1:
                lyric = next(line for section in result[0].chart_content["sections"]
                             for line in section["lines"] if line.get("type") == "lyric")
                lyric["confidence"] = "synthetic_mismatch"
        return result

    monkeypatch.setattr(chart_parser, "parse_songs", force_inline_comparison_mismatch)
    song = chart_parser.parse_docx(str(document))[0]

    assert internal_round_trips == 2
    separated, separated_error = chart_parser.plain_text_chart_body(song.chart_content)
    assert separated_error is None
    assert chart_parser.parse_ordinary_chart_source(separated) == song.chart_content
    assert song.chart_source.startswith("[Verse]\n e|--0--|\n")
    reparsed = chart_parser.parse_ordinary_chart_source(song.chart_source)
    assert reparsed == song.chart_content
    assert any(line.get("confidence") == "high"
               for section in reparsed["sections"] for line in section["lines"])


def test_structural_title_without_artist_keeps_artist_missing(tmp_path: Path) -> None:
    document = tmp_path / "missing-artist-heading.docx"
    _write_docx(document, [
        _heading("Moondance – Am"),
        _paragraph("C G"),
    ], _HEADING_STYLES)

    songs = chart_parser.parse_docx(str(document))

    assert len(songs) == 1
    assert songs[0].title == "Moondance"
    assert songs[0].performance_key == "Am"
    assert songs[0].artist is None
    assert songs[0].warnings == []


def test_parenthesized_heading_key_sets_capo_without_becoming_artist(tmp_path: Path) -> None:
    document = tmp_path / "parenthesized-capo-key.docx"
    _write_docx(document, [
        _heading("Example One – D", None, "(E)", None, "Example Band"),
        _paragraph("[Verse] D G"),
        _heading("Example Two – D (E)", None, "Second Band", None, "G"),
        _paragraph("[Verse] D A"),
        _heading("Example Three – Dm", None, "(Em)", None, "Third Band"),
        _paragraph("[Verse] Dm Gm"),
    ])

    songs = chart_parser.parse_docx(str(document))

    assert [(song.title, song.artist, song.performance_key, song.alt_key,
             song.original_key, song.capo_fret) for song in songs] == [
        ("Example One", "Example Band", "D", "E", None, 2),
        ("Example Two", "Second Band", "D", "E", "G", 2),
        ("Example Three", "Third Band", "Dm", "Em", None, 2),
    ]
    assert all(song.warnings == [] for song in songs)
    assert all(song.chart_source is not None and
               chart_parser.parse_ordinary_chart_source(song.chart_source) == song.chart_content
               for song in songs)


def test_parenthesized_heading_key_with_incompatible_quality_is_blocking(tmp_path: Path) -> None:
    document = tmp_path / "unresolved-capo-key.docx"
    _write_docx(document, [
        _heading("Example – Dm", None, "(E)", None, "Example Band"),
        _paragraph("[Verse] Dm Gm"),
    ])

    song = chart_parser.parse_docx(str(document))[0]

    assert (song.title, song.artist, song.performance_key, song.alt_key, song.capo_fret) == (
        "Example", "Example Band", "Dm", "E", 0)
    assert [(warning["reason"], warning["paragraph"], warning["span"])
            for warning in song.warnings] == [("heading_capo_key_relation_unresolved", 1, 1)]
    with pytest.raises(ValueError, match="heading_capo_key_relation_unresolved"):
        validate_parser_diagnostics([chart_parser.song_to_dict(song)])


def test_structural_heading_unresolved_fields_are_reported_without_inference(tmp_path: Path) -> None:
    document = tmp_path / "unresolved-heading.docx"
    heading = _heading("Candidate Title – G/C", None, "Known Band")
    _write_docx(document, [
        _heading("Opening – C", None, "First Band"), heading,
        _paragraph("content after unresolved heading"),
    ], _HEADING_STYLES)

    songs = chart_parser.parse_docx(str(document))

    assert [(song.title, song.artist) for song in songs] == [
        ("Opening", "First Band"), ("Candidate Title", "Known Band"),
    ]
    assert songs[1].performance_key is None
    assert songs[1].warnings == [{
        "code": "plain_text_body_unresolved", "section": "?", "line": "?",
        "reason": "unrecognized_heading_key_field", "paragraph": 2, "span": 1,
        "style": None, "outline_level": 1, "page_break_before": False,
        "candidate_title": "Candidate Title", "candidate_artist": "Known Band",
        "candidate_performance_key": None, "candidate_key_field": "G/C",
    }]
    assert "content after unresolved heading" in json.dumps(
        songs[1].chart_content, ensure_ascii=False)
    with pytest.raises(ValueError, match="unrecognized_heading_key_field"):
        validate_parser_diagnostics([chart_parser.song_to_dict(songs[1])])


def test_single_unsupported_slash_key_heading_keeps_boundary_and_location(tmp_path: Path) -> None:
    document = tmp_path / "single-unsupported-key.docx"
    _write_docx(document, [
        _heading("Opening – C", None, "First Band"),
        _paragraph("opening chart content"),
        _heading("Candidate Title – G/C"),
        _paragraph("candidate chart content"),
    ])

    songs = chart_parser.parse_docx(str(document))

    assert [(song.title, song.artist) for song in songs] == [
        ("Opening", "First Band"), ("Candidate Title", None),
    ]
    assert songs[1].performance_key is None
    assert songs[1].warnings == [{
        "code": "plain_text_body_unresolved", "section": "?", "line": "?",
        "reason": "unrecognized_heading_key_field", "paragraph": 3, "span": 1,
        "style": None, "outline_level": 1, "page_break_before": False,
        "candidate_title": "Candidate Title", "candidate_artist": None,
        "candidate_performance_key": None, "candidate_key_field": "G/C",
    }]
    assert "opening chart content" in json.dumps(songs[0].chart_content)
    assert "candidate chart content" in json.dumps(songs[1].chart_content)
    with pytest.raises(ValueError, match="unrecognized_heading_key_field"):
        validate_parser_diagnostics([chart_parser.song_to_dict(songs[1])])


def test_artist_only_and_supported_key_headings_keep_existing_metadata(tmp_path: Path) -> None:
    document = tmp_path / "supported-headings.docx"
    _write_docx(document, [
        _heading("Tabbed Artist", None, "AC/DC"),
        _paragraph("first chart content"),
        _heading("Dashed Artist - Known Band"),
        _paragraph("second chart content"),
        _heading("Supported – C", None, "Example Group", None, "G"),
        _paragraph("third chart content"),
    ])

    songs = chart_parser.parse_docx(str(document))

    assert [(song.title, song.artist, song.performance_key, song.original_key)
            for song in songs] == [
        ("Tabbed Artist", "AC/DC", None, None),
        ("Dashed Artist", "Known Band", None, None),
        ("Supported", "Example Group", "C", "G"),
    ]
    assert all(song.warnings == [] for song in songs)
    for index, song in enumerate(songs, 1):
        assert f"{('first', 'second', 'third')[index - 1]} chart content" in json.dumps(
            song.chart_content)


@pytest.mark.parametrize("paragraphs,expected_source,expected_type", [
    ([_paragraph("[Verse] direction 2x")], "[Verse] direction 2x\n", "raw"),
    ([_paragraph("[Intro] C D | Em F x2")], "[Intro] C D | Em F x2\n", "raw"),
    ([_paragraph("Intro: C D | Em F x2")], "Intro: C D | Em F x2\n", "raw"),
    ([_paragraph("[Intro] C D Em F x2")], "[Intro] C D Em F x2\n", "raw"),
    ([_paragraph("Intro: C D Em F x2")], "Intro: C D Em F x2\n", "raw"),
    ([_paragraph("Intro: lyric only phrase")], "[Intro]\nlyric only phrase\n", "lyric"),
])
def test_section_tails_keep_readable_exact_round_trips(
        tmp_path: Path, paragraphs: list[str], expected_source: str,
        expected_type: str) -> None:
    document = tmp_path / "section-tail.docx"
    _write_docx(document, [_heading("Tail Song – C", None, "Example Band"), *paragraphs])

    song = chart_parser.parse_docx(str(document))[0]

    lines = [line for section in song.chart_content["sections"] for line in section["lines"]]
    assert lines[0]["type"] == expected_type
    if expected_type == "progression":
        assert lines[0]["repeat"] == 2
    assert song.chart_source == expected_source
    assert "[[CCM-" not in song.chart_source
    assert chart_parser.parse_ordinary_chart_source(song.chart_source) == song.chart_content


def test_curly_apostrophe_tabbed_lyric_keeps_blocking_warning_without_proven_metrics(
        tmp_path: Path) -> None:
    document = tmp_path / "curly-apostrophe-tab.docx"
    _write_docx(document, [
        _heading("Tab Pair – C", None, "Example Band"),
        _paragraph("[Verse]"),
        _word_tab_paragraph("C", None, "G"),
        _word_tab_paragraph("A lyric with ’ apostrophe", None, "continued"),
    ])

    paragraphs = chart_parser.load_paragraphs(str(document))
    lyric = paragraphs[3]
    song = chart_parser.parse_docx(str(document))[0]

    assert lyric.tab_layout_error == "unsupported_non_ascii_tabbed_paragraph"
    assert lyric.physical_positions is None
    assert any(w.get("reason") == "unsupported_non_ascii_tabbed_paragraph"
               for w in song.warnings)
    with pytest.raises(ValueError, match="unsupported_non_ascii_tabbed_paragraph"):
        validate_parser_diagnostics([chart_parser.song_to_dict(song)])


def test_supported_mixed_tabs_normalize_to_plain_text_without_blocking(tmp_path: Path) -> None:
    document = tmp_path / "mixed-tabs.docx"
    paragraphs = [
        _heading("Tab Guard Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"),
        _word_tab_paragraph("C", None, "G   ", None, "D"),
        _word_tab_paragraph("invented lyric for tab guard"),
    ]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>")
    with zipfile.ZipFile(document, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("word/document.xml", xml.encode("utf-8"))
        docx.writestr("word/settings.xml", _DEFAULT_TAB_SETTINGS)

    song = chart_parser.parse_docx(str(document))[0]
    line = song.chart_content["sections"][0]["lines"][0]
    assert [chord["sym"] for chord in line["chords"]] == ["C", "G", "D"]
    assert song.parser_diagnostics_version == 1
    assert song.warnings == []
    assert song.chart_content["sections"][0]["lines"][0]["chords"] == [
        {"sym": "C", "pos": 0}, {"sym": "G", "pos": 7}, {"sym": "D", "pos": 15},
    ]
    assert "[[CCM-" not in song.chart_source
    assert song.chart_source == "[Verse]\nC      G       D\ninvented lyric for tab guard\n"
    artifact = chart_parser.song_to_dict(song)
    assert artifact["warnings"] == song.warnings
    validate_parser_diagnostics([artifact])


def test_paragraph_tab_stops_do_not_change_text_anchors_or_diagnostics(tmp_path: Path) -> None:
    plain = tmp_path / "without-stops.docx"
    with_stops = tmp_path / "with-stops.docx"
    base = [
        _heading("Tab Stop Control \u2013 C", None, "Invented Artist"),
        _paragraph("[Verse]"), _paragraph("C   G"), _paragraph("invented lyric"),
    ]
    defined = [*base[:2], _with_paragraph_tab_stop(base[2]),
               _with_paragraph_tab_stop(base[3])]
    _write_docx(plain, base)
    _write_docx(with_stops, defined)

    plain_paras = chart_parser.load_paragraphs(str(plain))
    stop_paras = chart_parser.load_paragraphs(str(with_stops))
    plain_song = chart_parser.parse_docx(str(plain))[0]
    stop_song = chart_parser.parse_docx(str(with_stops))[0]
    assert [p.text for p in plain_paras] == [p.text for p in stop_paras]
    assert plain_song.chart_content == stop_song.chart_content
    assert plain_song.warnings == stop_song.warnings == []


def test_tab_stops_and_inserted_tabs_preserve_only_actual_tabs(tmp_path: Path) -> None:
    without_stops = tmp_path / "actual-tabs.docx"
    with_stops = tmp_path / "actual-tabs-and-stops.docx"
    base = [
        _heading("Tab Stop Control \u2013 C", None, "Invented Artist"),
        _paragraph("[Verse]"), _word_tab_paragraph("C", None, "G   ", None, "D"),
        _word_tab_paragraph("invented lyric"),
    ]
    _write_docx(without_stops, base)
    defined = [*base[:2], _with_paragraph_tab_stop(base[2]),
               _with_paragraph_tab_stop(base[3])]
    _write_docx(with_stops, defined)

    plain_paras = chart_parser.load_paragraphs(str(without_stops))
    stop_paras = chart_parser.load_paragraphs(str(with_stops))
    plain_song = chart_parser.parse_docx(str(without_stops))[0]
    stop_song = chart_parser.parse_docx(str(with_stops))[0]
    assert plain_paras[2].text.count(chart_parser._TAB) == 2
    assert stop_paras[2].text == plain_paras[2].text
    assert stop_paras[3].text == plain_paras[3].text
    assert stop_song.chart_content == plain_song.chart_content
    assert stop_song.warnings == plain_song.warnings
    assert stop_song.warnings == []
    assert stop_song.chart_content == plain_song.chart_content
    assert stop_song.chart_source == plain_song.chart_source


def test_unsupported_tabbed_font_still_blocks_load(tmp_path: Path) -> None:
    document = tmp_path / "unsupported-font.docx"
    chord = _word_tab_paragraph("C", None, "G", None, "D").replace("Courier New", "Arial")
    paragraphs = [_heading("Unsupported Tab Song – C", None, "Invented Artist"),
                  _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric")]
    _write_docx(document, paragraphs)
    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
        "paragraph": 3, "span": 1,
        "reason": "unsupported_tabbed_paragraph_font_or_size",
    }]
    with pytest.raises(ValueError, match="word_tab_placement_unresolved"):
        validate_parser_diagnostics([chart_parser.song_to_dict(song)])


def test_explicit_left_tab_stop_and_indent_are_measured_from_paragraph_origin(tmp_path: Path) -> None:
    document = tmp_path / "explicit-tab.docx"
    chord = _word_tab_paragraph("C", None, "G").replace(
        "<w:p>", '<w:p><w:pPr><w:ind w:left="120"/>'
        '<w:tabs><w:tab w:val="left" w:pos="480"/></w:tabs></w:pPr>', 1)
    _write_docx(document, [
        _heading("Explicit Stop Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ])
    song = chart_parser.parse_docx(str(document))[0]
    assert song.chart_content["sections"][0]["lines"][0]["chords"] == [
        {"sym": "C", "pos": 0}, {"sym": "G", "pos": 4},
    ]
    assert song.warnings == []


def test_tab_physical_width_scales_by_font_size_before_mapping_to_lyric_columns(tmp_path: Path) -> None:
    document = tmp_path / "scaled-courier-tab.docx"
    chord = _word_tab_paragraph("C", None, "G", font_size=22)
    _write_docx(document, [
        _heading("Scaled Tab Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ])
    song = chart_parser.parse_docx(str(document))[0]
    assert song.chart_content["sections"][0]["lines"][0]["chords"] == [
        {"sym": "C", "pos": 0}, {"sym": "G", "pos": 7},
    ]
    assert song.warnings == []


def test_tab_mapping_rounds_physical_chord_start_once_for_10pt_over_8pt(tmp_path: Path) -> None:
    document = tmp_path / "single-round-courier-tab.docx"
    chord = _word_tab_paragraph("C", None, "G", font_size=20)
    lyric = _word_tab_paragraph("invented lyric", font_size=16)
    _write_docx(document, [
        _heading("Single Round Tab Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, lyric,
    ])
    song = chart_parser.parse_docx(str(document))[0]
    assert song.chart_content["sections"][0]["lines"][0]["chords"] == [
        {"sym": "C", "pos": 0}, {"sym": "G", "pos": 7},
    ]
    assert song.warnings == []


def test_character_style_font_metrics_are_resolved_for_supported_tabs(tmp_path: Path) -> None:
    document = tmp_path / "character-style-metrics.docx"
    chord = _word_tab_paragraph("C", None, "G").replace(
        '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
        '<w:sz w:val="16"/><w:b/></w:rPr>',
        '<w:rPr><w:rStyle w:val="ChordMetrics"/></w:rPr>')
    styles = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="character" w:styleId="ChordMetrics">'
              '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
              '<w:sz w:val="16"/></w:rPr></w:style></w:styles>')
    _write_docx(document, [
        _heading("Character Style Tab Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ], styles)
    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == []
    assert song.chart_content["sections"][0]["lines"][0]["chords"] == [
        {"sym": "C", "pos": 0}, {"sym": "G", "pos": 7},
    ]


@pytest.mark.parametrize("explicit_style", [False, True], ids=["default-style", "explicit-style"])
def test_default_and_explicit_paragraph_style_spacing_block_tabs(
        tmp_path: Path, explicit_style: bool) -> None:
    document = tmp_path / f"paragraph-spacing-{explicit_style}.docx"
    chord = _word_tab_paragraph("C", None, "G")
    if explicit_style:
        chord = chord.replace(
            "<w:p>", '<w:p><w:pPr><w:pStyle w:val="Normal"/></w:pPr>', 1)
    styles = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:styleId="Normal" w:default="1">'
              '<w:rPr><w:spacing w:val="20"/></w:rPr></w:style></w:styles>')
    _write_docx(document, [
        _heading("Inherited Spacing Song \u2013 C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ], styles)

    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
        "paragraph": 3, "span": 1,
        "reason": "unsupported_tabbed_paragraph_spacing",
    }]


@pytest.mark.parametrize(("style_kind", "style_xml", "paragraph_style"), [
    ("paragraph_missing", "", '<w:pPr><w:pStyle w:val="Absent"/></w:pPr>'),
    ("paragraph_cycle",
     '<w:style w:type="paragraph" w:styleId="A"><w:basedOn w:val="B"/></w:style>'
     '<w:style w:type="paragraph" w:styleId="B"><w:basedOn w:val="A"/></w:style>',
     '<w:pPr><w:pStyle w:val="A"/></w:pPr>'),
    ("paragraph_missing_parent",
     '<w:style w:type="paragraph" w:styleId="A"><w:basedOn w:val="Absent"/></w:style>',
     '<w:pPr><w:pStyle w:val="A"/></w:pPr>'),
    ("character_missing",
     '<w:style w:type="paragraph" w:styleId="Normal" w:default="1"/>', None),
    ("character_cycle",
     '<w:style w:type="paragraph" w:styleId="Normal" w:default="1"/>'
     '<w:style w:type="character" w:styleId="A"><w:basedOn w:val="B"/></w:style>'
     '<w:style w:type="character" w:styleId="B"><w:basedOn w:val="A"/></w:style>', None),
    ("character_missing_parent",
     '<w:style w:type="paragraph" w:styleId="Normal" w:default="1"/>'
     '<w:style w:type="character" w:styleId="A"><w:basedOn w:val="Absent"/></w:style>', None),
])
def test_unresolved_relevant_style_chains_block_tab_normalization(
        tmp_path: Path, style_kind: str, style_xml: str,
        paragraph_style: str | None) -> None:
    document = tmp_path / f"{style_kind}.docx"
    chord = _word_tab_paragraph("C", None, "G")
    if paragraph_style:
        chord = chord.replace("<w:p>", f"<w:p>{paragraph_style}", 1)
    if style_kind.startswith("character_"):
        run_style = "Absent" if style_kind == "character_missing" else "A"
        chord = chord.replace(
            '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
            '<w:sz w:val="16"/><w:b/>',
            f'<w:rStyle w:val="{run_style}"/>'
            '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
            '<w:sz w:val="16"/><w:b/>')
    styles = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              f'{style_xml}</w:styles>')
    _write_docx(document, [
        _heading("Broken Style Song \u2013 C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ], styles)

    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
        "paragraph": 3, "span": 1,
        "reason": ("unsupported_tabbed_paragraph_character_style"
                   if style_kind.startswith("character_")
                   else "unsupported_tabbed_paragraph_style"),
    }]


def test_valid_default_paragraph_style_inheritance_supports_tabs(tmp_path: Path) -> None:
    document = tmp_path / "default-paragraph-style.docx"
    inherited_runs = re.compile(r"<w:rPr>.*?</w:rPr>")
    chord = inherited_runs.sub("<w:rPr/>", _word_tab_paragraph("C", None, "G"))
    lyric = inherited_runs.sub("<w:rPr/>", _word_tab_paragraph("invented lyric"))
    styles = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:styleId="Normal" w:default="1">'
              '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
              '<w:sz w:val="16"/></w:rPr></w:style></w:styles>')
    _write_docx(document, [
        _heading("Default Style Song \u2013 C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, lyric,
    ], styles)

    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == []
    assert song.chart_content["sections"][0]["lines"][0]["chords"] == [
        {"sym": "C", "pos": 0}, {"sym": "G", "pos": 7},
    ]


def test_unresolved_style_on_unrelated_tab_free_text_does_not_block(tmp_path: Path) -> None:
    document = tmp_path / "tab-free-missing-style.docx"
    text = _paragraph("ordinary invented phrase")
    text = text.replace("<w:p>", '<w:p><w:pPr><w:pStyle w:val="Absent"/></w:pPr>', 1)
    _write_docx(document, [
        _heading("Tab Free Style Song \u2013 C", None, "Invented Artist"),
        _paragraph("[Verse]"), text,
    ])

    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == []
    assert song.chart_content["sections"][0]["lines"][0]["text"] == "ordinary invented phrase"


@pytest.mark.parametrize(("style_xml", "lyric_style"), [
    ('<w:style w:type="paragraph" w:styleId="Normal" w:default="1">'
     '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
     '<w:sz w:val="16"/></w:rPr></w:style>', "Absent"),
    ('<w:style w:type="paragraph" w:styleId="Normal" w:default="1">'
     '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
     '<w:sz w:val="16"/></w:rPr></w:style>'
     '<w:style w:type="paragraph" w:styleId="A"><w:basedOn w:val="B"/></w:style>'
     '<w:style w:type="paragraph" w:styleId="B"><w:basedOn w:val="A"/></w:style>', "A"),
])
def test_unresolved_lyric_style_chain_blocks_tabbed_pairing(
        tmp_path: Path, style_xml: str, lyric_style: str) -> None:
    document = tmp_path / f"unresolved-lyric-style-{lyric_style}.docx"
    lyric = _word_tab_paragraph("invented lyric")
    lyric = lyric.replace(
        "<w:p>", f'<w:p><w:pPr><w:pStyle w:val="{lyric_style}"/></w:pPr>', 1)
    styles = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              f'{style_xml}</w:styles>')
    _write_docx(document, [
        _heading("Paired Lyric Style Song \u2013 C", None, "Invented Artist"),
        _paragraph("[Verse]"), _word_tab_paragraph("C", None, "G"), lyric,
    ], styles)

    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
        "paragraph": 4, "span": 1,
        "reason": "unsupported_tabbed_paragraph_style",
    }]


@pytest.mark.parametrize(("formatting", "reason"), [
    ('<w:spacing w:val="20"/>', "unsupported_tabbed_paragraph_spacing"),
    ('<w:w w:val="90"/>', "unsupported_tabbed_paragraph_scaling"),
])
@pytest.mark.parametrize("inheritance", ["character", "paragraph"])
def test_inherited_tab_width_changes_are_blocking(
        tmp_path: Path, formatting: str, reason: str, inheritance: str) -> None:
    document = tmp_path / f"inherited-width-{inheritance}.docx"
    chord = _word_tab_paragraph("C", None, "G")
    if inheritance == "character":
        chord = chord.replace(
            '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
            '<w:sz w:val="16"/><w:b/></w:rPr>',
            '<w:rPr><w:rStyle w:val="ChordMetrics"/></w:rPr>')
        style = (f'<w:style w:type="character" w:styleId="ChordMetrics">'
                 f'<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>'
                 f'<w:sz w:val="16"/>{formatting}</w:rPr></w:style>')
        chord = chord.replace("<w:p>", '<w:p><w:pPr><w:pStyle w:val="ChordParagraph"/></w:pPr>', 1)
        paragraph_style = '<w:style w:type="paragraph" w:styleId="ChordParagraph"/>'
        extra_style = paragraph_style + style
    else:
        chord = chord.replace("<w:p>", '<w:p><w:pPr><w:pStyle w:val="ChordParagraph"/></w:pPr>', 1)
        extra_style = (f'<w:style w:type="paragraph" w:styleId="ChordParagraph">'
                       f'<w:rPr>{formatting}</w:rPr></w:style>')
    styles = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              f'{extra_style}</w:styles>')
    _write_docx(document, [
        _heading("Inherited Width Tab Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ], styles)
    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
        "paragraph": 3, "span": 1,
        "reason": reason,
    }]


def test_mixed_run_sizes_in_a_tabbed_paragraph_remain_blocking(tmp_path: Path) -> None:
    document = tmp_path / "mixed-run-sizes.docx"
    chord = _word_tab_paragraph("C", None, "G").replace(
        '<w:sz w:val="16"/>', '<w:sz w:val="20"/>', 1)
    _write_docx(document, [
        _heading("Mixed Size Tab Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ])
    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
        "paragraph": 3, "span": 1,
        "reason": "mixed_font_sizes_in_tabbed_paragraph",
    }]


def test_nested_run_containers_preserve_inline_text_tabs_and_breaks(tmp_path: Path) -> None:
    document = tmp_path / "nested-runs.docx"
    nested = ('<w:p><w:hyperlink><w:r><w:t>before</w:t></w:r></w:hyperlink>'
              '<w:ins><w:r><w:tab/></w:r></w:ins>'
              '<w:smartTag><w:r><w:br/><w:t>after</w:t></w:r></w:smartTag></w:p>')
    _write_docx(document, [nested])
    assert chart_parser.load_paragraphs(str(document))[0].text == (
        "before" + chart_parser._TAB + "\nafter"
    )


def test_tab_free_docx_keeps_existing_space_aligned_anchors(tmp_path: Path) -> None:
    document = tmp_path / "tab-free.docx"
    paragraphs = [
        _heading("Tab Free Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"),
        _paragraph("C   G"),
        _paragraph("invented lyric line"),
        _paragraph(""),
        _paragraph("invented next line"),
    ]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>")
    with zipfile.ZipFile(document, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("word/document.xml", xml.encode("utf-8"))

    song = chart_parser.parse_docx(str(document))[0]
    lines = song.chart_content["sections"][0]["lines"]
    assert lines == [
        {"type": "lyric", "text": "invented lyric line",
         "chords": [{"sym": "C", "pos": 0}, {"sym": "G", "pos": 4}],
         "confidence": "low"},
        {"type": "spacer"},
        {"type": "lyric", "text": "invented next line", "chords": [],
         "confidence": "high"},
    ]
    assert song.warnings == []


def test_plain_text_body_preserves_structures_collisions_unicode_and_trailing_spaces() -> None:
    chart = {"sections": [{"label": "Verse", "lines": [
        {"type": "progression", "chords": ["C", "G"], "repeat": 4,
         "note": "ending cue"},
        {"type": "repeat", "ref": "Chorus", "times": 2},
        {"type": "raw", "text": "e|--0-2--"},
        {"type": "lyric", "text": "🎸echo  ", "chords": [
            {"sym": "C", "pos": 0}, {"sym": "G", "pos": 0}], "confidence": "low"},
        {"type": "lyric", "text": "tail", "chords": [
            {"sym": "Am", "pos": 8}], "confidence": "low"},
    ]}]}
    source, error = chart_parser.plain_text_chart_body(chart)
    assert error is None
    assert source is not None and "[[CCM-" not in source
    assert "[Verse]\nC G 4x ending cue\n(Chorus) x2\ne|--0-2--\n{C}{G}🎸echo  \n        Am\ntail\n" == source
    assert chart["sections"][0]["lines"][3]["confidence"] == "high"

    round_trip_paragraphs = [
        chart_parser.Paragraph("Invented round trip – C\x01Invented artist", None,
                               outline_level=0),
        *(chart_parser.Paragraph(line, None) for line in source.splitlines()),
    ]
    reparsed = chart_parser.parse_songs(round_trip_paragraphs)[0]
    assert reparsed.chart_content == chart
    assert reparsed.chart_source == source


def test_unsupported_tabs_in_a_progression_are_reported_too(tmp_path: Path) -> None:
    document = tmp_path / "unsupported-progression-tab.docx"
    chord = _word_tab_paragraph("C", None, "G").replace("Courier New", "Arial")
    _write_docx(document, [
        _heading("Unsupported Progression – C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord,
    ])
    song = chart_parser.parse_docx(str(document))[0]
    assert song.chart_content["sections"][0]["lines"][0]["type"] == "progression"
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
        "paragraph": 3, "span": 1,
        "reason": "unsupported_tabbed_paragraph_font_or_size",
    }]


def test_plain_text_body_blocks_unsupported_raw_and_ambiguous_layout() -> None:
    source, error = chart_parser.plain_text_chart_body({"sections": [{"label": None, "lines": [
        {"type": "raw", "text": "freeform note that ordinary parser calls lyrics"},
    ]}]})
    assert source is None
    assert error == "raw_line_not_recognized_by_ordinary_text_parser"


def test_progression_repeat_detection_matches_ordinary_parser_contract() -> None:
    paragraphs = [
        chart_parser.Paragraph("Synthetic progression – C\x01Artist", None,
                               outline_level=0),
        chart_parser.Paragraph("[Verse]", None),
        chart_parser.Paragraph("C G 4x ending cue", None),
        chart_parser.Paragraph("D 2024 imagined cue", None),
    ]
    song = chart_parser.parse_songs(paragraphs)[0]
    lines = song.chart_content["sections"][0]["lines"]
    assert lines[0] == {
        "type": "progression", "chords": ["C", "G"],
        "repeat": 4, "note": "ending cue",
    }
    assert lines[1]["type"] == "lyric"
    assert song.warnings == []


def test_batch_report_surfaces_tab_diagnostic_without_lyrics(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    output = tmp_path / "out"
    docs.mkdir()
    _write_tab_guard_docx(docs / "invented.docx")

    assert batch_parse.run(str(docs), str(output), per_doc=False, strict=False) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    songs = json.loads((output / "songs.json").read_text(encoding="utf-8"))
    assert report["corpus_totals"]["unresolved_word_tab_placement"] == 1
    assert report["corpus_totals"]["unresolved_plain_text_body"] == 0
    assert report["flagged_songs"][0]["unresolved_placements"] == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
        "paragraph": 3, "span": 1,
        "reason": "unsupported_tabbed_paragraph_font_or_size", "doc": "invented.docx",
        "title": "Tab Guard Song",
    }]
    assert "invented lyric for tab guard" not in json.dumps(report)
    assert songs[0]["parser_diagnostics_version"] == 1
    assert songs[0]["primary_genre"] == "invented"
    assert songs[0]["primary_genre_source"] == "filename_fallback"
    # FR-109: without a cover page, the filename Genre is the primary candidate.
    assert loader._explicit_categories(songs[0]) == (["invented"], [], "invented")


def _write_tab_guard_docx(path: Path) -> None:
    paragraphs = [
        _heading("Tab Guard Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"),
        _word_tab_paragraph("C", None, "G   ", None, "D").replace("Courier New", "Arial"),
        _word_tab_paragraph("invented lyric for tab guard"),
    ]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("word/document.xml", xml.encode("utf-8"))
        docx.writestr("word/settings.xml", _DEFAULT_TAB_SETTINGS)


_DEFAULT_TAB_SETTINGS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:defaultTabStop w:val="720"/></w:settings>'
)


def test_blank_paragraphs_and_hard_breaks_keep_chart_spacing_separate(tmp_path: Path) -> None:
    document = tmp_path / "spacing.docx"
    paragraphs = [
        _heading("Spacing Song – C", None, "Test Group"),
        _paragraph("[Verse 1]"),
        _paragraph("Invented first verse line."),
        _paragraph(""),
        _paragraph("Invented second verse line."),
        _paragraph(""),
        _paragraph("[Chorus]"),
        _paragraph("Invented refrain."),
        _paragraph(""),
        _heading("Next Song – D", None, "Other Group"),
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
        _heading("Break Song – C", None, "Test Group"),
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
        _heading("Next Song – D", None, "Other Group"),
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
        {"type": "lyric", "text": "Page control before", "chords": [], "confidence": "high"},
        {"type": "lyric", "text": "page control aftercolumn control after",
         "chords": [], "confidence": "high"},
    ]


def test_ordinary_chord_columns_use_utf16_code_units() -> None:
    assert chart_parser.pair_space_aligned("C          G", "x")["chords"] == [
        {"sym": "C", "pos": 0}, {"sym": "G", "pos": 11},
    ]
    assert chart_parser.parse_inline_brace_line("{C}🎸{G}x")["chords"] == [
        {"sym": "C", "pos": 0}, {"sym": "G", "pos": 2},
    ]


APP_TABLES = (
    "artists",
    "genres",
    "vibes",
    "songs",
    "song_vibes",
    "song_genres",
    "setlists",
    "setlist_songs",
    "mutation_receipts",
    "song_enrichment_runs",
    "schema_migrations",
)


def _scratch_database_url() -> str:
    database = os.environ.get("PIPELINE_TEST_DATABASE", "")
    if not re.fullmatch(r"ccm_loader_c1_[a-z0-9_]{1,32}", database):
        raise RuntimeError(
            "Set PIPELINE_TEST_DATABASE to this run's unique "
            "ccm_loader_c1_<suffix> database after initializing it "
            "with the app migration runner."
        )
    base = os.environ.get("DATABASE_URL")
    if not base:
        raise RuntimeError("Compose DATABASE_URL is required for the scratch test.")
    parts = urlsplit(base)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


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


def _sequence_contents(conn: psycopg.Connection) -> dict[str, tuple[int, bool]]:
    state = {}
    with conn.cursor() as cur:
        cur.execute("""SELECT c.relname FROM pg_class c
                       JOIN pg_namespace n ON n.oid=c.relnamespace
                       WHERE n.nspname='public' AND c.relkind='S' ORDER BY c.relname""")
        names = [row[0] for row in cur.fetchall()]
        for name in names:
            cur.execute(sql.SQL("SELECT last_value,is_called FROM public.{}")
                        .format(sql.Identifier(name)))
            state[name] = cur.fetchone()
    return state


def _valid_insert(title: str, artist: str, source: str | None, **metadata) -> dict:
    source_text = "[Verse]\n"
    return {
        "title": title, "artist": artist,
        "source_document": source,
        "chart_source": source_text,
        "chart_content": chart_parser.parse_ordinary_chart_source(source_text),
        "parser_diagnostics_version": 1, "warnings": [], **metadata,
    }


def test_insert_only_loader_preview_skip_collisions_atomicity_and_concurrency() -> None:
    with psycopg.connect(_scratch_database_url(), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            assert cur.fetchone()[0] == os.environ["PIPELINE_TEST_DATABASE"]
        _ensure_seed_rows(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT s.id FROM songs s JOIN artists a ON a.id=s.artist_id
                           WHERE s.title=%s AND a.name=%s AND s.source_document=%s""",
                        ("T13 pytest existing song", "T13 pytest seed artist", "t13-pytest-seed.docx"))
            existing_id = cur.fetchone()[0]
            cur.execute("INSERT INTO genres(name) VALUES (%s) RETURNING id", ("T13 manual extra genre",))
            manual_genre_id = cur.fetchone()[0]
            cur.execute("INSERT INTO vibes(name) VALUES (%s) RETURNING id", ("T13 manual extra vibe",))
            manual_vibe_id = cur.fetchone()[0]
            cur.execute("""UPDATE songs SET original_key='F#m', performance_key='E',
                           preferred_key='A', alt_key='G', capo_fret=4, bpm=111,
                           release_year=1999, bb_structure='Intro-V-C',
                           chart_source='[Verse]\\nmanual edit\\n',
                           chart_content=%s
                           WHERE id=%s""",
                        (Jsonb({"sections": [{"label": "Manual", "lines": []}]}), existing_id))
            cur.execute("INSERT INTO song_genres(song_id,genre_id,is_primary) VALUES (%s,%s,false)",
                        (existing_id, manual_genre_id))
            cur.execute("INSERT INTO song_vibes(song_id,vibe_id) VALUES (%s,%s)",
                        (existing_id, manual_vibe_id))
            cur.execute("""INSERT INTO mutation_receipts
                           (operation_id,request_hash,entity_type,resource_id,response)
                           VALUES (%s,%s,'song',%s,%s)""",
                        ("00000000-0000-4000-8000-000000000001", "a" * 64,
                         str(existing_id), Jsonb({"song_id": existing_id, "synthetic": True})))
            cur.execute("""INSERT INTO song_enrichment_runs
                           (operation_id,request_hash,manifest,response)
                           VALUES (%s,%s,%s,%s)""",
                        ("00000000-0000-4000-8000-000000000002", "b" * 64,
                         Jsonb({"song_id": existing_id, "synthetic": True}),
                         Jsonb({"status": "complete", "synthetic": True})))
        conn.commit()

        # The same natural identity with altered manual/source data is an exact
        # skip. It does not rename the case-insensitive artist or touch tags.
        skip_input = _valid_insert(
            "t13 PYTEST existing song", "t13 pytest SEED artist", "t13-pytest-seed.docx",
            original_key="C", performance_key="C", preferred_key="C", capo_fret=0,
            bpm=120, release_year=2020, bb_structure="replace", primary_genre="Missing Genre",
            primary_genre_source="cover_page", vibes=["Missing Vibe"],
        )
        # The committed exact-skip path must preserve every protected app row,
        # exact song version, assignment/reference and sequence value.
        exact_skip_before = _table_contents(conn)
        exact_skip_sequences = _sequence_contents(conn)
        conn.commit()
        exact_skip = load(conn, [skip_input])
        assert exact_skip["counts"]["exact_skips"] == 1
        assert exact_skip["inserted"] == []
        assert _table_contents(conn) == exact_skip_before
        assert _sequence_contents(conn) == exact_skip_sequences
        conn.commit()
        before = _table_contents(conn)
        before_sequences = _sequence_contents(conn)
        conn.commit()
        dry_insert = _valid_insert("T13 C1 dry only", "T13 dry only artist", "dry-only.docx")
        preview_report = load(conn, [skip_input, dry_insert], dry_run=True)
        assert preview_report["counts"]["exact_skips"] == 1
        assert preview_report["counts"]["proposed_inserts"] == 1
        assert "manual edit" not in json.dumps(preview_report)
        assert "[Verse]" not in json.dumps(preview_report)
        assert _table_contents(conn) == before
        assert _sequence_contents(conn) == before_sequences
        conn.commit()

        # A new row carries only explicit metadata; managed definitions are
        # referenced by canonical IDs, and the cover-page Genre is primary (FR-109).
        with conn.cursor() as cur:
            cur.execute("INSERT INTO genres(name) VALUES (%s),(%s),(%s)",
                        ("T13 Ambiguous  Genre", "T13 Ambiguous Genre", "T13 Genre Only"))
            cur.execute("INSERT INTO vibes(name) VALUES (%s)", ("T13 Vibe Only",))
        conn.commit()
        new_input = _valid_insert(
            "T13 C1 new song", "T13 C1 new artist", "C1-source.docx",
            performance_key="D", preferred_key="G", original_key=None, alt_key=None,
            capo_fret=2, bb_structure="Verse-Chorus", primary_genre="T13 pytest seed genre",
            primary_genre_source="cover_page",
            additional_genres=["T13 Ambiguous Genre", "T13 Unknown Genre", "T13 Vibe Only"],
            vibes=["T13 Vibe Only", "T13 Genre Only"],
        )
        defs_before = {name: _table_contents(conn)[name] for name in ("artists", "genres", "vibes")}
        conn.commit()
        inserted = load(conn, [new_input])
        assert inserted["counts"]["proposed_inserts"] == 1
        assert inserted["counts"]["unresolved_categories"] == 4
        assert inserted["inserted"][0]["song_id"]
        with conn.cursor() as cur:
            new_id = inserted["inserted"][0]["song_id"]
            cur.execute("""SELECT title,original_key,performance_key,preferred_key,alt_key,
                           capo_fret,bpm,release_year,bb_structure,chart_source,chart_content
                           FROM songs WHERE id=%s""", (new_id,))
            row = cur.fetchone()
            assert row == ("T13 C1 new song", None, "D", "G", None, 2, None, None,
                           "Verse-Chorus", "[Verse]\n", {"sections": [{"label": "Verse", "lines": []}]})
            cur.execute("SELECT g.name,sg.is_primary FROM song_genres sg JOIN genres g ON g.id=sg.genre_id WHERE sg.song_id=%s ORDER BY g.name", (new_id,))
            assert cur.fetchall() == [("T13 pytest seed genre", True)]
            cur.execute("SELECT v.name FROM song_vibes sv JOIN vibes v ON v.id=sv.vibe_id WHERE sv.song_id=%s", (new_id,))
            assert cur.fetchall() == [("T13 Vibe Only",)]
        conn.commit()
        assert {name: _table_contents(conn)[name] for name in ("artists", "genres", "vibes")} != defs_before
        # Only the new artist row changed; no tag definitions were created or renamed.
        assert _table_contents(conn)["genres"] == defs_before["genres"]
        assert _table_contents(conn)["vibes"] == defs_before["vibes"]
        conn.commit()

        fallback_input = _valid_insert(
            "T13 C1 filename fallback", "T13 C1 fallback artist", "fallback.docx",
            primary_genre="T13 pytest seed genre", primary_genre_source="filename_fallback",
        )
        fallback_result = load(conn, [fallback_input])
        fallback_id = fallback_result["inserted"][0]["song_id"]
        with conn.cursor() as cur:
            # FR-109: without a cover page, the filename Genre is the primary.
            cur.execute("SELECT g.name,sg.is_primary FROM song_genres sg JOIN genres g ON g.id=sg.genre_id WHERE sg.song_id=%s", (fallback_id,))
            assert cur.fetchall() == [("T13 pytest seed genre", True)]
        conn.commit()

        unmatched_input = _valid_insert(
            "T13 C1 unmatched primary", "T13 C1 unmatched artist", "unmatched.docx",
            primary_genre="T13 Unmatched Primary", primary_genre_source="cover_page",
            additional_genres=["T13 Genre Only"],
        )
        genres_before_unmatched = _table_contents(conn)["genres"]
        conn.commit()
        unmatched_result = load(conn, [unmatched_input])
        # FR-114: an unmatched primary is reported, never created, and no primary is set.
        assert [(item["category"], item["label"]) for item in unmatched_result["unresolved_categories"]] == [
            ("Genre", "T13 Unmatched Primary")]
        unmatched_id = unmatched_result["inserted"][0]["song_id"]
        with conn.cursor() as cur:
            cur.execute("SELECT g.name,sg.is_primary FROM song_genres sg JOIN genres g ON g.id=sg.genre_id WHERE sg.song_id=%s", (unmatched_id,))
            assert cur.fetchall() == [("T13 Genre Only", False)]
        conn.commit()
        assert _table_contents(conn)["genres"] == genres_before_unmatched
        conn.commit()

        rerun_before = _table_contents(conn)
        rerun_sequences = _sequence_contents(conn)
        conn.commit()
        rerun = load(conn, [new_input])
        assert rerun["inserted"] == []
        assert rerun["counts"]["exact_skips"] == 1
        assert _table_contents(conn) == rerun_before
        assert _sequence_contents(conn) == rerun_sequences
        conn.commit()

        # Repeated input identities, multiple exact rows, and a different-source
        # row each refuse the entire operation without writes.
        duplicated = _valid_insert("T13 duplicate", "T13 dup artist", "dup.docx")
        blocked_before = _table_contents(conn)
        conn.commit()
        with pytest.raises(loader.LoadPreflightError, match="duplicate_input_identity"):
            load(conn, [duplicated, dict(duplicated)])
        with pytest.raises(loader.LoadPreflightError, match="cross_source_input_collision"):
            load(conn, [duplicated, _valid_insert("T13 duplicate", "T13 dup artist", "other.docx")])
        assert _table_contents(conn) == blocked_before
        with conn.cursor() as cur:
            cur.execute("INSERT INTO artists(name) VALUES (%s) RETURNING id", ("T13 multi artist",))
            multi_artist_id = cur.fetchone()[0]
            cur.execute("INSERT INTO songs(title,artist_id,source_document,chart_content) VALUES (%s,%s,%s,%s),(%s,%s,%s,%s)",
                        ("T13 multiple", multi_artist_id, "same.docx", Jsonb({"sections": []}),
                         "T13 multiple", multi_artist_id, "same.docx", Jsonb({"sections": []})))
            cur.execute("INSERT INTO artists(name) VALUES (%s) RETURNING id", ("T13 cross artist",))
            cross_artist_id = cur.fetchone()[0]
            cur.execute("INSERT INTO songs(title,artist_id,source_document,chart_content) VALUES (%s,%s,%s,%s)",
                        ("T13 cross", cross_artist_id, "old.docx", Jsonb({"sections": []})))
        conn.commit()
        blocked_before = _table_contents(conn)
        conn.commit()
        with pytest.raises(loader.LoadPreflightError, match="multiple_exact_database_matches"):
            load(conn, [_valid_insert("T13 multiple", "T13 multi artist", "same.docx")])
        with pytest.raises(loader.LoadPreflightError, match="same_title_artist_other_source"):
            load(conn, [_valid_insert("T13 cross", "T13 cross artist", "new.docx")])
        assert _table_contents(conn) == blocked_before

        # A DB failure after the first insert rolls back every row from the batch.
        with conn.cursor() as cur:
            cur.execute("""CREATE FUNCTION public.ccm_c1_test_fail_insert() RETURNS trigger
                           LANGUAGE plpgsql AS $$ BEGIN
                             IF NEW.title='T13 C1 fail second' THEN RAISE EXCEPTION 'test failure'; END IF;
                             RETURN NEW;
                           END $$""")
            cur.execute("CREATE TRIGGER ccm_c1_test_fail_insert BEFORE INSERT ON songs FOR EACH ROW EXECUTE FUNCTION public.ccm_c1_test_fail_insert()")
        conn.commit()
        atomic_before = _table_contents(conn)
        conn.commit()
        with pytest.raises(psycopg.Error, match="test failure"):
            load(conn, [_valid_insert("T13 C1 first", "T13 C1 atomic artist", "atomic.docx"),
                        _valid_insert("T13 C1 fail second", "T13 C1 atomic artist", "atomic.docx")])
        with conn.cursor() as cur:
            cur.execute("DROP TRIGGER ccm_c1_test_fail_insert ON songs")
            cur.execute("DROP FUNCTION public.ccm_c1_test_fail_insert()")
        conn.commit()
        assert _table_contents(conn) == atomic_before

    # Two simultaneous loader transactions serialize at the protected table
    # locks; one inserts and the other observes and skips that committed row.
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    item = _valid_insert("T13 C1 concurrent", "T13 C1 concurrent artist", "concurrent.docx")
    barrier = Barrier(2)
    def concurrent_load():
        with psycopg.connect(_scratch_database_url(), autocommit=False) as concurrent_conn:
            barrier.wait(timeout=10)
            return load(concurrent_conn, [item])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: concurrent_load(), range(2)))
    assert sum(len(result["inserted"]) for result in results) == 1
    assert sum(result["counts"]["exact_skips"] for result in results) == 1


def test_legacy_loader_artifact_fails_closed_before_database_access() -> None:
    class NoSqlConnection:
        def cursor(self):
            raise AssertionError("preflight must reject before database access")

    with pytest.raises(ValueError, match="parser_diagnostics_version"):
        load(NoSqlConnection(), [{"title": "Legacy artifact"}], dry_run=False)


def test_nonempty_primary_genre_without_provenance_rejects_before_database_access() -> None:
    class NoSqlConnection:
        def cursor(self):
            raise AssertionError("missing Genre provenance must reject before database access")

    song = _valid_insert("Older parsed song", "Old Artist", "older.docx",
                         primary_genre="Rock")
    with pytest.raises(loader.ParserValidationError) as error:
        load(NoSqlConnection(), [song])
    message = str(error.value)
    assert "older.docx / Older parsed song / Old Artist" in message
    assert "reparse with the current parser" in message


def test_cli_jsonb_failure_suppresses_chart_content_context(tmp_path: Path, monkeypatch, capsys) -> None:
    sentinel = "PRIVATE_CHART_CONTEXT_SENTINEL"
    database_url = _scratch_database_url()

    # Confirm this PostgreSQL version includes JSONB context in its raw error;
    # the CLI must still print only the SQLSTATE and fixed rollback wording.
    with psycopg.connect(database_url, autocommit=False) as probe:
        with pytest.raises(psycopg.Error) as raw_error:
            with probe.transaction():
                with probe.cursor() as cur:
                    cur.execute("SELECT %s::jsonb", (Jsonb({"chart": sentinel + "\x00"}),))
    assert sentinel in str(raw_error.value)

    artifact = _valid_insert("T13 SQL error privacy song", "T13 SQL error privacy artist",
                             "sql-error-privacy.docx")
    artifact["chart_source"] = None
    artifact["chart_content"] = {"sections": [], "chart": sentinel + "\x00"}
    path = tmp_path / "sql-error-songs.json"
    path.write_text(json.dumps([artifact]), encoding="utf-8")
    # Ordinary chart text cannot carry the NUL that makes PostgreSQL reject the
    # JSONB, so skip the source/content preflight to reach the database error.
    monkeypatch.setattr(loader, "validate_parser_diagnostics", lambda songs: None)
    monkeypatch.setattr(sys, "argv", ["load_songs.py", str(path), "--database-url", database_url])

    assert loader.main() == 1
    captured = capsys.readouterr()
    error = captured.err
    assert "SQLSTATE 22P05" in error, f"stdout={captured.out!r}; stderr={error!r}"
    assert "details suppressed" in error
    assert sentinel not in error


@pytest.mark.parametrize("source", ["cover_page", "filename_fallback"])
def test_primary_genre_provenance_is_explicit(source: str) -> None:
    song = _valid_insert("Explicit genre origin", "Origin Artist", "origin.docx",
                         primary_genre="Rock", primary_genre_source=source)
    prepared, failures = loader._validate_batch([song])
    assert not failures
    # FR-109: either provenance supplies the primary Genre.
    assert prepared[0]["genres"] == ["Rock"]
    assert prepared[0]["primary_genre"] == "Rock"


def test_ambiguous_artist_resolution_is_explicit() -> None:
    class MultipleArtists:
        def execute(self, query, params):
            assert "lower(name)=lower(%s)" in query
            assert params == ("Ambiguous Artist",)

        def fetchall(self):
            return [(1, "Ambiguous Artist"), (2, "ambiguous artist")]

    result = loader._resolve_artist(MultipleArtists(), " Ambiguous Artist ")
    assert result == {"status": "ambiguous", "matches": [
        {"id": 1, "name": "Ambiguous Artist"},
        {"id": 2, "name": "ambiguous artist"},
    ]}


@pytest.mark.parametrize("bad_version", [None, True, 1.0, "1", 0, 2, -1])
def test_malformed_or_unsupported_diagnostic_versions_fail_closed(bad_version) -> None:
    with pytest.raises(ValueError, match="parser_diagnostics_version"):
        validate_parser_diagnostics([{
            "title": "Synthetic malformed version",
            "parser_diagnostics_version": bad_version,
            "warnings": [],
        }])


def test_version_one_low_confidence_warning_is_nonblocking() -> None:
    validate_parser_diagnostics([{
        **_ordinary_source_artifact(),
        "title": "Synthetic ordinary warning",
        "warnings": [{"code": "space_alignment_low_confidence"}],
    }])


def _ordinary_source_artifact() -> dict:
    source = "[Verse]\nC   G\ninvented phrase\n"
    return {
        "title": "Synthetic source consistency",
        "chart_source": source,
        "chart_content": chart_parser.parse_ordinary_chart_source(source),
        "parser_diagnostics_version": 1,
        "warnings": [],
    }


@pytest.mark.parametrize("mismatch", ["anchor", "anchor_json_type", "line_type", "confidence"])
def test_ordinary_source_content_mismatches_fail_preflight(mismatch) -> None:
    artifact = _ordinary_source_artifact()
    line = artifact["chart_content"]["sections"][0]["lines"][0]
    if mismatch == "anchor":
        line["chords"][1]["pos"] += 1
    elif mismatch == "anchor_json_type":
        line["chords"][0]["pos"] = False
    elif mismatch == "line_type":
        line["type"] = "progression"
    else:
        line["confidence"] = "high"
    with pytest.raises(ValueError, match="chart_source_content_mismatch"):
        validate_parser_diagnostics([artifact])


def test_direct_loader_rejects_source_content_mismatch_before_sql() -> None:
    class NoSqlConnection:
        def cursor(self):
            raise AssertionError("source/content validation must precede SQL")

    artifact = _ordinary_source_artifact()
    artifact["chart_content"]["sections"][0]["lines"][0]["confidence"] = "high"
    for dry_run in (False, True):
        with pytest.raises(ValueError, match="chart_source_content_mismatch"):
            load(NoSqlConnection(), [artifact], dry_run=dry_run)


def test_ordinary_source_content_exact_match_is_accepted() -> None:
    validate_parser_diagnostics([_ordinary_source_artifact()])


@pytest.mark.parametrize(("chart_source", "problem"), [
    (None, "chart_source_missing"),
    ("[[CCM-CHART:2]]\r\nOld\\n", "chart_source_content_mismatch"),
], ids=["null", "ccm"])
def test_missing_or_generated_chart_source_is_rejected(chart_source, problem) -> None:
    legacy_chart = {"sections": [{"label": "Old", "lines": [
        {"type": "lyric", "text": "old content", "chords": [],
         "confidence": "high"},
    ]}]}
    with pytest.raises(ValueError, match=problem):
        validate_parser_diagnostics([{
            "title": "Legacy source", "chart_source": chart_source,
            "chart_content": legacy_chart, "parser_diagnostics_version": 1,
            "warnings": [],
        }])


@pytest.mark.parametrize("dry_run", [False, True], ids=["normal", "dry-run"])
def test_cli_source_content_mismatch_rejected_before_database_connection(
        dry_run: bool, tmp_path: Path, monkeypatch, capsys) -> None:
    artifact = _ordinary_source_artifact()
    artifact["chart_content"]["sections"][0]["lines"][0]["confidence"] = "high"
    path = tmp_path / "mismatched-songs.json"
    path.write_text(json.dumps([artifact]), encoding="utf-8")

    def fail_if_connected(*args, **kwargs):
        raise AssertionError("source/content mismatch must fail before connection")

    argv = ["load_songs.py", str(path), "--database-url", "postgresql://unused/unused"]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(loader.psycopg, "connect", fail_if_connected)
    assert loader.main() == 1
    assert "chart_source_content_mismatch" in capsys.readouterr().err


@pytest.mark.parametrize("warnings_field", [
    pytest.param({}, id="omitted"),
    pytest.param({"warnings": None}, id="null"),
    pytest.param({"warnings": "none"}, id="string"),
    pytest.param({"warnings": {}}, id="object"),
])
def test_direct_loader_rejects_missing_or_malformed_warnings_before_sql(warnings_field) -> None:
    class NoSqlConnection:
        def cursor(self):
            raise AssertionError("warnings preflight must run before SQL")

    artifact = {"title": "Synthetic warnings contract",
                "parser_diagnostics_version": 1, **warnings_field}
    with pytest.raises(ValueError, match="malformed parser warnings"):
        load(NoSqlConnection(), [artifact], dry_run=False)


@pytest.mark.parametrize("artifact", [
    {"title": "Synthetic legacy", "warnings": []},
    {"title": "Synthetic unresolved", "parser_diagnostics_version": 1,
     "warnings": [{"code": "word_tab_placement_unresolved", "section": 1,
                   "line": 2, "reason": "word_tab_semantics_not_modeled"}]},
    {"title": "Synthetic warnings omitted", "parser_diagnostics_version": 1},
    {"title": "Synthetic warnings null", "parser_diagnostics_version": 1,
     "warnings": None},
    {"title": "Synthetic warnings string", "parser_diagnostics_version": 1,
     "warnings": "none"},
    {"title": "Synthetic warnings object", "parser_diagnostics_version": 1,
     "warnings": {}},
])
@pytest.mark.parametrize("dry_run", [False, True], ids=["normal", "dry-run"])
def test_cli_preflight_rejects_before_database_connection(
        artifact, dry_run: bool, tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "synthetic-songs.json"
    path.write_text(json.dumps([artifact]), encoding="utf-8")

    def fail_if_connected(*args, **kwargs):
        raise AssertionError("preflight should reject before psycopg.connect")

    argv = ["load_songs.py", str(path), "--database-url", "postgresql://unused/unused"]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(loader.psycopg, "connect", fail_if_connected)
    assert loader.main() == 1
    error = capsys.readouterr().err
    assert error
    if dry_run:
        assert "database connection: not opened" in error
