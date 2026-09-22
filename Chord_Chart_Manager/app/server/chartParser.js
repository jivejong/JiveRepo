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
      chords.push({ sym: clean(m[0]), pos: Math.min(m.index, lyricLine.length) });
    }
    return { type: "lyric", text: clean(lyricLine.replace(/\s+$/, "")),
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
        cur.lines.push(parseProgression(pendingChord));
        pendingChord = null;
      }
    };
    const newSection = (label) => { cur = { label: label ? clean(label) : null, lines: [] }; sections.push(cur); };

    for (const rawLine of lines) {
      const line = rawLine.replace(/\s+$/, "");
      const s = line.trim();

      if (!s) { flushPending(); continue; }

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
        const trailing = bm[2].trim();
        if (trailing) {
          if (trailing.split(/\s+/).some(isChord)) cur.lines.push(parseProgression(trailing));
          else cur.lines.push({ type: "raw", text: trailing });
        }
        continue;
      }

      const bc = BARE_SECTION_COLON_RE.exec(s);
      if (bc) {
        const tail = bc[3].trim();
        const toks = tail.split(/[,\s]+/).filter(Boolean);
        const chordish = toks.filter(isChord).length;
        if (toks.length && chordish >= Math.max(1, Math.floor(toks.length / 2))) {
          flushPending();
          newSection((bc[1] + (bc[2] || "")).trim());
          cur.lines.push(parseProgression(tail.replace(/,/g, " ")));
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

      // A bare line that's mostly chords plus a repeat/annotation (e.g.
      // "G D C  4x") is a progression, not a lyric.
      const toks = s.split(/\s+/).filter(Boolean);
      const chordCount = toks.filter(isChord).length;
      const hasRepeat = REPEAT_RE.test(s);
      if (toks.length > 0 && chordCount > 0 &&
          chordCount >= toks.length - 1 && (hasRepeat || chordCount === toks.length)) {
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
    return { sections };
  }

  /* Inverse: render structured chart_content back to editable text. Used to
   * seed chart_source for migrated songs that only have structured content. */
  function chartToText(chart) {
    if (!chart || !Array.isArray(chart.sections)) return "";
    const out = [];
    for (const sec of chart.sections) {
      if (sec.label) out.push(`[${sec.label}]`);
      for (const line of sec.lines || []) {
        if (line.type === "lyric") {
          if (line.chords && line.chords.length) {
            let row = "";
            for (const c of [...line.chords].sort((a, b) => a.pos - b.pos)) {
              const pos = Math.max(0, c.pos | 0);
              row += pos < row.length ? " " + c.sym : " ".repeat(pos - row.length) + c.sym;
            }
            out.push(row);
          }
          out.push(line.text || "");
        } else if (line.type === "progression") {
          let l = (line.chords || []).join("  ");
          if (line.repeat) l += `  ${line.repeat}x`;
          if (line.note) l += `  ${line.note}`;
          out.push(l);
        } else if (line.type === "raw") {
          out.push(line.text || "");
        } else if (line.type === "repeat") {
          out.push(`(${line.ref}${line.times ? ") x" + line.times : ")"}`);
        }
      }
      out.push("");
    }
    return out.join("\n").replace(/\n+$/, "\n");
  }

  return { parseChartBody, chartToText, _isChord: isChord };
});
