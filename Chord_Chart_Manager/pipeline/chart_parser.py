"""
chart_parser.py — Parse chord-chart .docx files into structured song JSON.

Design goals (from the schema + normalization decisions):
  * One doc = one genre binder. An optional cover page names the genre;
    it becomes each song's PRIMARY genre.
  * Only effective Word outline headings (levels 0–8) start songs.
    Heading text supplies the title and any available metadata.
  * Three source chord notations are normalized into TWO line types:
        - "lyric"       : text + chords with character positions
        - "progression" : bare chord sequence, optional repeat count
    Anything unparseable (tab tablature, freeform notes) is preserved
    verbatim as a "raw" line so nothing is ever lost.
  * Space-aligned chord positions are COMPUTED (column -> nearest char),
    so those lines carry confidence="low" for later human review.

Output per song is the `chart_content` JSONB shape the schema expects,
plus the flat metadata fields (title, artist, keys, capo, bpm, bb_structure).

Pure standard library. Reads the docx zip directly; no python-docx needed.
"""

from __future__ import annotations

import re
import json
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote_to_bytes
from dataclasses import dataclass, field, asdict
from typing import Optional


# --------------------------------------------------------------------------
# 1. DOCX -> paragraph stream
# --------------------------------------------------------------------------
# We read word/document.xml and turn each <w:p> into a Paragraph carrying:
#   - text     : visible text, with <w:tab/> expanded to a sentinel we can
#                find again, and runs concatenated in order
#   - style    : pStyle val (Heading1, NoSpacing, ...) or None
# We keep tabs as a private sentinel char first, then expand to spaces for
# column math — but only AFTER we've used tab structure to split title fields.

_TAB = "\x01"       # private sentinel standing in for an inserted <w:tab/>
_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

_W = "{" + _NS["w"] + "}"
_COURIER_NEW_CELL_EM = 1229 / 2048
_DEFAULT_TAB_TWIPS = 720


@dataclass
class Paragraph:
    text: str                 # tabs as _TAB sentinel, runs joined
    style: Optional[str]
    normalized_text: Optional[str] = None
    tab_layout_error: Optional[str] = None
    cell_twips: Optional[float] = None
    physical_positions: Optional[dict[int, float]] = None
    outline_level: Optional[int] = None
    page_break_before: bool = False
    explicit_page_break_offsets: tuple[int, ...] = ()
    rendered_page_break_count: int = 0
    source_index: Optional[int] = None
    source_span_index: Optional[int] = None

    @property
    def text_spaces(self) -> str:
        """Visible text with inserted tabs expanded under the bounded DOCX rule."""
        return self.normalized_text if self.normalized_text is not None else self.text.replace(_TAB, " ")

    @property
    def stripped(self) -> str:
        return self.text_spaces.strip()


def _column_length(text: str) -> int:
    """Chart columns count UTF-16 code units, matching JavaScript string indices."""
    return len(text.encode("utf-16-le")) // 2


def _validated_style_chain(style_id: Optional[str], styles: dict[str, ET.Element],
                           expected_type: str) -> tuple[list[ET.Element], Optional[str]]:
    """Resolve a relevant style chain, reporting broken references and cycles."""
    chain = []
    seen = set()
    current = style_id
    while current:
        if current in seen:
            return list(reversed(chain)), "cyclic"
        seen.add(current)
        style = styles.get(current)
        if style is None or style.attrib.get(_W + "type") != expected_type:
            return list(reversed(chain)), "missing_or_wrong_type"
        chain.append(style)
        based_on = style.find(_W + "basedOn")
        if based_on is not None:
            current = based_on.attrib.get(_W + "val")
            if not current:
                return list(reversed(chain)), "missing_or_wrong_type"
        else:
            current = None
    return list(reversed(chain)), None


def _paragraph_style_chain(paragraph: ET.Element,
                           styles: dict[str, ET.Element]
                           ) -> tuple[list[ET.Element], Optional[str]]:
    p_style = paragraph.find(_W + "pPr/" + _W + "pStyle")
    if p_style is not None:
        style_id = p_style.attrib.get(_W + "val")
        if not style_id:
            return [], "missing_or_wrong_type"
    else:
        defaults = [
            style for style in styles.values()
            if style.attrib.get(_W + "type") == "paragraph"
            and style.attrib.get(_W + "default", "0").lower() in ("1", "true", "on")
        ]
        if len(defaults) > 1:
            return [], "missing_or_wrong_type"
        if not defaults:
            return [], None
        style_id = defaults[0].attrib.get(_W + "styleId")
    return _validated_style_chain(style_id, styles, "paragraph")


def _tab_properties(paragraph: ET.Element, style_chain: list[ET.Element]
                    ) -> tuple[int, list[int], Optional[str]]:
    ppr_chain = [style.find(_W + "pPr") for style in style_chain]
    direct = paragraph.find(_W + "pPr")
    ppr_chain.append(direct)
    indent = 0
    stops: dict[int, str] = {}
    error = None
    for ppr in ppr_chain:
        if ppr is None:
            continue
        ind = ppr.find(_W + "ind")
        if ind is not None:
            raw_left = ind.attrib.get(_W + "left", ind.attrib.get(_W + "start"))
            if raw_left is not None:
                indent = int(raw_left)
            for attr in ("right", "hanging", "firstLine", "end"):
                if int(ind.attrib.get(_W + attr, "0")) != 0:
                    error = "unsupported_paragraph_indentation"
        tabs = ppr.find(_W + "tabs")
        if tabs is not None:
            for tab in tabs.findall(_W + "tab"):
                pos = int(tab.attrib.get(_W + "pos", "0"))
                val = tab.attrib.get(_W + "val", "left")
                if val == "clear":
                    error = "unsupported_cleared_tab_stop"
                    stops.pop(pos, None)
                else:
                    stops[pos] = val
    for val in stops.values():
        if val != "left":
            error = "unsupported_non_left_tab_stop"
    if indent < 0:
        error = "unsupported_negative_indentation"
    return indent, sorted(pos for pos, val in stops.items() if val == "left"), error


def _effective_run_sources(run: ET.Element, style_chain: list[ET.Element],
                           doc_default: ET.Element | None,
                           styles: dict[str, ET.Element]) -> tuple[list[ET.Element | None], bool]:
    run_style = run.find(_W + "rPr/" + _W + "rStyle")
    char_style_id = run_style.attrib.get(_W + "val") if run_style is not None else None
    char_chain, char_chain_error = _validated_style_chain(
        char_style_id, styles, "character")
    invalid_character_style = bool(run_style is not None and (
        not char_style_id or char_chain_error or not char_chain
        or char_chain[-1].attrib.get(_W + "type") != "character"))
    sources = [doc_default]
    sources.extend(style.find(_W + "rPr") for style in style_chain)
    if not invalid_character_style:
        sources.extend(style.find(_W + "rPr") for style in char_chain)
    sources.append(run.find(_W + "rPr"))
    return sources, invalid_character_style


def _effective_font_size(sources: list[ET.Element | None]
                         ) -> tuple[Optional[str], Optional[int]]:
    font = None
    size = None
    for rpr in sources:
        if rpr is None:
            continue
        fonts = rpr.find(_W + "rFonts")
        if fonts is not None:
            font = fonts.attrib.get(_W + "ascii", font)
            font = fonts.attrib.get(_W + "hAnsi", font)
        sz = rpr.find(_W + "sz")
        if sz is not None and _W + "val" in sz.attrib:
            size = int(sz.attrib[_W + "val"])
    return font, size


def _paragraph_cell_width(paragraph: ET.Element, style_chain: list[ET.Element],
                          doc_default_rpr: ET.Element | None,
                          styles: dict[str, ET.Element]
                          ) -> tuple[Optional[float], Optional[str]]:
    metrics = set()
    error = None
    for run in paragraph.iter(_W + "r"):
        run_text = "".join(node.text or "" for node in run.iter(_W + "t"))
        has_tab = any(node.tag == _W + "tab" for node in run.iter())
        if not run_text and not has_tab:
            continue
        sources, invalid_character_style = _effective_run_sources(
            run, style_chain, doc_default_rpr, styles)
        font, size = _effective_font_size(sources)
        if invalid_character_style:
            error = "unsupported_tabbed_paragraph_character_style"
        if font != "Courier New" or not isinstance(size, int) or size <= 0:
            error = "unsupported_tabbed_paragraph_font_or_size"
        else:
            metrics.add(size)
        spacing = width = None
        for rpr in sources:
            if rpr is None:
                continue
            candidate = rpr.find(_W + "spacing")
            if candidate is not None:
                spacing = candidate
            candidate = rpr.find(_W + "w")
            if candidate is not None:
                width = candidate
        if spacing is not None and int(spacing.attrib.get(_W + "val", "0")) != 0:
            error = "unsupported_tabbed_paragraph_spacing"
        if width is not None and int(width.attrib.get(_W + "val", "100")) != 100:
            error = "unsupported_tabbed_paragraph_scaling"
        if any(ord(char) > 127 for char in run_text):
            error = "unsupported_non_ascii_tabbed_paragraph"
    if len(metrics) > 1:
        error = "mixed_font_sizes_in_tabbed_paragraph"
    if error or not metrics:
        return None, error
    # w:sz is in half-points. 1 pt = 20 twips.
    return (next(iter(metrics)) * 10 * _COURIER_NEW_CELL_EM), None


