/*
 * transpose.js — chord transposition for the React app (ESM named exports).
 * Same logic as the shared browser module, exposed for `import` in Vite.
 *
 * Only the root and slash-bass move; quality/extensions carry through.
 * Enharmonic spelling follows the TARGET key.
 */

const SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const FLAT  = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];

const NOTE_TO_PC = {
  C: 0, "B#": 0, "C#": 1, Db: 1, D: 2, "D#": 3, Eb: 3, E: 4, Fb: 4,
  F: 5, "E#": 5, "F#": 6, Gb: 6, G: 7, "G#": 8, Ab: 8, A: 9, "A#": 10,
  Bb: 10, B: 11, Cb: 11,
};

const FLAT_KEYS = new Set([
  "F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb",
  "Dm", "Gm", "Cm", "Fm", "Bbm", "Ebm", "Abm",
]);

export function prefersFlats(key) {
  if (!key) return false;
  const norm = key.trim().replace(/^[a-g]/, (c) => c.toUpperCase());
  return FLAT_KEYS.has(norm);
}

const ROOT_RE = /^([A-G])(#{1,2}|b{1,2})?/;

function parseRoot(token) {
  const m = ROOT_RE.exec(token);
  if (!m) return null;
  const name = m[1] + (m[2] || "");
  const pc = NOTE_TO_PC[name];
  if (pc === undefined) return null;
  return [pc, token.slice(m[0].length)];
}

function spell(pc, useFlats) {
  pc = ((pc % 12) + 12) % 12;
  return (useFlats ? FLAT : SHARP)[pc];
}

export function transposeChord(symbol, semitones, targetKey) {
  if (!symbol) return symbol;
  const useFlats = prefersFlats(targetKey);
  let bass = null, core = symbol;
  const slash = symbol.indexOf("/");
  if (slash !== -1) { core = symbol.slice(0, slash); bass = symbol.slice(slash + 1); }
  const parsed = parseRoot(core);
  if (!parsed) return symbol;
  const [pc, rest] = parsed;
  let out = spell(pc + semitones, useFlats) + rest;
  if (bass !== null) {
    const bp = parseRoot(bass);
    out += "/" + (bp ? spell(bp[0] + semitones, useFlats) + bp[1] : bass);
  }
  return out;
}

export function semitonesBetween(fromKey, toKey) {
  const norm = (k) => k.trim().replace(/^[a-g]/, (c) => c.toUpperCase()).replace(/m$/, "");
  const from = NOTE_TO_PC[norm(fromKey)];
  const to = NOTE_TO_PC[norm(toKey)];
  if (from === undefined || to === undefined) return 0;
  return (((to - from) % 12) + 12) % 12;
}

export function transposeChart(chart, semitones, targetKey) {
  if (!chart || !Array.isArray(chart.sections)) return chart;
  return {
    ...chart,
    sections: chart.sections.map((sec) => ({
      ...sec,
      lines: (sec.lines || []).map((line) => {
        if (line.type === "lyric" && Array.isArray(line.chords)) {
          return { ...line, chords: line.chords.map((c) => ({ ...c, sym: transposeChord(c.sym, semitones, targetKey) })) };
        }
        if (line.type === "progression" && Array.isArray(line.chords)) {
          return { ...line, chords: line.chords.map((s) => transposeChord(s, semitones, targetKey)) };
        }
        return line;
      }),
    })),
  };
}

/* Sounding key when playing `writtenKey` with a capo on fret N. */
export function soundingKey(writtenKey, capo) {
  if (!writtenKey || !capo) return writtenKey;
  return transposeChord(writtenKey, capo, writtenKey);
}

/* Signed shortest-direction semitone delta (-6..+6). */
export function keyDelta(fromKey, toKey) {
  let d = semitonesBetween(fromKey, toKey);
  if (d > 6) d -= 12;
  return d;
}

// Conventional spelling for each key by pitch class — the spelling a musician
// expects to read (flat keys stay flat: Eb, Bb, Ab, Db, F; sharp keys stay
// sharp). Used so a chart displayed in, say, B-flat reads "Bb" not "A#".
const CANON_MAJOR = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
const CANON_MINOR = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"];

export function canonicalKey(pc, isMinor) {
  pc = ((pc % 12) + 12) % 12;
  return (isMinor ? CANON_MINOR : CANON_MAJOR)[pc];
}

/* The conventional key name you land in after transposing `baseKey` by
 * `semitones`. Drives both the displayed key label and the flat/sharp choice
 * for the whole chart, so spelling follows the key you're actually in. */
export function keyAfter(baseKey, semitones) {
  if (!baseKey) return baseKey;
  const trimmed = baseKey.trim();
  const isMinor = /m$/.test(trimmed) && !/maj/i.test(trimmed);
  const root = trimmed.replace(/m$/, "").replace(/^[a-g]/, (c) => c.toUpperCase());
  const pc = NOTE_TO_PC[root];
  if (pc === undefined) return baseKey;
  return canonicalKey(pc + semitones, isMinor);
}
