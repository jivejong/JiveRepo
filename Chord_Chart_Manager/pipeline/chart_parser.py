"""
chart_parser.py — Parse chord-chart .docx files into structured song JSON.

Design goals (from the schema + normalization decisions):
  * One doc = one genre binder. An optional cover page names the genre;
    it becomes each song's PRIMARY genre.
  * Titles are detected by CONTENT pattern ("Title – Key ... Artist"),
    not by paragraph style, because some titles are Normal-styled.
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
#   - is_bold  : whether the paragraph's runs are bold (weak title signal)
# We keep tabs as a private sentinel char first, then expand to spaces for
# column math — but only AFTER we've used tab structure to split title fields.

_TAB = "\x01"       # private sentinel standing in for <w:tab/>
_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

# Matches a whole paragraph block
_P_RE = re.compile(r"<w:p\b.*?</w:p>", re.DOTALL)
_PSTYLE_RE = re.compile(r'<w:pStyle w:val="([^"]+)"')
# Ordered stream of the run-level things we care about inside a paragraph:
_RUNBIT_RE = re.compile(r"<w:tab\b|<w:br\b[^>]*/>|<w:cr\b[^>]*/>|<w:t[ >].*?</w:t>", re.DOTALL)
_BOLD_RE = re.compile(r"<w:b/>|<w:b ")


@dataclass
class Paragraph:
    text: str                 # tabs as _TAB sentinel, runs joined
    style: Optional[str]
    is_bold: bool

    @property
    def text_spaces(self) -> str:
        """Text with tabs expanded to a single space (for column math)."""
        return self.text.replace(_TAB, " ")

    @property
    def stripped(self) -> str:
        return self.text_spaces.strip()


def _unescape(s: str) -> str:
    return (s.replace("&amp;", "&").replace("&lt;", "<")
             .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'"))


def _column_length(text: str) -> int:
    """Chart columns count UTF-16 code units, matching JavaScript string indices."""
    return len(text.encode("utf-16-le")) // 2


def load_paragraphs(docx_path: str) -> list[Paragraph]:
    with zipfile.ZipFile(docx_path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    out: list[Paragraph] = []
    for pblock in _P_RE.findall(xml):
        style_m = _PSTYLE_RE.search(pblock)
        style = style_m.group(1) if style_m else None
        is_bold = bool(_BOLD_RE.search(pblock))
        parts: list[str] = []
        for bit in _RUNBIT_RE.findall(pblock):
            if bit.startswith("<w:tab"):
                parts.append(_TAB)
            elif bit.startswith("<w:br"):
                # Only text-wrapping breaks belong to chart text. Page and
                # column breaks are document layout controls, not blank rows.
                kind = re.search(r"\bw:type=[\"']([^\"']+)[\"']", bit)
                if not kind or kind.group(1) == "textWrapping":
                    parts.append("\n")
            elif bit.startswith("<w:cr"):
                parts.append("\n")
            else:
                inner = re.sub(r"<[^>]+>", "", bit)
                parts.append(_unescape(inner))
        out.append(Paragraph("".join(parts), style, is_bold))
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
# 3. Title-line detection & field parsing
# --------------------------------------------------------------------------
# Shape: Title – Key [(AltKey)] <tabs> Artist [<tabs> OriginalKey]
# The em-dash (–) separates title from the metadata tail. Fields in the tail
# are tab-separated but the tab COUNT is inconsistent, so we split on runs of
# tabs and classify each token as key-like or name-like.

_EMDASH = "\u2013"   # –
# A chord/key token: root note, optional accidental, optional quality/min.
_KEY_TOKEN_RE = re.compile(r"^[A-G][#b]?(m|min|maj|sus|dim|aug|add)?\d*$")
# Alt key parenthetical, e.g. "(E)" or "(B)"
_ALTKEY_RE = re.compile(r"\(([A-G][#b]?m?)\)")


def looks_like_title(p: Paragraph) -> bool:
    """
    Content heuristic (works whether or not the paragraph is Heading1):
    a single line containing an em-dash, whose pre-dash part is short-ish
    text and whose post-dash tail begins with a key-like token.
    """
    txt = p.stripped
    if _EMDASH not in txt:
        return False
    left, right = txt.split(_EMDASH, 1)
    left = left.strip()
    right = right.strip()
    if not left or not right:
        return False
    # Left side (the actual title) shouldn't look like a lyric sentence —
    # titles here are short. Guard against a lyric that happens to contain "–".
    if len(left) > 60:
        return False
    # First token after the dash should be key-like (strip any "(Alt)" first).
    first = _ALTKEY_RE.sub("", right).strip().split()
    if not first:
        return False
    return bool(_KEY_TOKEN_RE.match(first[0]))


@dataclass
class TitleInfo:
    title: str
    performance_key: Optional[str]
    alt_key: Optional[str]
    artist: Optional[str]
    original_key: Optional[str]


def parse_title(p: Paragraph) -> TitleInfo:
    """
    Split on the em-dash, then split the tail on tab-runs into fields.
    Classify: first field = perf key (+ optional (alt)); a trailing
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
        # First field holds the performance key and possibly "(AltKey)".
        head = fields[0]
        am = _ALTKEY_RE.search(head)
        if am:
            alt_key = am.group(1)
            head = _ALTKEY_RE.sub("", head).strip()
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

    return TitleInfo(
        title=clean_text(title),
        performance_key=perf_key,
        alt_key=alt_key,
        artist=clean_text(artist) if artist else None,
        original_key=orig_key,
    )


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