def _expand_docx_tabs(text: str, paragraph: ET.Element, styles: dict[str, ET.Element],
                      default_tab: int, doc_default_rpr: ET.Element | None
                      ) -> tuple[str, Optional[str], Optional[float], Optional[dict[int, float]]]:
    if _TAB not in text:
        return text, None, None, None
    chain, style_error = _paragraph_style_chain(paragraph, styles)
    indent, explicit_stops, error = _tab_properties(paragraph, chain)
    if style_error:
        error = error or "unsupported_tabbed_paragraph_style"
    cell_twips, metrics_error = _paragraph_cell_width(
        paragraph, chain, doc_default_rpr, styles)
    error = error or metrics_error
    if error:
        return text.replace(_TAB, " "), error, None, None

    # Courier New cell width is 1229/2048 em. Convert the effective run size
    # to twips, then advance each tab to the next explicit
    # left stop or 720-twip default stop. Convert a physical stop to the nearest
    # text column using round-half-up; this makes half-cell cases deterministic.
    out: list[str] = []
    physical_positions: dict[int, float] = {}
    x = float(indent)
    column = 0
    global_column = 0
    for piece_index, piece in enumerate(text.split(_TAB)):
        out.append(piece)
        for char in piece:
            physical_positions[global_column] = x - indent
            if char == "\n":
                x = float(indent)
                column = 0
                global_column += 1
            else:
                x += cell_twips
                width = _column_length(char)
                column += width
                global_column += width
        if piece_index == text.count(_TAB):
            continue
        default_next = (int(x // default_tab) + 1) * default_tab if default_tab > 0 else None
        candidates = [stop for stop in explicit_stops if stop > x]
        if default_next is not None:
            candidates.append(default_next)
        if not candidates:
            return text.replace(_TAB, " "), "no_effective_tab_stop", None, None
        next_x = min(candidates)
        column_at_stop = int(((next_x - indent) / cell_twips) + 0.5)
        spaces = max(1, column_at_stop - column)
        out.append(" " * spaces)
        column += spaces
        global_column += spaces
        x = float(next_x)
    return "".join(out), None, cell_twips, physical_positions


def load_paragraphs(docx_path: str) -> list[Paragraph]:
    with zipfile.ZipFile(docx_path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
        styles_root = ET.fromstring(z.read("word/styles.xml")) if "word/styles.xml" in z.namelist() else None
        settings_root = ET.fromstring(z.read("word/settings.xml")) if "word/settings.xml" in z.namelist() else None
    styles = {}
    if styles_root is not None:
        styles = {style.attrib.get(_W + "styleId"): style
                  for style in styles_root.findall(_W + "style")
                  if style.attrib.get(_W + "styleId")}
    default_tab = _DEFAULT_TAB_TWIPS
    if settings_root is not None:
        default = settings_root.find(".//" + _W + "defaultTabStop")
        if default is not None:
            default_tab = int(default.attrib.get(_W + "val", default_tab))
    doc_default_rpr = None
    if styles_root is not None:
        doc_default_rpr = styles_root.find(_W + "docDefaults/" + _W + "rPrDefault/" + _W + "rPr")
    out: list[Paragraph] = []

    def paragraph_structure(paragraph: ET.Element, style: Optional[str]) -> tuple[Optional[int], bool]:
        """Resolve paragraph properties by nearest direct/style override."""
        direct = paragraph.find(_W + "pPr")
        level: Optional[int] = None
        page_before: Optional[bool] = None

        def read_outline(ppr: Optional[ET.Element]) -> Optional[int]:
            item = ppr.find(_W + "outlineLvl") if ppr is not None else None
            if item is None:
                return None
            try:
                return int(item.attrib.get(_W + "val", ""))
            except ValueError:
                return None

        def read_on_off(ppr: Optional[ET.Element], name: str) -> Optional[bool]:
            item = ppr.find(_W + name) if ppr is not None else None
            if item is None:
                return None
            value = item.attrib.get(_W + "val", "1").strip().lower()
            return value not in {"0", "false", "off", "no"}

        level = read_outline(direct)
        page_before = read_on_off(direct, "pageBreakBefore")
        seen = set()
        current = style
        while current and current not in seen:
            seen.add(current)
            definition = styles.get(current)
            if definition is None:
                break
            inherited = definition.find(_W + "pPr")
            if level is None:
                level = read_outline(inherited)
            if page_before is None:
                page_before = read_on_off(inherited, "pageBreakBefore")
            based_on = definition.find(_W + "basedOn")
            current = based_on.attrib.get(_W + "val") if based_on is not None else None
        return level, bool(page_before)

    for source_index, paragraph in enumerate(root.iter(_W + "p"), 1):
        p_style = paragraph.find(_W + "pPr/" + _W + "pStyle")
        style = p_style.attrib.get(_W + "val") if p_style is not None else None
        paragraph_styles, _ = _paragraph_style_chain(paragraph, styles)
        effective_style_ids = [item.attrib.get(_W + "styleId")
                               for item in paragraph_styles]
        effective_style = style or (effective_style_ids[-1] if effective_style_ids else None)
        outline_level, page_break_before = paragraph_structure(paragraph, effective_style)
        parts: list[str] = []
        page_break_offsets: list[int] = []

        def append_inline(node: ET.Element, in_run: bool = False) -> None:
            # Walk nested run containers (hyperlink, insertion, smartTag, etc.)
            # in document order, but never interpret pPr/rPr layout metadata.
            if node.tag == _W + "r":
                in_run = True
            if in_run and node.tag == _W + "rPr":
                return
            if in_run and node.tag == _W + "t":
                parts.append(node.text or "")
                return
            if in_run and node.tag == _W + "tab":
                parts.append(_TAB)
                return
            if in_run and node.tag == _W + "br":
                kind = node.attrib.get(_W + "type")
                if kind in (None, "textWrapping"):
                    parts.append("\n")
                elif kind == "page":
                    page_break_offsets.append(sum(len(part) for part in parts))
                    parts.append("\n")
                return
            if in_run and node.tag == _W + "cr":
                parts.append("\n")
                return
            for child in node:
                append_inline(child, in_run)

        append_inline(paragraph)
        text = "".join(parts)
        rendered_breaks = sum(1 for node in paragraph.iter(_W + "lastRenderedPageBreak"))
        style_chain, style_error = _paragraph_style_chain(paragraph, styles)
        cell_twips, _ = _paragraph_cell_width(
            paragraph, style_chain, doc_default_rpr, styles)
        normalized, layout_error, tab_cell_twips, physical_positions = _expand_docx_tabs(
            text, paragraph, styles, default_tab, doc_default_rpr)
        if _TAB not in text and style_error:
            # Pairing metadata only: an unrelated tab-free paragraph stays usable.
            layout_error = "unsupported_tabbed_paragraph_style"
        out.append(Paragraph(
            text=text, style=style, normalized_text=normalized,
            tab_layout_error=layout_error,
            cell_twips=tab_cell_twips if tab_cell_twips is not None else cell_twips,
            physical_positions=physical_positions, outline_level=outline_level,
            page_break_before=page_break_before,
            explicit_page_break_offsets=tuple(page_break_offsets),
            rendered_page_break_count=rendered_breaks, source_index=source_index))
    return out


# --------------------------------------------------------------------------
# 2. Text hygiene
# --------------------------------------------------------------------------
# The smart-quote conversion in the source doubled apostrophes: Life''s.
# Collapse doubles back to singles. Leave everything else intact.

def clean_text(s: str) -> str:
    s = s.replace("''", "'")
    return s


# --------------------------------------------------------------------------
# 3. Heading field parsing
# --------------------------------------------------------------------------
# Common heading shape: Title – Key [(AltKey)] <tabs> Artist [<tabs> OriginalKey]
# The em-dash (–) separates title from the metadata tail. Fields in the tail
# are tab-separated but the tab COUNT is inconsistent, so we split on runs of
# tabs and classify each token as key-like or name-like. This grammar parses
# headings only; it never discovers song boundaries from paragraph text.

_EMDASH = "\u2013"   # –
# A chord/key token: root note, optional accidental, optional quality/min.
_KEY_TOKEN_RE = re.compile(r"^[A-G][#b]?(m|min|maj|sus|dim|aug|add)?\d*$")
# Parenthesized sounding key with capo, e.g. written D and sounding (E).
_ALTKEY_RE = re.compile(r"\(([A-G][#b]?m?)\)")
_SIMPLE_KEY_RE = re.compile(r"^([A-G])([#b]?)(m?)$")
_KEY_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _capo_for_keys(written: str, sounding: str) -> Optional[int]:
    """Return the fret offset for two keys of the same major/minor quality."""
    first, second = _SIMPLE_KEY_RE.fullmatch(written), _SIMPLE_KEY_RE.fullmatch(sounding)
    if not first or not second or first.group(3) != second.group(3):
        return None

    def pitch(match: re.Match[str]) -> int:
        accidental = {"#": 1, "b": -1, "": 0}[match.group(2)]
        return (_KEY_PITCH[match.group(1)] + accidental) % 12

    return (pitch(second) - pitch(first)) % 12


_ROUND_TRIP_TITLE = "Plain Source Check"


def _round_trip_heading() -> Paragraph:
    """Internal source-parser sentinel; public DOCX boundaries remain Word-only."""
    return Paragraph(_ROUND_TRIP_TITLE, None, outline_level=0)


@dataclass
class TitleInfo:
    title: str
    performance_key: Optional[str]
    alt_key: Optional[str]
    artist: Optional[str]
    original_key: Optional[str]
    unresolved_reason: Optional[str] = None
    candidate_key_field: Optional[str] = None
    capo_fret: Optional[int] = None


def parse_title(p: Paragraph) -> TitleInfo:
    """
    Split on the em-dash, then split the tail on tab-runs into fields.
    Classify: first field = written key (+ optional sounding key); a trailing
    key-like field = original key; the remaining name-like field = artist.
    """
    # Use the tab-preserving text so we can split the tail on tab runs.
    raw = p.text.replace(_TAB * 1, _TAB)  # keep sentinels
    # Split title from tail on the em-dash.
    left, _, tail = raw.partition(_EMDASH)
    title = left.strip()

    # Break the tail into fields on runs of tab sentinels (and stray spaces
    # around them). Collapse multiple tabs to one delimiter.
    fields = [f.strip() for f in re.split(f"{_TAB}+", tail) if f.strip()]

    perf_key = alt_key = artist = orig_key = None

    if fields:
        # The parenthesized sounding key may share the written-key field or
        # occupy the next tab field. It is metadata, never part of the artist.
        head = fields[0]
        am = _ALTKEY_RE.search(head)
        if am:
            alt_key = am.group(1)
            head = _ALTKEY_RE.sub("", head).strip()
        elif len(fields) > 1:
            separate_key = _ALTKEY_RE.fullmatch(fields[1])
            if separate_key:
                alt_key = separate_key.group(1)
                del fields[1]
        toks = head.split()
        if toks and _KEY_TOKEN_RE.match(toks[0]):
            perf_key = toks[0]
            # any leftover words in this field are part of the artist
            leftover = " ".join(toks[1:]).strip()
            if leftover:
                fields.insert(1, leftover)

    # Remaining fields (after the first) are artist and/or original key.
    rest = fields[1:]
    # A trailing lone key-like token = original key.
    if rest and _KEY_TOKEN_RE.match(rest[-1]) and len(rest) >= 2:
        orig_key = rest[-1]
        rest = rest[:-1]
    elif len(rest) == 1 and _KEY_TOKEN_RE.match(rest[0]):
        # ambiguous single key-like token with no artist — treat as orig key
        orig_key = rest[0]
        rest = []
    artist = " ".join(rest).strip() or None

    capo_fret = _capo_for_keys(perf_key, alt_key) if perf_key and alt_key else None
    return TitleInfo(
        title=clean_text(title),
        performance_key=perf_key,
        alt_key=alt_key,
        artist=clean_text(artist) if artist else None,
        original_key=orig_key,
        unresolved_reason="heading_capo_key_relation_unresolved" if alt_key and capo_fret is None else None,
        capo_fret=capo_fret,
    )


_STRUCTURAL_TITLE_SEPARATOR = re.compile(r"\s+(?:--|[\u2013\u2014-])\s+")


def _parse_structural_title(p: Paragraph) -> TitleInfo:
    """Parse a Word heading's fields without using its text to infer a boundary."""
    if _EMDASH in p.text:
        parsed = parse_title(p)
        fields = [field.strip() for field in re.split(f"{_TAB}+", p.text.partition(_EMDASH)[2]) if field.strip()]
        first_field = fields[0].split()[0] if fields else None
        normalized_key_field = _ALTKEY_RE.sub("", first_field or "").strip()
        unknown_key = bool(first_field and not _KEY_TOKEN_RE.match(normalized_key_field))
        slash_parts = normalized_key_field.split("/")
        unsupported_slash_key = (len(slash_parts) == 2 and
                                 all(_KEY_TOKEN_RE.match(part) for part in slash_parts))
        if unknown_key and (len(fields) > 1 or unsupported_slash_key):
            return TitleInfo(parsed.title, None, parsed.alt_key, parsed.artist,
                             parsed.original_key, "unrecognized_heading_key_field",
                             first_field, parsed.capo_fret)
        return parsed

    text = p.stripped
    raw = p.text.strip()
    match = _STRUCTURAL_TITLE_SEPARATOR.search(text)
    if match:
        title = text[:match.start()].strip()
        tail = text[match.end():].strip()
        raw_with_tabs = raw.replace(_TAB, "\t")
        raw_match = _STRUCTURAL_TITLE_SEPARATOR.search(raw_with_tabs)
        raw_tail = raw_with_tabs[raw_match.end():] if raw_match else tail
        fields = [field.strip() for field in re.split(r"\t+", raw_tail) if field.strip()]
        if len(fields) > 1:
            key_token = fields[0].split()[0]
            artist = " ".join(fields[1:])
            return TitleInfo(clean_text(title), None, None, clean_text(artist), None,
                             "unrecognized_heading_key_field" if not _KEY_TOKEN_RE.match(key_token) else None,
                             key_token if not _KEY_TOKEN_RE.match(key_token) else None)
        tokens = tail.split()
        if tokens and _KEY_TOKEN_RE.match(tokens[0]):
            return TitleInfo(clean_text(title), tokens[0], None,
                             clean_text(" ".join(tokens[1:])) or None, None,
                             None if len(tokens) > 1 else "heading_artist_missing")
        return TitleInfo(clean_text(title), None, None, clean_text(tail) or None,
                         None, None)
    if _TAB in p.text:
        fields = [field.strip() for field in p.text.split(_TAB) if field.strip()]
        if fields:
            return TitleInfo(clean_text(fields[0]), None, None,
                             clean_text(" ".join(fields[1:])) or None, None)
    return TitleInfo(clean_text(text), None, None, None, None)


def _is_meaningful_preheading_chart(paragraph: Paragraph) -> bool:
    text = paragraph.stripped
    return bool(text and (
        _TAB in paragraph.text or _BB_RE.match(text) or _BRACKET_SECTION_RE.match(text)
        or _BARE_SECTION_COLON_RE.match(text) or _BARE_SECTION_RE.match(text)
        or is_tab_line(text) or is_decorated_chord_line(text)
        or _INLINE_BRACE_RE.search(text) or is_chord_only_line(text)
    ))


def _is_song_boundary_candidate(p: Paragraph) -> bool:
    """Only a nonempty effective Word outline heading starts a song."""
    return bool(p.stripped and p.outline_level is not None
                and 0 <= p.outline_level <= 8)


# --------------------------------------------------------------------------
# 4. Line classification within a song body
# --------------------------------------------------------------------------
# Section labels: "[Chorus 1]" bracketed, or bare "Verse 1"/"Chorus"/"Bridge".
# A "(Chorus)" in parens = repeat marker, distinct from a section definition.

_BRACKET_SECTION_RE = re.compile(r"^\[([^\]]+)\](.*)$")
_BARE_SECTION_RE = re.compile(
    r"^(Intro|Outro|Verse|Chorus|Bridge|Pre-?Chorus|Interlude|Instrumental|"
    r"Solo|Link|Middle|Refrain|Tag|Ending|Coda|Hook|Breakdown|Turnaround)"
    r"(\s*\d+)?\s*:?\s*$",
    re.IGNORECASE,
)
_REPEAT_PARENS_RE = re.compile(r"^\(([^)]+)\)\s*(x\s*\d+)?\s*$", re.IGNORECASE)
_PROGRESSION_REPEAT_RE = re.compile(r"\(?\s*(\d+)\s*[xX]\)?|\b[xX]\s*(\d+)\b")
# Bare section label followed by a colon and trailing content on the SAME line,
# e.g. "Intro: D  D, E7, G, A" — label defines a section, tail is a progression.
_BARE_SECTION_COLON_RE = re.compile(
    r"^(Intro|Outro|Verse|Chorus|Bridge|Pre-?Chorus|Interlude|Instrumental|"
    r"Solo|Link|Middle|Refrain|Tag|Ending|Coda|Hook|Breakdown|Turnaround)"
    r"(\s*\d+)?\s*:\s*(.+)$",
    re.IGNORECASE,
)
_REPEAT_COUNT_RE = re.compile(r"\(?\s*x?\s*(\d+)\s*x?\s*\)?", re.IGNORECASE)

_BB_RE = re.compile(r"^BB\s*:", re.IGNORECASE)
_CAPO_RE = re.compile(r"capo\s*(\d+)", re.IGNORECASE)
# An "OriginalKey-BPM" token that can ride on the BB line after the structure,
# e.g. "D-114" = original key D, BPM 114. Key must be a valid root; BPM in a
# plausible range so we don't grab arbitrary hyphenated text.
_KEY_BPM_RE = re.compile(r"\b([A-G][#b]?m?)-(\d{2,3})\b")

# A "chord token" for progression/space-line detection.
_CHORD_RE = re.compile(
    r"^[A-G][#b]?"
    r"(m|min|maj|sus|dim|aug|add|M)?"
    r"\d*"
    r"(sus|add|dim|aug|maj|b|#|/)*"
    r"[0-9#b]*"
    r"(/[A-G][#b]?)?$"
)

_ORDERED_ROW_SEPARATORS = "-\u2013\u2014/\\*|,;:"
_CHORD_LIKE_TOKEN_RE = re.compile(
    r"^[A-G][#b]?(?:m|min|maj|sus|dim|aug|add|M)?\d*"
    r"(?:[#b].*|/(?:[A-G][#b]?(?:\d.*|[#b].*)))$"
)
_OPENING_METADATA_PATTERNS = (
    ("capo", re.compile(r"(?i)(?<!\w)capo\s*(\d+)(?!\w)")),
    ("key_bpm", re.compile(r"(?i)(?<!\w)([A-G][#b]?m?)\s*[-\u2013]\s*(\d{2,3})(?!\w)")),
    ("original_key", re.compile(r"(?i)(?<!\w)orig(?:inal)?\s+([A-G][#b]?m?)(?!\w)")),
)


def is_chord_token(tok: str) -> bool:
    return bool(_CHORD_RE.match(tok))


def _is_fully_parenthesized(text: str) -> bool:
    """Whether one balanced outer annotation encloses the complete line."""
    start = len(text) - len(text.lstrip())
    end = len(text.rstrip()) - 1
    if start > end or text[start] != "(":
        return False
    depth = 0
    for index in range(start, end + 1):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth < 0 or (depth == 0 and index != end):
                return False
    return depth == 0


def ordered_chord_analysis(text: str) -> dict:
    """Return chord spans and whether literal ordering carries information.

    Parenthesized/bracketed annotations are opaque. Unknown tokens make the
    row ambiguous for transposition, while the source text remains literal.
    """
    if re.search(r"\d+%", text):
        return {"spans": [], "ambiguous": False, "unresolved_tokens": [],
                "prose_tokens": [], "partial_transposition": False, "literal": False}
    spans: list[dict] = []
    opaque = 0
    unresolved_tokens: list[dict] = []
    prose_tokens: list[dict] = []
    visible = 0
    literal_syntax = False
    repeated_space = False
    opaque_only = _is_fully_parenthesized(text)
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            end = index + 1
            while end < len(text) and text[end].isspace():
                end += 1
            if end - index > 1:
                repeated_space = True
            index = end
            continue
        if char in "([":
            closing = ")" if char == "(" else "]"
            depth = 1
            index += 1
            literal_syntax = True
            while index < len(text) and depth:
                if text[index] == char:
                    depth += 1
                elif text[index] == closing:
                    depth -= 1
                index += 1
            continue
        separators_without_slash = _ORDERED_ROW_SEPARATORS.replace("/", "")
        if char in separators_without_slash or char in ")]":
            literal_syntax = True
            index += 1
            continue
        if char == "%":
            before = text[:index].rstrip()
            after = text[index + 1:].lstrip()
            if before.endswith("|") and after.startswith("|"):
                literal_syntax = True
                index += 1
                continue
        if char == "/":
            literal_syntax = True
            index += 1
            continue
        end = index + 1
        while (end < len(text) and not text[end].isspace()
               and text[end] not in separators_without_slash + "()[]"):
            end += 1
        token = text[index:end]
        if is_chord_token(token):
            spans.append({"start": index, "end": end, "symbol": token})
        elif re.fullmatch(r"(?:\d+[xX]?|[xX]\d+)", token):
            literal_syntax = True
        else:
            opaque += 1
            item = {"start": index, "end": end, "text": token}
            if _CHORD_LIKE_TOKEN_RE.fullmatch(token):
                unresolved_tokens.append(item)
            else:
                prose_tokens.append(item)
        visible += 1
        index = end

    eligible = len(spans) >= 2 and len(spans) >= (visible + 1) // 2
    partial = eligible and bool(unresolved_tokens) and not prose_tokens and not opaque_only
    return {
        "spans": spans if eligible else [],
        "ambiguous": opaque > 0 or opaque_only,
        "unresolved_tokens": unresolved_tokens,
        "prose_tokens": prose_tokens,
        "partial_transposition": partial,
        "literal": opaque_only or (eligible and (literal_syntax or repeated_space)),
    }


def _link_literal_chord_rows(chart: dict) -> None:
    """Retain chord spans and their following lyric association without changing source text."""
    for section in chart.get("sections", []):
        lines = section.get("lines", [])
        for index, line in enumerate(lines[:-1]):
            if (line.get("type") != "raw" or lines[index + 1].get("type") != "lyric"
                    or is_tab_line(line.get("text", ""))):
                continue
            analysis = ordered_chord_analysis(line.get("text", ""))
            if analysis["spans"] and not analysis["prose_tokens"]:
                line["paired_to_next"] = True
                line["chord_spans"] = [
                    {"symbol": span["symbol"], "start": span["start"], "end": span["end"]}
                    for span in analysis["spans"]
                ]


def is_ordered_chord_line(text: str) -> bool:
    analysis = ordered_chord_analysis(text)
    return bool(analysis["literal"])


def extract_opening_metadata(text: str, current: Optional[dict] = None) -> tuple[str, dict, list[str]]:
    """Capture bounded metadata fields, returning unconsumed source text.

    The caller limits this to the opening metadata zone. Arbitrary prose is
    never consumed: after removing candidate spans, only whitespace and chart
    punctuation may remain, or the original text is returned unchanged.
    """
    matches = []
    for kind, pattern in _OPENING_METADATA_PATTERNS:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), kind, match))
    if not matches:
        return text, {}, []
    matches.sort(key=lambda item: item[0])
    spans = []
    values: dict[str, list] = {}
    for start, end, kind, match in matches:
        if spans and start < spans[-1][1]:
            continue
        spans.append((start, end))
        if kind == "capo":
            values.setdefault(kind, []).append(int(match.group(1)))
        elif kind == "key_bpm":
            values.setdefault("original_key", []).append(match.group(1))
            values.setdefault("bpm", []).append(int(match.group(2)))
        else:
            values.setdefault(kind, []).append(match.group(1))
    remainder = list(text)
    for start, end in spans:
        remainder[start:end] = " " * (end - start)
    residual = "".join(remainder)
    if re.sub(r"[\s/\\,*;|:-]*", "", residual):
        return text, {}, []
    current = current or {}
    conflicts = [key for key, items in values.items()
                 if len(set(items)) > 1 or
                 (current.get(key) is not None and current.get(key) != items[0])]
    if conflicts:
        return text, {}, conflicts
    captured = {key: items[0] for key, items in values.items()}
    return residual, captured, []


