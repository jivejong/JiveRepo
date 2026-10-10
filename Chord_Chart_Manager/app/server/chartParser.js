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
  const GENERATED_HEADER = "[[CCM-CHART:2]]";
  const LEGACY_GENERATED_HEADER = "[[CCM-CHART:1]]";

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
      chords.push({ sym: clean(m[0]), pos: m.index });
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
    if (lines[0] === GENERATED_HEADER || lines[0] === LEGACY_GENERATED_HEADER) {
      try { return parseGeneratedChart(lines.slice(1), lines[0] === GENERATED_HEADER); }
      catch (error) { error.status = 400; throw error; }
    }
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
    const newSection = (label) => {
      if (cur?.lines.at(-1)?.type === "spacer") cur.lines.pop();
      cur = { label: label ? clean(label) : null, lines: [] };
      sections.push(cur);
    };

    for (const rawLine of lines) {
      const line = rawLine.replace(/\s+$/, "");
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
    if (cur?.lines.at(-1)?.type === "spacer") cur.lines.pop();
    return { sections };
  }

  function parseGeneratedChordText(text) {
    const chords = [];
    let lyric = "";
    for (let i = 0; i < text.length;) {
      if (text[i] === "\\" && i + 1 < text.length) {
        const escaped = text[i + 1];
        lyric += escaped === "n" ? "\n" : escaped === "r" ? "\r" : escaped;
        i += 2;
        continue;
      }
      if (text.startsWith("[[C:", i)) {
        const end = text.indexOf("]]", i + 4);
        if (end < 0) throw new Error("Generated chart has an incomplete chord anchor marker");
        let sym;
        try { sym = decodeURIComponent(text.slice(i + 4, end)); }
        catch { throw new Error("Generated chart has an invalid chord anchor marker"); }
        if (!isChord(sym)) throw new Error("Generated chart has an invalid chord symbol");
        chords.push({ sym: clean(sym), pos: lyric.length });
        i = end + 2;
        continue;
      }
      lyric += text[i];
      i += 1;
    }
    return { type: "lyric", text: lyric, chords, confidence: "high" };
  }

  function escapeGeneratedText(text) {
    return String(text || "").replace(/\\/g, "\\\\").replace(/\[/g, "\\[")
      .replace(/\r/g, "\\r").replace(/\n/g, "\\n");
  }

  function unescapeGeneratedText(text) {
    return text.replace(/\\([\\\[nr])/g, (_, escaped) =>
      escaped === "n" ? "\n" : escaped === "r" ? "\r" : escaped);
  }

  function parseGeneratedLyricV2(body, confidence) {
    const splitAt = body.lastIndexOf("[[CCM-CHORDS]]");
    if (splitAt < 0) throw new Error("Generated lyric is missing its chord list");
    const lyric = unescapeGeneratedText(body.slice(0, splitAt));
    const encoded = body.slice(splitAt + "[[CCM-CHORDS]]".length);
    const chords = [];
    const marker = /\[\[C:(\d+):([^\]]*)\]\]/g;
    let match;
    let cursor = 0;
    while ((match = marker.exec(encoded))) {
      if (encoded.slice(cursor, match.index).trim()) throw new Error("Generated lyric has invalid chord data");
      let sym;
      try { sym = decodeURIComponent(match[2]); }
      catch { throw new Error("Generated lyric has an invalid chord symbol"); }
      const pos = Number(match[1]);
      if (!Number.isSafeInteger(pos) || pos < 0 || !isChord(sym)) {
        throw new Error("Generated lyric has an invalid chord anchor");
      }
      chords.push({ sym: clean(sym), pos });
      cursor = marker.lastIndex;
    }
    if (encoded.slice(cursor).trim()) throw new Error("Generated lyric has invalid chord data");
    return { type: "lyric", text: lyric, chords, confidence };
  }

  function parseGeneratedChart(lines, version2 = false) {
    const sections = [];
    let current = null;
    if (lines[lines.length - 1] === "") lines = lines.slice(0, -1);
    for (const line of lines) {
      if (line.startsWith("[[CCM-SECTION]]")) {
        if (current?.lines.at(-1)?.type === "spacer") current.lines.pop();
        const label = unescapeGeneratedText(line.slice("[[CCM-SECTION]]".length));
        current = { label: label || null, lines: [] };
        sections.push(current);
        continue;
      }
      if (!current) throw new Error("Generated chart line appears before its section marker");
      let match;
      if ((match = /^\[\[CCM-LYRIC:(high|low)\]\](.*)$/.exec(line))) {
        const parsed = version2
          ? parseGeneratedLyricV2(match[2], match[1])
          : parseGeneratedChordText(match[2]);
        parsed.confidence = match[1];
        current.lines.push(parsed);
      } else if ((match = /^\[\[CCM-PROGRESSION\]\](.*)$/.exec(line))) {
        let body = match[1];
        const noteIndex = body.indexOf("[[CCM-NOTE]]");
        let note;
        if (noteIndex >= 0) {
          note = unescapeGeneratedText(body.slice(noteIndex + "[[CCM-NOTE]]".length));
          body = body.slice(0, noteIndex);
        }
        const repeat = /\[\[CCM-PROGRESSION-REPEAT:(\d+)\]\]$/.exec(body);
        if (repeat) body = body.slice(0, repeat.index);
        const chordTokens = body.trim() ? body.trim().split(/\s+/) : [];
        if (chordTokens.some(token => !isChord(token))) {
          throw new Error("Generated progression contains an invalid chord token");
        }
        const progression = { type: "progression", chords: chordTokens };
        if (repeat) progression.repeat = Number(repeat[1]);
        if (noteIndex >= 0) progression.note = note;
        current.lines.push(progression);
      } else if ((match = /^\[\[CCM-REPEAT(?::(\d+))?\]\](.*)$/.exec(line))) {
        const repeated = { type: "repeat", ref: unescapeGeneratedText(match[2]) };
        if (match[1]) repeated.times = Number(match[1]);
        current.lines.push(repeated);
      } else if (line.startsWith("[[CCM-RAW]]")) {
        current.lines.push({ type: "raw", text: unescapeGeneratedText(line.slice("[[CCM-RAW]]".length)) });
      } else if (line === "[[CCM-SPACER]]") {
        if (current.lines.at(-1)?.type !== "spacer") current.lines.push({ type: "spacer" });
      } else {
        throw new Error("Generated chart contains a missing or unsupported line marker");
      }
    }
    return { sections };
  }

  function isGeneratedSourceUnchanged(existingSource, existingContent, submittedSource) {
    if (typeof submittedSource !== "string") return false;
    if (typeof existingSource === "string" && submittedSource === existingSource) return true;
    return (!existingSource && submittedSource === chartToText(existingContent));
  }

  function isGeneratedChartSource(source) {
    return typeof source === "string" &&
      (source.startsWith(`${GENERATED_HEADER}\n`) || source.startsWith(`${LEGACY_GENERATED_HEADER}\n`));
  }

  function generatedChordText(line) {
    const text = line.text || "";
    const byPosition = new Map();
    for (const chord of line.chords || []) {
      const pos = chord.pos;
      if (!Number.isSafeInteger(pos) || pos < 0) {
        throw new Error("Chord column must be a non-negative safe integer UTF-16 column");
      }
      if (!byPosition.has(pos)) byPosition.set(pos, []);
      byPosition.get(pos).push(chord.sym);
    }
    const anchors = [...byPosition.entries()].sort((a, b) => a[0] - b[0])
      .flatMap(([pos, symbols]) => symbols.map(sym => `[[C:${pos}:${encodeURIComponent(sym)}]]`)).join("");
    return `${escapeGeneratedText(text)}[[CCM-CHORDS]]${anchors}`;
  }

  /* Inverse: render structured chart_content back to editable text. Used to
   * seed chart_source for migrated songs that only have structured content. */
  function chartToText(chart) {
    if (!chart || !Array.isArray(chart.sections)) return "";
    const out = [GENERATED_HEADER];
    for (const sec of chart.sections) {
      out.push(`[[CCM-SECTION]]${escapeGeneratedText(sec.label || "")}`);
      for (const line of sec.lines || []) {
        if (line.type === "lyric") {
          const confidence = line.confidence === "low" ? "low" : "high";
          out.push(`[[CCM-LYRIC:${confidence}]]${generatedChordText(line)}`);
        } else if (line.type === "progression") {
          let l = (line.chords || []).join(" ");
          if (line.repeat !== undefined && line.repeat !== null) {
            l += `${l ? " " : ""}[[CCM-PROGRESSION-REPEAT:${line.repeat}]]`;
          }
          if (Object.hasOwn(line, "note")) l += `[[CCM-NOTE]]${escapeGeneratedText(line.note)}`;
          out.push(`[[CCM-PROGRESSION]]${l}`);
        } else if (line.type === "raw") {
          out.push(`[[CCM-RAW]]${escapeGeneratedText(line.text || "")}`);
        } else if (line.type === "repeat") {
          out.push(`[[CCM-REPEAT${line.times !== undefined && line.times !== null ? `:${line.times}` : ""}]]${escapeGeneratedText(line.ref || "")}`);
        } else if (line.type === "spacer") {
          out.push("[[CCM-SPACER]]");
        } else {
          throw new Error(`Unsupported chart line type: ${line.type}`);
        }
      }
    }
    return `${out.join("\n")}\n`;
  }

  return {
    parseChartBody, chartToText, isGeneratedSourceUnchanged, isGeneratedChartSource,
    _isChord: isChord,
  };
});