def is_chord_token(tok: str) -> bool:
    return bool(_CHORD_RE.match(tok))


def is_chord_only_line(text: str) -> bool:
    """True if every whitespace-separated token is a chord (the classic
    chord line that sits above a lyric line)."""
    toks = text.split()
    if not toks:
        return False
    return all(is_chord_token(t) for t in toks)


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
    return text.startswith(_GENERATED_HEADER + "\n") or text.startswith(_LEGACY_GENERATED_HEADER + "\n")


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
    return {"type": "lyric", "text": clean_text(lyric_line.rstrip()),
            "chords": chords, "confidence": "low"}


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
    warnings: list[str] = field(default_factory=list)


def _new_section(label: Optional[str]) -> dict:
    return {"label": label, "lines": []}


def parse_songs(paragraphs: list[Paragraph],
                source_document: Optional[str] = None,
                cover_genre: Optional[str] = None) -> list[Song]:
    """
    Walk the paragraph stream, splitting into songs at each title line and
    classifying every body line into the normalized chart_content model.
    """
    songs: list[Song] = []
    cur: Optional[Song] = None
    cur_section: Optional[dict] = None
    pending_chord_line: Optional[str] = None   # a chord-only line awaiting its lyric
    generated_mode = False
    generated_header = _GENERATED_HEADER
    generated_lines: list[str] = []
    chart_started = False

    def flush_pending_as_progression():
        """If a chord-only line had no lyric under it, it's a progression."""
        nonlocal pending_chord_line, chart_started
        if pending_chord_line is not None and cur is not None:
            ensure_section()
            cur_section["lines"].append(
                parse_progression(None, pending_chord_line))
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

    def start_section(label: Optional[str]):
        nonlocal cur_section, chart_started
        if cur_section and cur_section["lines"] and cur_section["lines"][-1].get("type") == "spacer":
            cur_section["lines"].pop()
        cur_section = _new_section(label)
        cur.chart_content["sections"].append(cur_section)
        chart_started = True

    expanded_paragraphs = []
    for p in paragraphs:
        parts = p.text.split("\n")
        expanded_paragraphs.extend(Paragraph(part, p.style, p.is_bold) for part in parts)
    for p in expanded_paragraphs:
        raw_text = p.text_spaces
        txt = raw_text if generated_mode else raw_text.rstrip()
        stripped = txt.strip()

        # ---- title line: starts a new song ----
        if looks_like_title(p):
            flush_pending_as_progression()
            flush_generated()
            if cur_section and cur_section["lines"] and cur_section["lines"][-1].get("type") == "spacer":
                cur_section["lines"].pop()
            ti = parse_title(p)
            cur = Song(
                title=ti.title, artist=ti.artist,
                performance_key=ti.performance_key,
                original_key=ti.original_key, alt_key=ti.alt_key,
                capo_fret=0, bpm=None, bb_structure=None,
                primary_genre=cover_genre, source_document=source_document,
            )
            songs.append(cur)
            cur_section = None
            chart_started = False
            continue

        if cur is None:
            # Content before the first title (e.g. a genre cover page).
            # If we weren't given a cover_genre and this is the very first
            # non-empty line, remember it as the genre.
            if stripped and cover_genre is None:
                cover_genre = stripped
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
            body = stripped.split(":", 1)[1].strip()
            capo_m = _CAPO_RE.search(body)
            if capo_m:
                cur.capo_fret = int(capo_m.group(1))
                body = _CAPO_RE.sub("", body).strip(" /").strip()
            kb_m = _KEY_BPM_RE.search(body)
            if kb_m:
                # BPM always comes from here; original key only fills in if the
                # title line didn't already provide one (title wins on conflict).
                cur.bpm = int(kb_m.group(2))
                if not cur.original_key:
                    cur.original_key = kb_m.group(1)
                body = _KEY_BPM_RE.sub("", body).strip(" /").strip()
            cur.bb_structure = clean_text(body) if body else None
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
            trailing = bm.group(2).strip()
            # repeat-only paren like "(Chorus)"? handled below in bare branch;
            # here bracket label always defines/starts a section.
            start_section(clean_text(label))
            if trailing:
                # e.g. "[Intro] G D C (4x)" — a progression on the same line
                if any(is_chord_token(t) for t in trailing.split()):
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
            # Only treat the tail as a progression if it's chord-ish; otherwise
            # it's likely a lyric ("Intro:" rarely precedes lyrics, but guard).
            tail_toks = re.split(r"[,\s]+", tail)
            if tail_toks and sum(is_chord_token(t) for t in tail_toks if t) >= max(1, len(tail_toks)//2):
                flush_pending_as_progression()
                start_section(clean_text(label))
                # commas are just separators inside these inline progressions
                cur_section["lines"].append(
                    parse_progression(None, tail.replace(",", " ")))
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

        # ---- chord-only line: hold it; it pairs with the next lyric ----
        if is_chord_only_line(stripped):
            flush_pending_as_progression()   # two chord lines in a row -> first is a progression
            pending_chord_line = txt
            continue

        # ---- lyric line ----
        ensure_section()
        if pending_chord_line is not None:
            cur_section["lines"].append(
                pair_space_aligned(pending_chord_line, txt))
            chart_started = True
            pending_chord_line = None
        else:
            # lyric with no chords above it
            cur_section["lines"].append(
                {"type": "lyric", "text": clean_text(txt), "chords": [],
                 "confidence": "high"})
            chart_started = True

    # end: flush any trailing pending chord line
    flush_generated()
    if pending_chord_line is not None and cur is not None:
        if cur_section is None:
            cur_section = _new_section(None)
            cur.chart_content["sections"].append(cur_section)
        cur_section["lines"].append(parse_progression(None, pending_chord_line))
    if cur_section and cur_section["lines"] and cur_section["lines"][-1].get("type") == "spacer":
        cur_section["lines"].pop()

    return songs


def parse_docx(docx_path: str, cover_genre: Optional[str] = None) -> list[Song]:
    import os
    paras = load_paragraphs(docx_path)
    return parse_songs(paras, source_document=os.path.basename(docx_path),
                       cover_genre=cover_genre)


def song_to_dict(s: Song) -> dict:
    d = asdict(s)
    return d


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "Sample.docx"
    songs = parse_docx(path)
    print(json.dumps([song_to_dict(s) for s in songs], indent=2, ensure_ascii=False))