def is_chord_only_line(text: str) -> bool:
    """True if every whitespace-separated token is a chord (the classic
    chord line that sits above a lyric line)."""
    toks = text.split()
    if not toks:
        return False
    return all(is_chord_token(t) for t in toks)


def is_decorated_chord_line(text: str) -> bool:
    """Identify literal chord rows when structured grouping changes the line."""
    return is_ordered_chord_line(text) and not is_tab_line(text)


# Tablature: lines like  e--5-2-0-3-...  or rows of dashes/pipes/digits.
_TAB_LINE_RE = re.compile(r"^[eEADGBb]?\s*[-|]{2,}")


def is_tab_line(text: str) -> bool:
    if _TAB_LINE_RE.match(text.strip()):
        return True
    # dense dash/pipe/digit content with few letters
    t = text.strip()
    if len(t) >= 6:
        symbolic = sum(c in "-|0123456789" for c in t)
        if symbolic / len(t) > 0.6:
            return True
    return False


# --------------------------------------------------------------------------
# 5. Chord/lyric pairing
# --------------------------------------------------------------------------

_INLINE_BRACE_RE = re.compile(r"\{([^}]*)\}")
_GENERATED_HEADER = "[[CCM-CHART:2]]"
_LEGACY_GENERATED_HEADER = "[[CCM-CHART:1]]"
_DOCX_SPACER_MARKER = "\x00CCM-DOCX-SPACER\x00"


