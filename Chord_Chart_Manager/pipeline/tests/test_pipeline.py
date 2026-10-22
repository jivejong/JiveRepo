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
        if styles_xml is not None:
            docx.writestr("word/styles.xml", styles_xml.encode("utf-8"))


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
        _paragraph("Synthetic Pipe Song – G", None, "Example Band"),
        _paragraph("[Verse]"),
        _paragraph("G  D | C G | Em F | G"),
        _paragraph("Invented lyric line."),
    ])

    songs = chart_parser.parse_docx(str(document))

    assert len(songs) == 1
    song = songs[0]
    assert song.chart_content["sections"][0]["lines"][:2] == [
        {"type": "raw", "text": "G  D | C G | Em F | G"},
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


def test_supported_mixed_tabs_normalize_to_plain_text_without_blocking(tmp_path: Path) -> None:
    document = tmp_path / "mixed-tabs.docx"
    paragraphs = [
        _paragraph("Tab Guard Song – C", None, "Invented Artist"),
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
        _paragraph("Tab Stop Control \u2013 C", None, "Invented Artist"),
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
        _paragraph("Tab Stop Control \u2013 C", None, "Invented Artist"),
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
    paragraphs = [_paragraph("Unsupported Tab Song – C", None, "Invented Artist"),
                  _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric")]
    _write_docx(document, paragraphs)
    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
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
        _paragraph("Explicit Stop Song – C", None, "Invented Artist"),
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
        _paragraph("Scaled Tab Song – C", None, "Invented Artist"),
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
        _paragraph("Single Round Tab Song – C", None, "Invented Artist"),
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
        _paragraph("Character Style Tab Song – C", None, "Invented Artist"),
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
        _paragraph("Inherited Spacing Song \u2013 C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ], styles)

    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
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
        _paragraph("Broken Style Song \u2013 C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ], styles)

    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
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
        _paragraph("Default Style Song \u2013 C", None, "Invented Artist"),
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
        _paragraph("Tab Free Style Song \u2013 C", None, "Invented Artist"),
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
        _paragraph("Paired Lyric Style Song \u2013 C", None, "Invented Artist"),
        _paragraph("[Verse]"), _word_tab_paragraph("C", None, "G"), lyric,
    ], styles)

    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
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
        _paragraph("Inherited Width Tab Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ], styles)
    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
        "reason": reason,
    }]


def test_mixed_run_sizes_in_a_tabbed_paragraph_remain_blocking(tmp_path: Path) -> None:
    document = tmp_path / "mixed-run-sizes.docx"
    chord = _word_tab_paragraph("C", None, "G").replace(
        '<w:sz w:val="16"/>', '<w:sz w:val="20"/>', 1)
    _write_docx(document, [
        _paragraph("Mixed Size Tab Song – C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord, _word_tab_paragraph("invented lyric"),
    ])
    song = chart_parser.parse_docx(str(document))[0]
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
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
        _paragraph("Tab Free Song – C", None, "Invented Artist"),
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
        chart_parser.Paragraph("Invented round trip – C\x01Invented artist", None, False),
        *(chart_parser.Paragraph(line, None, False) for line in source.splitlines()),
    ]
    reparsed = chart_parser.parse_songs(round_trip_paragraphs)[0]
    assert reparsed.chart_content == chart
    assert reparsed.chart_source == source


def test_unsupported_tabs_in_a_progression_are_reported_too(tmp_path: Path) -> None:
    document = tmp_path / "unsupported-progression-tab.docx"
    chord = _word_tab_paragraph("C", None, "G").replace("Courier New", "Arial")
    _write_docx(document, [
        _paragraph("Unsupported Progression – C", None, "Invented Artist"),
        _paragraph("[Verse]"), chord,
    ])
    song = chart_parser.parse_docx(str(document))[0]
    assert song.chart_content["sections"][0]["lines"][0]["type"] == "progression"
    assert song.warnings == [{
        "code": "word_tab_placement_unresolved", "section": 1, "line": 1,
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
        chart_parser.Paragraph("Synthetic progression – C\x01Artist", None, False),
        chart_parser.Paragraph("[Verse]", None, False),
        chart_parser.Paragraph("C G 4x ending cue", None, False),
        chart_parser.Paragraph("D 2024 imagined cue", None, False),
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
        "reason": "unsupported_tabbed_paragraph_font_or_size", "doc": "invented.docx",
        "title": "Tab Guard Song",
    }]
    assert "invented lyric for tab guard" not in json.dumps(report)
    assert songs[0]["parser_diagnostics_version"] == 1
    assert songs[0]["primary_genre"] == "invented"
    assert songs[0]["primary_genre_source"] == "filename_fallback"
    assert loader._explicit_categories(songs[0])[0] == []


def _write_tab_guard_docx(path: Path) -> None:
    paragraphs = [
        _paragraph("Tab Guard Song – C", None, "Invented Artist"),
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
    imported_v2 = chart_parser.parse_docx(str(v2_path))[0]
    expected_plain = json.loads(json.dumps(chart, ensure_ascii=False))
    expected_plain["sections"][0]["lines"][0]["confidence"] = "low"
    assert imported_v2.chart_content == expected_plain
    assert imported_v2.chart_source is None
    assert imported_v2.warnings == [{
        "code": "plain_text_body_unresolved", "section": "?", "line": "?",
        "reason": "raw_line_not_recognized_by_ordinary_text_parser",
    }]


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
        # referenced by canonical IDs and all assignments stay non-primary.
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
            assert cur.fetchall() == [("T13 pytest seed genre", False)]
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
            cur.execute("SELECT count(*) FROM song_genres WHERE song_id=%s", (fallback_id,))
            assert cur.fetchone()[0] == 0
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


def test_concurrent_app_definition_create_uses_loader_name_key_and_resolution() -> None:
    """A create between catalog reads must serialize and expose normalized ambiguity."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    database_url = _scratch_database_url()
    suffix = uuid.uuid4().hex
    label = f"T13 concurrent normalized genre {suffix}"
    item = _valid_insert(f"T13 concurrent catalog song {suffix}", f"T13 concurrent catalog artist {suffix}",
                         f"concurrent-catalog-{suffix}.docx", primary_genre=label,
                         primary_genre_source="cover_page")
    with psycopg.connect(database_url, autocommit=False) as seed:
        with seed.cursor() as cur:
            cur.execute("INSERT INTO genres(name) VALUES (%s)", (label.replace(" ", "  ", 1),))
        seed.commit()

    definition_inserted = Event()
    loader_started = Event()
    loader_lock_attempted = Event()
    release_definition = Event()
    app_pid: dict[str, int] = {}
    loader_pid: dict[str, int] = {}
    attempted_lock_params: list[tuple[str, str]] = []

    def app_create_definition():
        with psycopg.connect(database_url, autocommit=False) as app_conn:
            with app_conn.transaction():
                with app_conn.cursor() as cur:
                    # Exact lock namespace/key and ordering used by
                    # tagDefinitionNames.lockTagDefinitionNames + createTagDefinition.
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                        ("ccm-tag-definition:Genre", label.lower()),
                    )
                    cur.execute("SELECT pg_backend_pid()")
                    app_pid["pid"] = cur.fetchone()[0]
                    cur.execute("INSERT INTO genres(name) VALUES (%s)", (label,))
                definition_inserted.set()
                if not release_definition.wait(timeout=30):
                    raise TimeoutError("test coordinator did not release app definition transaction")

    def concurrent_loader():
        with psycopg.connect(database_url, autocommit=False) as loader_conn:
            prepared, failures = loader._validate_batch([item])
            assert not failures and prepared[0]["genres"] == [label]
            with loader_conn.cursor() as cur:
                cur.execute("SELECT pg_backend_pid()")
                loader_pid["pid"] = cur.fetchone()[0]
            loader_conn.commit()
            loader_started.set()
            with loader_conn.transaction():
                with loader_conn.cursor() as raw_cur:
                    class CoordinatedCursor:
                        def execute(self, query, params=None):
                            if "pg_advisory_xact_lock" in query:
                                attempted_lock_params.append(tuple(params))
                                loader_lock_attempted.set()
                            return raw_cur.execute(query, params)

                        def __getattr__(self, name):
                            return getattr(raw_cur, name)

                    cur = CoordinatedCursor()
                    cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                    return loader._locked_apply_plan(cur, prepared, failures)

    with ThreadPoolExecutor(max_workers=2) as workers:
        app_future = workers.submit(app_create_definition)
        assert definition_inserted.wait(timeout=10)
        loader_future = workers.submit(concurrent_loader)
        assert loader_started.wait(timeout=10)
        try:
            assert loader_lock_attempted.wait(timeout=10), "loader never attempted its definition-name lock"
            deadline = time.monotonic() + 10
            observed_wait = False
            last_activity = None
            with psycopg.connect(database_url, autocommit=True) as observer:
                with observer.cursor() as cur:
                    cur.execute("SET statement_timeout = '1s'")
                    while time.monotonic() < deadline:
                        cur.execute(
                            "SELECT wait_event_type, wait_event, pg_blocking_pids(pid) "
                            "FROM pg_stat_activity WHERE pid=%s",
                            (loader_pid["pid"],),
                        )
                        last_activity = cur.fetchone()
                        if (last_activity and last_activity[0] == "Lock" and last_activity[1] == "advisory"
                                and app_pid["pid"] in last_activity[2]):
                            observed_wait = True
                            break
                        if loader_future.done():
                            break
            assert observed_wait, (
                "loader did not wait on the app create's normalized-name advisory lock; "
                f"activity={last_activity!r}; loader_done={loader_future.done()}; "
                f"keys={attempted_lock_params!r}"
            )
        finally:
            release_definition.set()
        app_future.result(timeout=10)
        result = loader_future.result(timeout=10)

    assert attempted_lock_params == [("ccm-tag-definition:Genre", label.lower())]
    assert observed_wait, (
        "loader did not wait on the app create's normalized-name advisory lock; "
        f"last activity={last_activity!r}"
    )

    assert len(result["proposed_inserts"]) == 1
    assert len(result["unresolved_categories"]) == 1
    assert result["unresolved_categories"][0]["reason"] == "ambiguous"
    with psycopg.connect(database_url, autocommit=True) as verify:
        with verify.cursor() as cur:
            cur.execute("SELECT count(*) FROM song_genres sg JOIN songs s ON s.id=sg.song_id "
                        "WHERE s.title=%s", (item["title"],))
            assert cur.fetchone()[0] == 0


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
    assert prepared[0]["genres"] == (["Rock"] if source == "cover_page" else [])


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
        "title": "Synthetic ordinary warning",
        "parser_diagnostics_version": 1,
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


def test_ordinary_source_content_exact_match_and_legacy_ccm_are_accepted() -> None:
    artifact = _ordinary_source_artifact()
    validate_parser_diagnostics([artifact])

    legacy_chart = {"sections": [{"label": "Old", "lines": [
        {"type": "lyric", "text": "old content", "chords": [],
         "confidence": "high"},
    ]}]}
    validate_parser_diagnostics([{
        "title": "Legacy CCM source", "chart_source": "[[CCM-CHART:2]]\r\nOld\\n",
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
