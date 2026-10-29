/*
 * chartParser.js — parse editable chart TEXT into the structured chart_content
 * shape (the same shape the Python migration parser produces and that
 * transpose.js / the renderer consume).
 *
 * This is the save-time half of "dual storage": the user edits chart_source
 * (text); on save the server calls parseChartBody() to regenerate the
 * structured chart_content. Shared Node/browser via a UMD wrapper.
 *
 * Scope note: unlike the Python parser (which parses whole .docx incl. title
 * and BB lines), this parses ONE song's chart BODY — the title/artist/keys/
 * bpm/BB are separate form fields in the editor. So it handles sections,
 * chord-over-lyric lines, inline {C}brace lines, progressions, and raw lines.
 *
 * Output: { sections: [ { label, lines: [ line, ... ] } ] }
 *   lyric line       : { type:"lyric", text, chords:[{sym,pos}], confidence }
 *   progression line : { type:"progression", chords:[...], repeat?, note? }
 *   raw line         : { type:"raw", text }
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ChartParser = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // A chord token: root + optional quality/extensions + optional slash bass.
  const CHORD_RE =
    /^[A-G][#b]?(m|min|maj|sus|dim|aug|add|M)?\d*(sus|add|dim|aug|maj|b|#|\/)*[0-9#b]*(\/[A-G][#b]?)?$/;

  const BRACKET_SECTION_RE = /^\[([^\]]+)\](.*)$/;
  const BARE_SECTION_RE =
    /^(Intro|Outro|Verse|Chorus|Bridge|Pre-?Chorus|Interlude|Instrumental|Solo|Link|Middle|Refrain|Tag|Ending|Coda|Hook|Breakdown|Turnaround)(\s*\d+)?\s*:?\s*$/i;
  const BARE_SECTION_COLON_RE =
    /^(Intro|Outro|Verse|Chorus|Bridge|Pre-?Chorus|Interlude|Instrumental|Solo|Link|Middle|Refrain|Tag|Ending|Coda|Hook|Breakdown|Turnaround)(\s*\d+)?\s*:\s*(.+)$/i;
  const INLINE_BRACE_RE = /\{([^}]*)\}/g;
  const TAB_LINE_RE = /^[eEADGBb]?\s*[-|]{2,}/;
  const REPEAT_RE = /\(?\s*(\d+)\s*[xX]\)?|\b[xX]\s*(\d+)\b/;
  // A "(Chorus)" / "(Chorus) x2" repeat marker: parens naming a section word.
  const REPEAT_MARKER_RE = /^\(([^)]+?)\)\s*(?:[xX]\s*(\d+))?\s*$/;
  const SECTION_WORD_RE =
    /^(Intro|Outro|Verse|Chorus|Bridge|Pre-?Chorus|Interlude|Instrumental|Solo|Link|Middle|Refrain|Tag|Ending|Coda|Hook)(\s*\d+)?$/i;

  function isChord(tok) {
    return CHORD_RE.test(tok);
  }
  function clean(s) {
    return (s || "").replace(/''/g, "'");
  }
  function isChordOnly(text) {
    const toks = text.trim().split(/\s+/).filter(Boolean);
    return toks.length > 0 && toks.every(isChord);
  }

  const ORDERED_ROW_SEPARATORS = "-\u2013\u2014/\\*|,;:";
  const CHORD_LIKE_TOKEN_RE = /^[A-G][#b]?(?:m|min|maj|sus|dim|aug|add|M)?\d*(?:[#b].*|\/(?:[A-G][#b]?(?:\d.*|[#b].*)))$/;

  function isFullyParenthesized(text) {
    const start = text.search(/\S/);
    const end = text.search(/\s*$/);
    const last = end - 1;
    if (start < 0 || text[start] !== "(" || start > last) return false;
    let depth = 0;
    for (let index = start; index <= last; index += 1) {
      if (text[index] === "(") depth += 1;
      else if (text[index] === ")") {
        depth -= 1;
        if (depth < 0 || (depth === 0 && index !== last)) return false;
      }
    }
    return depth === 0;
  }

  function decoratedChordAnalysis(text) {
    if (typeof text !== "string" || isTab(text.trim())) {
      return { spans: [], ambiguous: true };
    }
    if (/\d+%/.test(text)) {
      return { spans: [], ambiguous: false, unresolvedTokens: [], proseTokens: [],
        partialTransposition: false, literal: false };
    }
    const spans = [];
    let visibleTokens = 0, opaqueTokens = 0;
    const unresolvedTokens = [], proseTokens = [];
    let literalSyntax = false, repeatedSpace = false;
    const opaqueOnly = isFullyParenthesized(text);
    let index = 0;
    while (index < text.length) {
      const char = text[index];
      if (/\s/.test(char)) {
        let end = index + 1;
        while (end < text.length && /\s/.test(text[end])) end += 1;
        if (end - index > 1) repeatedSpace = true;
        index = end;
        continue;
      }
      if (char === "(" || char === "[") {
        const close = char === "(" ? ")" : "]";
        let depth = 1;
        index += 1;
        literalSyntax = true;
        while (index < text.length && depth > 0) {
          if (text[index] === char) depth += 1;
          else if (text[index] === close) depth -= 1;
          index += 1;
        }
        continue;
      }
      const separatorsWithoutSlash = ORDERED_ROW_SEPARATORS.replace("/", "");
      if (separatorsWithoutSlash.includes(char) || char === "/"
          || char === ")" || char === "]") {
        literalSyntax = true;
        index += 1;
        continue;
      }
      if (char === "%") {
        const before = text.slice(0, index).trimEnd();
        const after = text.slice(index + 1).trimStart();
        if (before.endsWith("|") && after.startsWith("|")) {
          literalSyntax = true;
          index += 1;
          continue;
        }
      }
      let end = index + 1;
      while (end < text.length && !/\s/.test(text[end])
          && !`${separatorsWithoutSlash}()[]`.includes(text[end])) end += 1;
      const token = text.slice(index, end);
      if (isChord(token)) spans.push({ start: index, end, sym: token });
      else if (/^(?:\d+[xX]?|[xX]\d+)$/.test(token)) literalSyntax = true;
      else {
        opaqueTokens += 1;
        const item = { start: index, end, text: token };
        if (CHORD_LIKE_TOKEN_RE.test(token)) unresolvedTokens.push(item);
        else proseTokens.push(item);
      }
      visibleTokens += 1;
      index = end;
    }
    const eligible = spans.length >= 2 && spans.length >= Math.ceil(visibleTokens / 2);
    const partialTransposition = eligible && unresolvedTokens.length > 0
      && proseTokens.length === 0 && !opaqueOnly;
    return {
      spans: eligible ? spans : [],
      ambiguous: opaqueTokens > 0 || opaqueOnly,
      unresolvedTokens,
      proseTokens,
      partialTransposition,
      literal: opaqueOnly || (eligible && (literalSyntax || repeatedSpace)),
    };
  }

  function decoratedChordTokenSpans(text) {
    return decoratedChordAnalysis(text).spans;
  }

  function isDecoratedChordLine(text) {
    return decoratedChordAnalysis(text).literal === true;
  }

  function transposeDecoratedRawLines(chart, transposeChord) {
    if (!chart || !Array.isArray(chart.sections) || typeof transposeChord !== "function") return chart;
    let changed = false;
    const sections = chart.sections.map(section => {
      let sectionChanged = false;
      const lines = (section.lines || []).map(line => {
        if (line.type !== "raw") return line;
        const analysis = decoratedChordAnalysis(line.text || "");
        const spans = analysis.spans;
        if (!spans.length) return line;
        if (analysis.ambiguous && !analysis.partialTransposition) {
          if (!analysis.unresolvedTokens.length) return line;
          changed = true;
          sectionChanged = true;
          return { ...line, transposition_status: "unresolved" };
        }
        let text = line.text;
        for (let i = spans.length - 1; i >= 0; i -= 1) {
          const span = spans[i];
          const replacement = transposeChord(span.sym);
          text = text.slice(0, span.start) + replacement + text.slice(span.end);
        }
        const status = analysis.partialTransposition ? "partial" : "complete";
        const updatedSpans = line.paired_to_next
          ? decoratedChordAnalysis(text).spans.map(span => ({
            symbol: span.sym, start: span.start, end: span.end,
          }))
          : null;
        changed = true;
        sectionChanged = true;
        return { ...line, text,
          ...(updatedSpans ? { chord_spans: updatedSpans } : {}),
          transposition_status: status };
      });
      return sectionChanged ? { ...section, lines } : section;
    });
    return changed ? { ...chart, sections } : chart;
  }
  function isTab(text) {
    const t = text.trim();
    if (TAB_LINE_RE.test(t)) return true;
    if (t.length >= 6) {
      const sym = [...t].filter((c) => "-|0123456789".includes(c)).length;
      if (sym / t.length > 0.6) return true;
    }
    return false;
  }

  function parseInlineBrace(text) {
    const chords = [];
    let lyric = "";
    let last = 0;
    let m;
    INLINE_BRACE_RE.lastIndex = 0;
    while ((m = INLINE_BRACE_RE.exec(text))) {
      lyric += text.slice(last, m.index);
      const sym = m[1].trim();
      if (sym) chords.push({ sym: clean(sym), pos: lyric.length });
      last = m.index + m[0].length;
    }
    lyric += text.slice(last);
    return { type: "lyric", text: clean(lyric), chords, confidence: "high" };
  }

  function pairSpaceAligned(chordLine, lyricLine) {
    const chords = [];
    const re = /\S+/g;
    let m;
    while ((m = re.exec(chordLine))) {
      if (!isChord(m[0])) continue;
      chords.push({ sym: clean(m[0]), pos: m.index });
    }
    return { type: "lyric", text: clean(lyricLine),
             chords, confidence: "low" };
  }

  function parseProgression(chordPart) {
    let repeat = null, note = null;
    const rm = REPEAT_RE.exec(chordPart);
    if (rm) {
      repeat = parseInt(rm[1] || rm[2], 10);
      chordPart = chordPart.slice(0, rm.index) + " " + chordPart.slice(rm.index + rm[0].length);
    }
    const chords = [], trailing = [];
    for (const t of chordPart.split(/\s+/).filter(Boolean)) {
      if (isChord(t)) chords.push(clean(t));
      else trailing.push(t);
    }
    const out = { type: "progression", chords };
    if (repeat) out.repeat = repeat;
    if (trailing.length) out.note = clean(trailing.join(" "));
    return out;
  }

  function parseChartBody(text) {
    const lines = (text || "").split(/\r?\n/);
    const sections = [];
    let cur = null;
    let pendingChord = null;

    const ensure = () => { if (!cur) { cur = { label: null, lines: [] }; sections.push(cur); } };
    const flushPending = () => {
      if (pendingChord !== null) {
        ensure();
        if (isDecoratedChordLine(pendingChord)) cur.lines.push({ type: "raw", text: pendingChord });
        else cur.lines.push(parseProgression(pendingChord));
        pendingChord = null;
      }
    };
    const newSection = (label) => {
      if (cur?.lines.at(-1)?.type === "spacer") cur.lines.pop();
      cur = { label: label ? clean(label) : null, lines: [] };
      sections.push(cur);
    };

    for (const rawLine of lines) {
      // Keep source whitespace on lyric and raw lines. It is part of the
      // editable body; classification uses a trimmed view separately.
      const line = rawLine;
      const s = line.trim();

      if (!s) {
        flushPending();
        if (cur && (cur.lines.length > 0 || cur.label)
            && cur.lines.at(-1)?.type !== "spacer") {
          cur.lines.push({ type: "spacer" });
        }
        continue;
      }

      if (isTab(s)) { flushPending(); ensure(); cur.lines.push({ type: "raw", text: line }); continue; }

      // "(Chorus)" / "(Chorus) x2" repeat marker (a reference, not a section def)
      const rmk = REPEAT_MARKER_RE.exec(s);
      if (rmk && SECTION_WORD_RE.test(rmk[1].trim()) && !isChordOnly(s)) {
        flushPending();
        ensure();
        const marker = { type: "repeat", ref: clean(rmk[1].trim()) };
        if (rmk[2]) marker.times = parseInt(rmk[2], 10);
        cur.lines.push(marker);
        continue;
      }

      let bm = BRACKET_SECTION_RE.exec(s);
      if (bm) {
        flushPending();
        newSection(bm[1].trim());
        const closeIndex = line.indexOf("]");
        const trailing = closeIndex >= 0 ? line.slice(closeIndex + 1) : bm[2];
        if (trailing) {
          if (/\d+%/.test(trailing)) {
            cur.lines.push({ type: "lyric", text: trailing, chords: [], confidence: "high" });
            continue;
          }
          if (isDecoratedChordLine(trailing)) cur.lines.push({ type: "raw", text: trailing });
          else if (decoratedChordAnalysis(trailing).proseTokens?.length) cur.lines.push({ type: "raw", text: trailing });
          else if (trailing.trim().split(/\s+/).some(isChord)) cur.lines.push(parseProgression(trailing));
          else cur.lines.push({ type: "raw", text: trailing });
        }
        continue;
      }

      const bc = BARE_SECTION_COLON_RE.exec(s);
      if (bc) {
        const tail = bc[3].trim();
        if (/\d+%/.test(tail)) {
          flushPending();
          newSection((bc[1] + (bc[2] || "")).trim());
          const colonIndex = line.indexOf(":");
          cur.lines.push({ type: "lyric", text: line.slice(colonIndex + 1),
            chords: [], confidence: "high" });
          continue;
        }
        const toks = tail.split(/[,\s]+/).filter(Boolean);
        const chordish = toks.filter(isChord).length;
        if (toks.length && chordish >= Math.max(1, Math.floor(toks.length / 2))) {
          flushPending();
          newSection((bc[1] + (bc[2] || "")).trim());
          const colonIndex = line.indexOf(":");
          const literalTail = colonIndex >= 0 ? line.slice(colonIndex + 1) : tail;
          if (isDecoratedChordLine(literalTail)) cur.lines.push({ type: "raw", text: literalTail });
          else cur.lines.push(parseProgression(tail.replace(/,/g, " ")));
          continue;
        }
      }

      if (BARE_SECTION_RE.test(s)) {
        flushPending();
        newSection(s.replace(/:\s*$/, "").trim());
        continue;
      }

      if (/\{[^}]*\}/.test(line)) { flushPending(); ensure(); cur.lines.push(parseInlineBrace(line)); continue; }

      if (isChordOnly(s)) { flushPending(); pendingChord = line; continue; }

      if (isDecoratedChordLine(line)) {
        flushPending(); ensure(); cur.lines.push({ type: "raw", text: line }); continue;
      }

      // A bare line that's mostly chords plus a repeat/annotation (e.g.
      // "G D C  4x") is a progression, not a lyric.
      const toks = s.split(/\s+/).filter(Boolean);
      if (/\d+%/.test(line)) {
        flushPending(); ensure();
        cur.lines.push({ type: "lyric", text: line, chords: [], confidence: "high" });
        continue;
      }
      const chordCount = toks.filter(isChord).length;
      const hasRepeat = REPEAT_RE.test(s);
      if (toks.length > 0 && chordCount > 0 &&
          ((hasRepeat && chordCount >= 1) ||
           (chordCount >= 2 && chordCount >= Math.floor(toks.length / 2)))) {
        flushPending();
        ensure();
        cur.lines.push(parseProgression(line));
        continue;
      }

      // lyric line
      ensure();
      if (pendingChord !== null) {
        cur.lines.push(pairSpaceAligned(pendingChord, line));
        pendingChord = null;
      } else {
        cur.lines.push({ type: "lyric", text: clean(line), chords: [], confidence: "high" });
      }
    }
    flushPending();
    if (cur?.lines.at(-1)?.type === "spacer") cur.lines.pop();
    linkLiteralChordRows({ sections });
    return { sections };
  }

  function linkLiteralChordRows(chart) {
    for (const section of chart.sections || []) {
      const lines = section.lines || [];
      for (let index = 0; index < lines.length - 1; index += 1) {
        const line = lines[index];
        if (line.type !== 'raw' || lines[index + 1].type !== 'lyric' || isTab(line.text || '')) continue;
        const analysis = decoratedChordAnalysis(line.text || '');
        if (analysis.spans.length && analysis.proseTokens.length === 0) {
          line.paired_to_next = true;
          line.chord_spans = analysis.spans.map(span => ({
            symbol: span.sym, start: span.start, end: span.end,
          }));
        }
      }
    }
    return chart;
  }

  return {
    parseChartBody,
    isDecoratedChordLine, decoratedChordTokenSpans, decoratedChordAnalysis,
    transposeDecoratedRawLines,
    _isChord: isChord,
  };
});