def _unescape_generated_text(text: str) -> str:
    return re.sub(r"\\([\\\[nr])", lambda m: {
        "n": "\n", "r": "\r", "\\": "\\", "[": "[",
    }[m.group(1)], text)


def _parse_generated_lyric(text: str, confidence: str) -> dict:
    chords = []
    lyric = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            escaped = text[i + 1]
            lyric.append("\n" if escaped == "n" else "\r" if escaped == "r" else escaped)
            i += 2
        elif text.startswith("[[C:", i):
            end = text.find("]]", i + 4)
            if end < 0:
                raise ValueError("Generated chart has an incomplete chord anchor marker")
            try:
                symbol = unquote_to_bytes(text[i + 4:end]).decode("utf-8")
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("Generated chart has an invalid chord anchor marker") from exc
            if not is_chord_token(symbol):
                raise ValueError("Generated chart has an invalid chord symbol")
            chords.append({"sym": clean_text(symbol), "pos": _column_length("".join(lyric))})
            i = end + 2
        else:
            lyric.append(text[i])
            i += 1
    return {"type": "lyric", "text": "".join(lyric), "chords": chords,
            "confidence": confidence}


def _parse_generated_lyric_v2(text: str, confidence: str) -> dict:
    delimiter = "[[CCM-CHORDS]]"
    split_at = text.rfind(delimiter)
    if split_at < 0:
        raise ValueError("Generated lyric is missing its chord list")
    lyric = _unescape_generated_text(text[:split_at])
    encoded = text[split_at + len(delimiter):]
    chords = []
    cursor = 0
    marker_re = re.compile(r"\[\[C:(\d+):([^\]]*)\]\]")
    for match in marker_re.finditer(encoded):
        if encoded[cursor:match.start()].strip():
            raise ValueError("Generated lyric has invalid chord data")
        try:
            symbol = unquote_to_bytes(match.group(2)).decode("utf-8")
            position = int(match.group(1))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Generated lyric has an invalid chord anchor") from exc
        if position < 0 or position > 9007199254740991 or not is_chord_token(symbol):
            raise ValueError("Generated lyric has an invalid chord anchor")
        chords.append({"sym": clean_text(symbol), "pos": position})
        cursor = match.end()
    if encoded[cursor:].strip():
        raise ValueError("Generated lyric has invalid chord data")
    return {"type": "lyric", "text": lyric, "chords": chords,
            "confidence": confidence}


def parse_generated_chart_body(text: str) -> dict:
    """Parse the versioned editable representation emitted by chartToText()."""
    lines = text.splitlines()
    if not lines:
        raise ValueError("Generated chart is empty")
    header = lines.pop(0)
    version2 = header == _GENERATED_HEADER
    if header not in (_GENERATED_HEADER, _LEGACY_GENERATED_HEADER):
        raise ValueError("Generated chart must start with a supported CCM header")
    if lines and lines[-1] == "":
        lines.pop()
    sections = []
    current = None
    for line in lines:
        if line.startswith("[[CCM-SECTION]]"):
            if current and current["lines"] and current["lines"][-1].get("type") == "spacer":
                current["lines"].pop()
            label = _unescape_generated_text(line[len("[[CCM-SECTION]]"):])
            current = {"label": label or None, "lines": []}
            sections.append(current)
            continue
        if current is None:
            raise ValueError("Generated chart line appears before its section marker")
        lyric_match = re.fullmatch(r"\[\[CCM-LYRIC:(high|low)\]\](.*)", line)
        if lyric_match:
            parser = _parse_generated_lyric_v2 if version2 else _parse_generated_lyric
            current["lines"].append(parser(lyric_match.group(2), lyric_match.group(1)))
            continue
        progression_match = re.fullmatch(r"\[\[CCM-PROGRESSION\]\](.*)", line)
        if progression_match:
            body = progression_match.group(1)
            note_index = body.find("[[CCM-NOTE]]")
            note = None
            if note_index >= 0:
                note = _unescape_generated_text(body[note_index + len("[[CCM-NOTE]]"):])
                body = body[:note_index]
            repeat = re.search(r"\[\[CCM-PROGRESSION-REPEAT:(\d+)\]\]$", body)
            if repeat:
                body = body[:repeat.start()]
            chords = body.split()
            if any(not is_chord_token(chord) for chord in chords):
                raise ValueError("Generated progression contains an invalid chord token")
            parsed = {"type": "progression", "chords": chords}
            if repeat:
                parsed["repeat"] = int(repeat.group(1))
            if note_index >= 0:
                parsed["note"] = note
            current["lines"].append(parsed)
            continue
        repeat_match = re.fullmatch(r"\[\[CCM-REPEAT(?::(\d+))?\]\](.*)", line)
        if repeat_match:
            parsed = {"type": "repeat", "ref": _unescape_generated_text(repeat_match.group(2))}
            if repeat_match.group(1):
                parsed["times"] = int(repeat_match.group(1))
            current["lines"].append(parsed)
            continue
        if line.startswith("[[CCM-RAW]]"):
            current["lines"].append({"type": "raw",
                                     "text": _unescape_generated_text(line[len("[[CCM-RAW]]"):])})
            continue
        if line == "[[CCM-SPACER]]":
            if not current["lines"] or current["lines"][-1].get("type") != "spacer":
                current["lines"].append({"type": "spacer"})
            continue
        raise ValueError("Generated chart contains a missing or unsupported line marker")
    return {"sections": sections}


def _escape_generated_text(text: str) -> str:
    return (str(text or "").replace("\\", "\\\\").replace("[", "\\[")
            .replace("\r", "\\r").replace("\n", "\\n"))


def serialize_generated_chart(chart: dict) -> str:
    """Serialize CCM v2; lyric positions are zero-based UTF-16 code units."""
    out = [_GENERATED_HEADER]
    for section in chart.get("sections", []):
        out.append("[[CCM-SECTION]]" + _escape_generated_text(section.get("label") or ""))
        for line in section.get("lines", []):
            kind = line.get("type")
            if kind == "lyric":
                confidence = "low" if line.get("confidence") == "low" else "high"
                anchors = []
                for chord in line.get("chords", []):
                    pos = chord.get("pos")
                    if not isinstance(pos, int) or pos < 0 or pos > 9007199254740991:
                        raise ValueError("Chord column must be a non-negative safe integer UTF-16 column")
                    symbol = quote(chord.get("sym", ""), safe="-_.!~*'()")
                    anchors.append((pos, f"[[C:{pos}:{symbol}]]"))
                anchors.sort(key=lambda anchor: anchor[0])
                encoded = "".join(value for _, value in anchors)
                out.append(f"[[CCM-LYRIC:{confidence}]]{_escape_generated_text(line.get('text', ''))}[[CCM-CHORDS]]{encoded}")
            elif kind == "progression":
                body = " ".join(line.get("chords", []))
                if line.get("repeat") is not None:
                    body += (" " if body else "") + f"[[CCM-PROGRESSION-REPEAT:{line['repeat']}]]"
                if "note" in line:
                    body += "[[CCM-NOTE]]" + _escape_generated_text(line["note"])
                out.append("[[CCM-PROGRESSION]]" + body)
            elif kind == "repeat":
                times = f":{line['times']}" if line.get("times") is not None else ""
                out.append(f"[[CCM-REPEAT{times}]]" + _escape_generated_text(line.get("ref", "")))
            elif kind == "raw":
                out.append("[[CCM-RAW]]" + _escape_generated_text(line.get("text", "")))
            elif kind == "spacer":
                out.append("[[CCM-SPACER]]")
            else:
                raise ValueError(f"Unsupported chart line type: {kind}")
    return "\n".join(out) + "\n"


def is_generated_chart_body(text: str) -> bool:
    return any(text.startswith(header + newline)
               for header in (_GENERATED_HEADER, _LEGACY_GENERATED_HEADER)
               for newline in ("\n", "\r\n"))


def parse_inline_brace_line(text: str) -> dict:
    """
    "{D}Well she was an {E7}American girl" ->
      lyric text with braces removed, chords at exact character offsets.
    Position is EXACT (confidence high).
    """
    chords = []
    lyric_chars = []
    i = 0
    for m in _INLINE_BRACE_RE.finditer(text):
        # append text before the brace
        lyric_chars.append(text[i:m.start()])
        pos = _column_length("".join(lyric_chars))
        sym = m.group(1).strip()
        if sym:
            chords.append({"sym": clean_text(sym), "pos": pos})
        i = m.end()
    lyric_chars.append(text[i:])
    lyric = clean_text("".join(lyric_chars))
    return {"type": "lyric", "text": lyric, "chords": chords,
            "confidence": "high"}


def pair_space_aligned(chord_line: str, lyric_line: str) -> dict:
    """
    Classic two-line form: chord symbols positioned by column above a lyric.
    Map each chord's starting column to the nearest character index in the
    lyric. Position is COMPUTED (confidence low) because manual spacing drifts.
    """
    chords = []
    for m in re.finditer(r"\S+", chord_line):
        sym = m.group(0)
        if not is_chord_token(sym):
            # not a real chord (stray annotation on the chord line) — skip,
            # but keep it from poisoning the pairing.
            continue
        col = _column_length(chord_line[:m.start()])
        pos = col
        chords.append({"sym": clean_text(sym), "pos": pos})
    return {"type": "lyric", "text": clean_text(lyric_line),
            "chords": chords, "confidence": "low"}


def _retarget_chord_columns(chord_line: str, physical_positions: dict[int, float],
                            lyric_cell_twips: float) -> tuple[Optional[str], Optional[str]]:
    """Map physical chord-row columns into the lyric row's UTF-16 text columns."""
    out = []
    cursor = 0
    for match in re.finditer(r"\S+", chord_line):
        symbol = match.group(0)
        if not is_chord_token(symbol):
            continue
        physical_twips = physical_positions.get(match.start())
        if physical_twips is None:
            return None, "tabbed_chord_physical_position_unavailable"
        lyric_column = int(physical_twips / lyric_cell_twips + 0.5)
        if lyric_column < cursor:
            return None, "tabbed_chords_overlap_after_lyric_column_mapping"
        out.append(" " * (lyric_column - cursor) + symbol)
        cursor = lyric_column + _column_length(symbol)
    return "".join(out), None


def plain_text_chart_body(chart: dict, inline_section_tails: Optional[dict[int, str]] = None) -> tuple[Optional[str], Optional[str]]:
    """Serialize supported chart structures through the ordinary text contract."""
    out: list[str] = []
    inline_section_tails = inline_section_tails or {}
    for section_index, section in enumerate(chart.get("sections", [])):
        label = section.get("label")
        section_lines = section.get("lines", [])
        inline_style = inline_section_tails.get(section_index)
        inline_first = bool(label and inline_style and section_lines and
                            section_lines[0].get("type") in ("raw", "progression"))
        if label and inline_first:
            first = section_lines[0]
            if first.get("type") == "raw":
                tail = first.get("text", "")
                separator = ""
            else:
                tail = " ".join(first.get("chords", []))
                if first.get("repeat") is not None:
                    tail += f" {first['repeat']}x" if tail else f"{first['repeat']}x"
                if first.get("note"):
                    tail += f" {first['note']}" if tail else first["note"]
                separator = " "
            prefix = f"[{label}]" if inline_style == "bracket" else f"{label}:"
            out.append(f"{prefix}{separator}{tail}")
            section_lines = section_lines[1:]
        elif label:
            if "]" in label or "\n" in label:
                return None, "section_label_not_representable_in_ordinary_text"
            out.append(f"[{label}]")
        for line in section_lines:
            kind = line.get("type")
            if kind == "spacer":
                out.append("")
            elif kind == "raw":
                raw = line.get("text", "")
                if not (is_tab_line(raw) or is_decorated_chord_line(raw)):
                    return None, "raw_line_not_recognized_by_ordinary_text_parser"
                out.append(raw)
            elif kind == "repeat":
                marker = f"({line.get('ref', '')})"
                if line.get("times") is not None:
                    marker += f" x{line['times']}"
                out.append(marker)
            elif kind == "progression":
                text = " ".join(line.get("chords", []))
                if line.get("repeat") is not None:
                    text += f" {line['repeat']}x" if text else f"{line['repeat']}x"
                if line.get("note"):
                    text += f" {line['note']}" if text else line["note"]
                out.append(text)
            elif kind == "lyric":
                text = line.get("text", "")
                chords = line.get("chords", [])
                if not chords:
                    if "{" in text or "}" in text:
                        return None, "literal_braces_not_representable_in_ordinary_text"
                    out.append(text)
                    continue
                ordered = list(chords)
                positions = [int(chord["pos"]) for chord in ordered]
                if positions != sorted(positions):
                    return None, "chord_order_not_representable_in_ordinary_text"
                cursor = 0
                chord_row: list[str] = []
                can_use_row = True
                for chord in ordered:
                    pos = int(chord["pos"])
                    token = chord["sym"]
                    if pos < cursor:
                        can_use_row = False
                        break
                    chord_row.append(" " * (pos - cursor) + token)
                    cursor = pos + _column_length(token)
                if can_use_row:
                    if "{" in text or "}" in text:
                        return None, "literal_braces_not_representable_in_ordinary_text"
                    out.extend(["".join(chord_row), text])
                    # The ordinary source represents alignment with spaces,
                    # so the shared parser correctly classifies these anchors
                    # as low-confidence space-aligned positions.
                    line["confidence"] = "low"
                else:
                    if "{" in text or "}" in text:
                        return None, "colliding_chords_with_literal_braces"
                    markers: dict[int, list[str]] = {}
                    for chord in ordered:
                        pos = int(chord["pos"])
                        if pos > _column_length(text):
                            return None, "colliding_beyond_end_chords_not_representable"
                        markers.setdefault(pos, []).append(chord["sym"])
                    pieces = []
                    previous = 0
                    for pos, symbols in sorted(markers.items()):
                        py_pos = _python_index_for_utf16(text, pos)
                        if py_pos is None:
                            return None, "chord_anchor_inside_utf16_surrogate"
                        previous_py = _python_index_for_utf16(text, previous)
                        if previous_py is None:
                            return None, "chord_anchor_inside_utf16_surrogate"
                        pieces.append(text[previous_py:py_pos])
                        pieces.extend("{" + symbol + "}" for symbol in symbols)
                        previous = pos
                    final_py = _python_index_for_utf16(text, previous)
                    if final_py is None:
                        return None, "chord_anchor_inside_utf16_surrogate"
                    pieces.append(text[final_py:])
                    out.append("".join(pieces))
                    line["confidence"] = "high"
            else:
                return None, "unsupported_chart_line_type"
    return "\n".join(out) + "\n", None


def _python_index_for_utf16(text: str, column: int) -> Optional[int]:
    units = 0
    for index, char in enumerate(text):
        if units == column:
            return index
        units += _column_length(char)
        if units > column:
            return None
    return len(text) if units == column else None


def _first_chart_difference(expected: dict, actual: dict) -> tuple[str, str]:
    expected_sections = expected.get("sections", [])
    actual_sections = actual.get("sections", [])
    for section_index in range(max(len(expected_sections), len(actual_sections))):
        if section_index >= len(expected_sections) or section_index >= len(actual_sections):
            return str(section_index + 1), "1"
        expected_section = expected_sections[section_index]
        actual_section = actual_sections[section_index]
        if expected_section.get("label") != actual_section.get("label"):
            return str(section_index + 1), "1"
        expected_lines = expected_section.get("lines", [])
        actual_lines = actual_section.get("lines", [])
        for line_index in range(max(len(expected_lines), len(actual_lines))):
            if (line_index >= len(expected_lines) or line_index >= len(actual_lines)
                    or expected_lines[line_index] != actual_lines[line_index]):
                return str(section_index + 1), str(line_index + 1)
    return "?", "?"


def parse_progression(section: Optional[str], chord_part: str) -> dict:
    """
    "[Solo] G Bm C D (2x)" / "[Outro] G Cadd9 ... 12X end on G"
    Pull chord tokens in order; capture a repeat count and any trailing note.
    """
    repeat = None
    note = None

    # find a repeat count like (2x), x4, 4X, 12X and REMOVE it from the text
    # so it doesn't linger in the trailing note.
    rm = re.search(r"\(?\s*(\d+)\s*[xX]\)?|\b[xX]\s*(\d+)\b", chord_part)
    if rm:
        repeat = int(rm.group(1) or rm.group(2))
        chord_part = (chord_part[:rm.start()] + " " + chord_part[rm.end():])

    toks = chord_part.split()
    chords = []
    trailing = []
    for t in toks:
        if is_chord_token(t):
            chords.append(clean_text(t))
        else:
            trailing.append(t)
    if trailing:
        note = clean_text(" ".join(trailing))

    out = {"type": "progression", "chords": chords}
    if section:
        out["section"] = section
    if repeat:
        out["repeat"] = repeat
    if note:
        out["note"] = note
    return out


# --------------------------------------------------------------------------
# 6. Song assembly
# --------------------------------------------------------------------------

@dataclass
class Song:
    title: str
    artist: Optional[str]
    performance_key: Optional[str]
    original_key: Optional[str]
    alt_key: Optional[str]
    capo_fret: int
    bpm: Optional[int]
    bb_structure: Optional[str]
    primary_genre: Optional[str]
    source_document: Optional[str]
    chart_content: dict = field(default_factory=lambda: {"sections": []})
    chart_source: Optional[str] = None
    warnings: list[dict] = field(default_factory=list)
    # Additive provenance marker: loaders reject older artifacts that cannot
    # prove they were checked for unresolved Word tab placement.
    parser_diagnostics_version: int = 1


def _new_section(label: Optional[str]) -> dict:
    return {"label": label, "lines": []}


def parse_songs(paragraphs: list[Paragraph],
                source_document: Optional[str] = None,
                cover_genre: Optional[str] = None,
                _derive_plain_body: bool = True,
                _parse_opening_metadata: bool = True) -> list[Song]:
    """
    Walk the paragraph stream, splitting into songs at each effective heading and
    classifying every body line into the normalized chart_content model.
    """
    songs: list[Song] = []
    cur: Optional[Song] = None
    cur_section: Optional[dict] = None
    pending_chord_line: Optional[Paragraph] = None  # retain OOXML tabs until pairing
    generated_mode = False
    generated_header = _GENERATED_HEADER
    generated_lines: list[str] = []
    chart_started = False
    inline_section_tails: dict[int, str] = {}
    song_inline_tails: dict[int, dict[int, str]] = {}
    unresolved_preheading: Optional[Paragraph] = None

    def flush_pending_as_progression():
        """If a chord-only line had no lyric under it, it's a progression."""
        nonlocal pending_chord_line, chart_started
        if pending_chord_line is not None and cur is not None:
            ensure_section()
            pending_text = pending_chord_line.text_spaces
            if is_ordered_chord_line(pending_text):
                cur_section["lines"].append({"type": "raw", "text": pending_text})
            else:
                cur_section["lines"].append(parse_progression(None, pending_text))
            pending_chord_line = None
            chart_started = True

    def ensure_section():
        nonlocal cur_section
        if cur_section is None:
            cur_section = _new_section(None)
            cur.chart_content["sections"].append(cur_section)

    def flush_generated():
        nonlocal generated_mode, generated_header, generated_lines, cur_section, chart_started
        if generated_mode and cur is not None:
            lines = list(generated_lines)
            while lines and lines[-1] == _DOCX_SPACER_MARKER:
                lines.pop()
            lines = ["[[CCM-SPACER]]" if line == _DOCX_SPACER_MARKER else line for line in lines]
            generated = parse_generated_chart_body("\n".join([generated_header, *lines]))
            cur.chart_content["sections"].extend(generated["sections"])
            cur_section = generated["sections"][-1] if generated["sections"] else None
            chart_started = any(section["lines"] for section in generated["sections"])
        generated_mode = False
        generated_lines = []

    def start_section(label: Optional[str], inline_style: Optional[str] = None):
        nonlocal cur_section, chart_started
        if cur_section and cur_section["lines"] and cur_section["lines"][-1].get("type") == "spacer":
            cur_section["lines"].pop()
        cur_section = _new_section(label)
        cur.chart_content["sections"].append(cur_section)
        if inline_style:
            inline_section_tails[len(cur.chart_content["sections"]) - 1] = inline_style
        chart_started = True

    expanded_paragraphs = []
    for p in paragraphs:
        parts = p.text.split("\n")
        normalized_parts = p.text_spaces.split("\n")
        source_offset = 0
        raw_start = 0
        for index, part in enumerate(parts):
            normalized = normalized_parts[index]
            part_length = _column_length(normalized)
            positions = None
            if p.physical_positions is not None:
                positions = {
                    key - source_offset: value
                    for key, value in p.physical_positions.items()
                    if source_offset <= key < source_offset + part_length
                }
            expanded_paragraphs.append(Paragraph(
                text=part, style=p.style, normalized_text=normalized,
                tab_layout_error=p.tab_layout_error, cell_twips=p.cell_twips,
                physical_positions=positions,
                outline_level=p.outline_level if index == 0 else None,
                page_break_before=p.page_break_before if index == 0 else
                (raw_start > 0 and raw_start - 1 in p.explicit_page_break_offsets),
                explicit_page_break_offsets=p.explicit_page_break_offsets if index == 0 else (),
                rendered_page_break_count=p.rendered_page_break_count if index == 0 else 0,
                source_index=p.source_index, source_span_index=index + 1))
            source_offset += part_length + (1 if index < len(parts) - 1 else 0)
            raw_start += len(part) + (1 if index < len(parts) - 1 else 0)
    for p in expanded_paragraphs:
        raw_text = p.text_spaces
        txt = raw_text
        stripped = txt.strip()

        # ---- effective outline heading: starts a new song ----
        if _is_song_boundary_candidate(p):
            if unresolved_preheading is not None:
                raise ValueError(
                    "pre_heading_chart_content_unresolved "
                    f"paragraph={unresolved_preheading.source_index} "
                    f"span={unresolved_preheading.source_span_index or 1}")
            flush_pending_as_progression()
            flush_generated()
            if cur_section and cur_section["lines"] and cur_section["lines"][-1].get("type") == "spacer":
                cur_section["lines"].pop()
            ti = _parse_structural_title(p)
            cur = Song(
                title=ti.title, artist=ti.artist,
                performance_key=ti.performance_key,
                original_key=ti.original_key, alt_key=ti.alt_key,
                capo_fret=ti.capo_fret if ti.capo_fret is not None else 0,
                bpm=None, bb_structure=None,
                primary_genre=cover_genre, source_document=source_document,
            )
            if ti.unresolved_reason:
                cur.warnings.append({
                    "code": "plain_text_body_unresolved",
                    "section": "?", "line": "?",
                    "reason": ti.unresolved_reason,
                    "paragraph": p.source_index,
                    "span": p.source_span_index,
                    "style": p.style,
                    "outline_level": p.outline_level,
                    "page_break_before": p.page_break_before,
                    "candidate_title": ti.title,
                    "candidate_artist": ti.artist,
                    "candidate_performance_key": ti.performance_key,
                    "candidate_key_field": ti.candidate_key_field,
                })
            songs.append(cur)
            inline_section_tails = {}
            song_inline_tails[id(cur)] = inline_section_tails
            cur_section = None
            chart_started = False
            continue

        if cur is None:
            if _is_meaningful_preheading_chart(p):
                unresolved_preheading = unresolved_preheading or p
            elif stripped and cover_genre is None:
                # Preserve the existing first plain cover-line genre behavior.
                cover_genre = stripped
            continue

        if (_parse_opening_metadata and not chart_started
                and not _BB_RE.match(stripped)
                and cur_section is None and stripped):
            residual, metadata, metadata_conflicts = extract_opening_metadata(
                raw_text, {
                    "capo": cur.capo_fret if cur.capo_fret else None,
                    "original_key": cur.original_key,
                    "bpm": cur.bpm,
                })
            if metadata_conflicts:
                cur.warnings.append({
                    "code": "opening_metadata_conflict",
                    "section": "?", "line": "?",
                    "reason": "conflicting_opening_metadata:" + ",".join(sorted(metadata_conflicts)),
                    "paragraph": p.source_index, "span": p.source_span_index,
                })
            elif metadata:
                for field, value in metadata.items():
                    setattr(cur, {"capo": "capo_fret"}.get(field, field), value)
                raw_text = residual
                txt = residual
                stripped = txt.strip()
                if not stripped:
                    continue

        if stripped in (_GENERATED_HEADER, _LEGACY_GENERATED_HEADER):
            flush_pending_as_progression()
            generated_mode = True
            generated_header = stripped
            generated_lines = []
            continue
        if generated_mode:
            generated_lines.append(_DOCX_SPACER_MARKER if not raw_text.strip() else raw_text)
            continue

        if _TAB in p.text and p.tab_layout_error:
            cur.warnings.append({
                "code": "word_tab_placement_unresolved",
                "section": max(1, len(cur.chart_content["sections"])),
                "line": (len(cur_section["lines"]) + 1) if cur_section else 1,
                "reason": p.tab_layout_error,
                "paragraph": p.source_index,
                "span": p.source_span_index,
            })

        # ---- blank line ----
        if not stripped:
            flush_pending_as_progression()
            if chart_started and cur_section and (not cur_section["lines"] or cur_section["lines"][-1].get("type") != "spacer"):
                cur_section["lines"].append({"type": "spacer"})
            continue

        # ---- BB / structure line ----
        if _BB_RE.match(stripped):
            flush_pending_as_progression()
            # BB text is everything after "BB:", minus trailing capo and
            # OriginalKey-BPM tokens.
            original_bb_value = raw_text.split(":", 1)[1]
            has_metadata_conflict = False
            body = stripped.split(":", 1)[1].strip()
            capo_m = _CAPO_RE.search(body)
            if capo_m:
                candidate_capo = int(capo_m.group(1))
                if cur.capo_fret and cur.capo_fret != candidate_capo:
                    has_metadata_conflict = True
                    cur.warnings.append({
                        "code": "opening_metadata_conflict", "section": "?", "line": "?",
                        "reason": "conflicting_opening_metadata:capo",
                        "paragraph": p.source_index, "span": p.source_span_index,
                    })
                else:
                    cur.capo_fret = candidate_capo
                body = _CAPO_RE.sub("", body).strip(" /").strip()
            kb_m = _KEY_BPM_RE.search(body)
            if kb_m:
                candidate_key = kb_m.group(1)
                candidate_bpm = int(kb_m.group(2))
                conflicting = []
                if cur.original_key and cur.original_key != candidate_key:
                    conflicting.append("original_key")
                if cur.bpm is not None and cur.bpm != candidate_bpm:
                    conflicting.append("bpm")
                if conflicting:
                    has_metadata_conflict = True
                    cur.warnings.append({
                        "code": "opening_metadata_conflict", "section": "?", "line": "?",
                        "reason": "conflicting_opening_metadata:" + ",".join(sorted(conflicting)),
                        "paragraph": p.source_index, "span": p.source_span_index,
                    })
                # Fill each absent field independently. A conflict in the
                # original key does not discard an otherwise unconflicted BPM.
                if cur.original_key is None:
                    cur.original_key = candidate_key
                if cur.bpm is None:
                    cur.bpm = candidate_bpm
                body = _KEY_BPM_RE.sub("", body).strip(" /").strip()
            # On conflict, retain the complete source value. Metadata fields
            # already established by the heading remain authoritative, while
            # the warning prevents this record from passing the loader gate.
            cur.bb_structure = (original_bb_value if has_metadata_conflict
                                else clean_text(body) if body else None)
            continue

        # ---- tablature / dense symbolic line -> raw passthrough ----
        if is_tab_line(stripped):
            flush_pending_as_progression()
            ensure_section()
            cur_section["lines"].append(
                {"type": "raw", "text": txt})
            chart_started = True
            continue

        # ---- section label (bracketed or bare), possibly with a progression ----
        bm = _BRACKET_SECTION_RE.match(stripped)
        if bm:
            flush_pending_as_progression()
            label = bm.group(1).strip()
            close_index = raw_text.find("]")
            trailing = raw_text[close_index + 1:] if close_index >= 0 else bm.group(2)
            # repeat-only paren like "(Chorus)"? handled below in bare branch;
            # here bracket label always defines/starts a section.
            start_section(clean_text(label), "bracket" if trailing else None)
            if trailing:
                if re.search(r"\d+%", trailing):
                    cur_section["lines"].append({"type": "lyric", "text": trailing,
                                                 "chords": [], "confidence": "high"})
                    chart_started = True
                    continue
                # e.g. "[Intro] G D C (4x)" — a progression on the same line
                if is_decorated_chord_line(trailing):
                    cur_section["lines"].append({"type": "raw", "text": trailing})
                    chart_started = True
                elif ordered_chord_analysis(trailing)["prose_tokens"]:
                    cur_section["lines"].append({"type": "raw", "text": trailing})
                    chart_started = True
                elif any(is_chord_token(t) for t in trailing.split()):
                    cur_section["lines"].append(
                        parse_progression(None, trailing))
                    chart_started = True
                else:
                    cur_section["lines"].append({"type": "raw", "text": trailing})
                    chart_started = True
            continue

        # bare "(Chorus)" repeat marker (not a definition)
        rp = _REPEAT_PARENS_RE.match(stripped)
        if rp and not is_chord_only_line(stripped):
            inner = rp.group(1).strip()
            # only treat as a repeat marker if it names a section-ish word
            if _BARE_SECTION_RE.match(inner) or inner.lower() in {
                    "chorus", "verse", "bridge", "intro", "outro"}:
                flush_pending_as_progression()
                ensure_section()
                cnt = None
                cm = _REPEAT_COUNT_RE.search(rp.group(2) or "")
                if cm:
                    cnt = int(cm.group(1))
                marker = {"type": "repeat", "ref": clean_text(inner)}
                if cnt:
                    marker["times"] = cnt
                cur_section["lines"].append(marker)
                chart_started = True
                continue

        # bare "Intro: D  D, E7, G, A" — label + trailing progression
        bc = _BARE_SECTION_COLON_RE.match(stripped)
        if bc:
            label = (bc.group(1) + (bc.group(2) or "")).strip()
            tail = bc.group(3).strip()
            if re.search(r"\d+%", tail):
                flush_pending_as_progression()
                start_section(clean_text(label))
                colon = raw_text.find(":")
                cur_section["lines"].append({"type": "lyric", "text": raw_text[colon + 1:],
                                             "chords": [], "confidence": "high"})
                chart_started = True
                continue
            # Only treat the tail as a progression if it's chord-ish; otherwise
            # it's likely a lyric ("Intro:" rarely precedes lyrics, but guard).
            tail_toks = re.split(r"[,\s]+", tail)
            chordish = (tail_toks and sum(is_chord_token(t) for t in tail_toks if t)
                        >= max(1, len(tail_toks)//2))
            if is_decorated_chord_line(tail):
                flush_pending_as_progression()
                start_section(clean_text(label), "colon")
                colon = raw_text.find(":")
                cur_section["lines"].append({"type": "raw", "text": raw_text[colon + 1:]})
                chart_started = True
                continue
            if chordish:
                flush_pending_as_progression()
                start_section(clean_text(label), "colon")
                # commas are just separators inside these inline progressions
                cur_section["lines"].append(
                    parse_progression(None, tail.replace(",", " ")))
                chart_started = True
                continue
            flush_pending_as_progression()
            start_section(clean_text(label))
            cur_section["lines"].append({"type": "lyric", "text": clean_text(tail),
                                         "chords": [], "confidence": "high"})
            chart_started = True
            continue

        bare = _BARE_SECTION_RE.match(stripped)
        if bare:
            flush_pending_as_progression()
            start_section(clean_text(stripped.rstrip(":").strip()))
            continue

        # ---- inline-brace lyric line ----
        if _INLINE_BRACE_RE.search(txt):
            flush_pending_as_progression()
            ensure_section()
            cur_section["lines"].append(parse_inline_brace_line(txt))
            chart_started = True
            continue

        # Preserve the classic chord-over-lyric pairing before considering a
        # standalone literal chord row. Tab-aligned rows retain their anchors.
        if is_chord_only_line(stripped):
            flush_pending_as_progression()
            pending_chord_line = p
            continue

        # Match the shared JavaScript parser's ordinary-text progression
        # rule for a mostly-chord line carrying a repeat or short annotation.
        tokens = stripped.split()
        if re.search(r"\d+%", txt):
            flush_pending_as_progression()
            ensure_section()
            cur_section["lines"].append({"type": "lyric", "text": txt,
                                         "chords": [], "confidence": "high"})
            chart_started = True
            continue
        chord_count = sum(is_chord_token(token) for token in tokens)
        if is_decorated_chord_line(txt):
            flush_pending_as_progression()
            ensure_section()
            analysis = ordered_chord_analysis(txt)
            cur_section["lines"].append({"type": "raw", "text": txt})
            if analysis["spans"] and analysis["unresolved_tokens"]:
                unresolved = analysis["unresolved_tokens"]
                reason = ("unsupported_chord_like_token:" + ",".join(
                    token["text"] for token in unresolved)
                    )
                cur.warnings.append({
                    "code": "ordered_chord_token_unresolved",
                    "section": len(cur.chart_content["sections"]),
                    "line": len(cur_section["lines"]),
                    "reason": reason,
                    "paragraph": p.source_index,
                    "span": p.source_span_index,
                })
            chart_started = True
            continue
        has_repeat = _PROGRESSION_REPEAT_RE.search(stripped) is not None
        if (tokens and chord_count > 0
                and ((has_repeat and chord_count >= 1)
                     or (chord_count >= 2
                         and chord_count >= len(tokens) // 2))):
            flush_pending_as_progression()
            ensure_section()
            cur_section["lines"].append(parse_progression(None, txt))
            chart_started = True
            continue

        # ---- lyric line ----
        ensure_section()
        if pending_chord_line is not None:
            chord_text = pending_chord_line.text_spaces
            if _TAB in pending_chord_line.text:
                if pending_chord_line.tab_layout_error:
                    # The chord row already emitted its reason-coded diagnostic.
                    pass
                elif p.tab_layout_error and _TAB not in p.text:
                    cur.warnings.append({
                        "code": "word_tab_placement_unresolved",
                        "section": len(cur.chart_content["sections"]),
                        "line": len(cur_section["lines"]) + 1,
                        "reason": p.tab_layout_error,
                        "paragraph": p.source_index,
                        "span": p.source_span_index,
                    })
                elif pending_chord_line.physical_positions is None or p.cell_twips is None:
                    cur.warnings.append({
                        "code": "word_tab_placement_unresolved",
                        "section": len(cur.chart_content["sections"]),
                        "line": len(cur_section["lines"]) + 1,
                        "reason": "unsupported_paired_lyric_font_metrics",
                        "paragraph": p.source_index,
                        "span": p.source_span_index,
                        "paired_paragraph": pending_chord_line.source_index,
                    })
                else:
                    chord_text, mapping_error = _retarget_chord_columns(
                        chord_text, pending_chord_line.physical_positions, p.cell_twips)
                    if mapping_error:
                        cur.warnings.append({
                            "code": "word_tab_placement_unresolved",
                            "section": len(cur.chart_content["sections"]),
                            "line": len(cur_section["lines"]) + 1,
                            "reason": mapping_error,
                            "paragraph": p.source_index,
                            "span": p.source_span_index,
                            "paired_paragraph": pending_chord_line.source_index,
                        })
                        chord_text = pending_chord_line.text_spaces
            cur_section["lines"].append(
                pair_space_aligned(chord_text, txt))
            chart_started = True
            pending_chord_line = None
        else:
            # lyric with no chords above it
            cur_section["lines"].append(
                {"type": "lyric", "text": clean_text(txt), "chords": [],
                 "confidence": "high"})
            chart_started = True

    # end: flush any trailing pending chord line
    if unresolved_preheading is not None:
        raise ValueError(
            "pre_heading_chart_content_unresolved "
            f"paragraph={unresolved_preheading.source_index} "
            f"span={unresolved_preheading.source_span_index or 1}")
    flush_generated()
    # Use the same ordered/literal decision for a final chord-only paragraph
    # as for one flushed by a section or song boundary.  The old special-case
    # progression conversion reordered punctuation and collapsed spacing.
    flush_pending_as_progression()
    if cur_section and cur_section["lines"] and cur_section["lines"][-1].get("type") == "spacer":
        cur_section["lines"].pop()

    for song in songs:
        _link_literal_chord_rows(song.chart_content)
        body, error = plain_text_chart_body(
            song.chart_content, song_inline_tails.get(id(song), {}))
        song.chart_source = body
        if error:
            song.warnings.append({
                "code": "plain_text_body_unresolved",
                "section": "?",
                "line": "?",
                "reason": error,
            })
        elif _derive_plain_body and body is not None:
            ordinary_paragraphs = [
                _round_trip_heading(),
                *(Paragraph(line, None) for line in body.splitlines()),
            ]
            reparsed = parse_songs(
                ordinary_paragraphs, source_document=None, cover_genre=None,
                _derive_plain_body=False, _parse_opening_metadata=False)
            if len(reparsed) != 1 or reparsed[0].chart_content != song.chart_content:
                # A source tail is first tried on its original line. If that
                # spelling changes the structure, accept separate label/body
                # lines only when they pass the same complete equality check.
                if song_inline_tails.get(id(song)):
                    separated, separated_error = plain_text_chart_body(song.chart_content)
                    if separated_error is None and separated is not None:
                        separate_paragraphs = [
                            _round_trip_heading(),
                            *(Paragraph(line, None) for line in separated.splitlines()),
                        ]
                        separate_result = parse_songs(
                            separate_paragraphs, source_document=None, cover_genre=None,
                            _derive_plain_body=False, _parse_opening_metadata=False)
                        if (len(separate_result) == 1 and
                                separate_result[0].chart_content == song.chart_content):
                            song.chart_source = separated
                            song.chart_content = separate_result[0].chart_content
                            continue
                section, line = _first_chart_difference(
                    song.chart_content,
                    reparsed[0].chart_content if len(reparsed) == 1 else {"sections": []})
                song.warnings.append({
                    "code": "plain_text_body_unresolved",
                    "section": section,
                    "line": line,
                    "reason": "ordinary_text_roundtrip_mismatch",
                })
            else:
                song.chart_content = reparsed[0].chart_content
    return songs


def parse_docx(docx_path: str, cover_genre: Optional[str] = None) -> list[Song]:
    import os
    paras = load_paragraphs(docx_path)
    return parse_songs(paras, source_document=os.path.basename(docx_path),
                       cover_genre=cover_genre)


def parse_ordinary_chart_source(source: str) -> dict:
    """Return the canonical chart structure represented by ordinary text.

    Confidence is part of the returned structure: callers comparing this value
    with persisted chart_content must retain and compare confidence fields.
    """
    paragraphs = [_round_trip_heading()]
    paragraphs.extend(Paragraph(line, None) for line in source.splitlines())
    songs = parse_songs(paragraphs, _derive_plain_body=False,
                        _parse_opening_metadata=False)
    if len(songs) != 1:
        raise ValueError("ordinary_source_song_boundaries")
    return songs[0].chart_content


def song_to_dict(s: Song) -> dict:
    d = asdict(s)
    return d


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "Sample.docx"
    songs = parse_docx(path)
    print(json.dumps([song_to_dict(s) for s in songs], indent=2, ensure_ascii=False))
