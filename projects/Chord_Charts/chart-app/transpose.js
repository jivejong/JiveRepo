/**
 * transpose.js
 *
 * Transpose engine for chord charts.
 * Pure JavaScript, no dependencies — runs in Node.js AND in the browser (PWA).
 *
 * Features:
 *   - Transpose any chord token up or down any number of semitones
 *   - Respects enharmonic preference (sharps vs flats)
 *   - Transposes full chart text (chord-above-lyric, inline {chord}, section+chord lines)
 *   - Computes sounding key from written key + capo fret
 *   - Converts between written key and target sounding key
 *
 * Usage:
 *   const { transposeChart, transposeSemitones, soundingKey } = require('./transpose');
 *
 *   // Transpose a full chart up 2 semitones (whole step)
 *   const newChart = transposeChart(chartText, 2);
 *
 *   // Transpose to a specific target key
 *   const newChart = transposeChart(chartText, 0, { fromKey: 'G', toKey: 'A' });
 *
 *   // Get sounding key (what the audience hears)
 *   const sounds = soundingKey('D', 2);  // → 'E'
 */

'use strict';

// ---------------------------------------------------------------------------
// Chromatic scale — sharps and flats
// ---------------------------------------------------------------------------
const SHARPS = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
const FLATS  = ['C','Db','D','Eb','E','F','Gb','G','Ab','A','Bb','B'];

// Enharmonic normalization — always resolve to sharps for internal math
const ENHARMONIC = {
  'Db':'C#', 'Eb':'D#', 'Fb':'E',  'Gb':'F#',
  'Ab':'G#', 'Bb':'A#', 'Cb':'B',
  // Double sharps/flats (rare but present in some charts)
  'E#':'F',  'B#':'C',
};

// Keys that conventionally use flats
const FLAT_KEYS = new Set(['F','Bb','Eb','Ab','Db','Gb','Dm','Gm','Cm','Fm','Bbm','Ebm']);

// ---------------------------------------------------------------------------
// Single chord token transposer
// ---------------------------------------------------------------------------

/**
 * Normalize a root note to its sharp equivalent for semitone math.
 * 'Bb' → 'A#', 'Db' → 'C#', etc.
 */
function normalizeRoot(root) {
  return ENHARMONIC[root] || root;
}

/**
 * Get the semitone index (0–11) of a root note.
 */
function rootToIndex(root) {
  const normalized = normalizeRoot(root);
  const idx = SHARPS.indexOf(normalized);
  if (idx === -1) throw new Error(`Unknown root note: ${root}`);
  return idx;
}

/**
 * Convert a semitone index back to a note name.
 * preferFlats: use flat spellings where possible.
 */
function indexToRoot(idx, preferFlats = false) {
  const scale = preferFlats ? FLATS : SHARPS;
  return scale[((idx % 12) + 12) % 12];
}

/**
 * Determine if flats should be preferred for a given target key.
 */
function shouldPreferFlats(targetKey) {
  if (!targetKey) return false;
  const root = targetKey.replace(/m$/, '');
  return FLAT_KEYS.has(targetKey) || FLAT_KEYS.has(root);
}

/**
 * Chord token regex — matches the root + quality + optional bass note.
 * Captures: [full, root, quality, slash, bassRoot]
 * Examples:
 *   'Am7'      → root='A',  quality='m7',  slash=null
 *   'G/B'      → root='G',  quality='',    slash='/', bassRoot='B'
 *   'F#maj7/A' → root='F#', quality='maj7',slash='/', bassRoot='A'
 *   'Cadd9'    → root='C',  quality='add9',slash=null
 */
const CHORD_RE = /^([A-G][b#]?)((?:maj7?|min7?|m7?|M7?|dim7?|aug|sus[24]?|add[0-9]+|[0-9]+[a-z]*)?)(\/([ A-G][b#]?))?$/;

/**
 * Transpose a single chord token by semitones.
 * Returns the original string unchanged if it doesn't parse as a chord.
 */
function transposeChord(chord, semitones, preferFlats = false) {
  const m = chord.match(CHORD_RE);
  if (!m) return chord;

  const [, root, quality, slash, bassRoot] = m;

  try {
    const newRootIdx  = rootToIndex(root) + semitones;
    const newRoot     = indexToRoot(newRootIdx, preferFlats);
    const newBass     = bassRoot
      ? indexToRoot(rootToIndex(bassRoot.trim()) + semitones, preferFlats)
      : null;

    return newRoot + quality + (slash && newBass ? `/${newBass}` : '');
  } catch {
    return chord; // unrecognized root — leave unchanged
  }
}

// ---------------------------------------------------------------------------
// Semitone delta between two keys
// ---------------------------------------------------------------------------

/**
 * Calculate how many semitones to go from fromKey to toKey.
 * E.g. fromKey='G' toKey='A' → 2
 */
function keyDelta(fromKey, toKey) {
  const from = rootToIndex(fromKey.replace(/m$/, ''));
  const to   = rootToIndex(toKey.replace(/m$/, ''));
  let delta = (to - from + 12) % 12;
  // Prefer the shortest path (up to 6 semitones each way)
  if (delta > 6) delta -= 12;
  return delta;
}

// ---------------------------------------------------------------------------
// Sounding key computation
// ---------------------------------------------------------------------------

/**
 * Given a written key and capo fret, return the sounding key.
 * soundingKey('D', 2) → 'E'
 * soundingKey('G', 3) → 'Bb'  (using flat preference)
 */
function soundingKey(writtenKey, capoFret) {
  if (!writtenKey || !capoFret) return writtenKey;
  const isMinor  = writtenKey.endsWith('m');
  const root     = writtenKey.replace(/m$/, '');
  const newIdx   = ((rootToIndex(root) + capoFret) % 12 + 12) % 12;
  // Determine flat preference from the resulting note
  // Check both enharmonic spellings — if EITHER is a flat key, prefer flats
  const sharpSpelling = SHARPS[newIdx];
  const flatSpelling  = FLATS[newIdx];
  const prefFlat = shouldPreferFlats(isMinor ? sharpSpelling + 'm' : sharpSpelling)
                || shouldPreferFlats(isMinor ? flatSpelling  + 'm' : flatSpelling);
  const newRoot  = indexToRoot(newIdx, prefFlat);
  return isMinor ? newRoot + 'm' : newRoot;
}

/**
 * Given a desired sounding key and capo fret, return the written key to use.
 * writtenKeyForSounding('E', 2) → 'D'
 */
function writtenKeyForSounding(targetSoundingKey, capoFret) {
  if (!capoFret) return targetSoundingKey;
  const isMinor = targetSoundingKey.endsWith('m');
  const root    = targetSoundingKey.replace(/m$/, '');
  const newIdx  = rootToIndex(root) - capoFret;
  const newRoot = indexToRoot(newIdx, shouldPreferFlats(targetSoundingKey));
  return isMinor ? newRoot + 'm' : newRoot;
}

// ---------------------------------------------------------------------------
// Full chart text transposer
// ---------------------------------------------------------------------------

// Chord token regex for inline matching within a line
const CHORD_TOKEN_RE = /\b([A-G][b#]?(?:maj7?|min7?|m7?|M7?|dim7?|aug|sus[24]?|add[0-9]+|[0-9]+[a-z]*)?(?:\/[A-G][b#]?)?)\b/g;

// Inline chord notation: {Chord}
const INLINE_CHORD_RE = /\{([A-G][b#]?(?:[^}]*)?)\}/g;

/**
 * Detect if a line is a chord-only line (≥80% chord tokens).
 */
function isChordLine(line) {
  const stripped = line.trim();
  if (!stripped) return false;
  const tokens = stripped.split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return false;
  const chordCount = tokens.filter(t => CHORD_RE.test(t)).length;
  return chordCount / tokens.length >= 0.8;
}

/**
 * Detect if a line is a section-with-chords line: [Intro] G D C
 */
function isSectionWithChords(line) {
  return /^\[.+?\]\s+[A-G]/.test(line.trim());
}

/**
 * Transpose all chord tokens in a chord-only line, preserving spacing.
 */
function transposeChordLine(line, semitones, preferFlats) {
  // Replace each chord token while preserving surrounding whitespace
  return line.replace(CHORD_TOKEN_RE, (match) => {
    // Only replace if it's actually a valid chord (not a lyric word that matched)
    if (CHORD_RE.test(match)) {
      return transposeChord(match, semitones, preferFlats);
    }
    return match;
  });
}

/**
 * Transpose inline chord notation: {D}Well she was → {E}Well she was
 */
function transposeInlineChords(line, semitones, preferFlats) {
  return line.replace(INLINE_CHORD_RE, (_, chord) => {
    return `{${transposeChord(chord, semitones, preferFlats)}}`;
  });
}

/**
 * Transpose the chord portion of a section-with-chords line.
 * "[Intro] G D C" → "[Intro] A E D"
 */
function transposeSectionWithChords(line, semitones, preferFlats) {
  // Keep the [bracket] label, transpose everything after it
  return line.replace(/^(\s*\[.+?\]\s*)(.+)$/, (_, label, rest) => {
    return label + transposeChordLine(rest, semitones, preferFlats);
  });
}

/**
 * Transpose a complete chart text by a given number of semitones.
 *
 * @param {string}  chartText  - Raw chart text (as stored in DB)
 * @param {number}  semitones  - Number of semitones to shift (can be negative)
 * @param {object}  opts
 * @param {string}  opts.fromKey    - Current written key (for delta calculation)
 * @param {string}  opts.toKey      - Target written key (alternative to semitones)
 * @param {boolean} opts.preferFlats - Force flat spellings
 * @returns {string} Transposed chart text
 */
function transposeChart(chartText, semitones = 0, opts = {}) {
  const { fromKey, toKey, preferFlats: forcedFlats } = opts;

  // If toKey provided, compute semitones from key delta
  let delta = semitones;
  if (fromKey && toKey) {
    delta = keyDelta(fromKey, toKey);
  }

  if (delta === 0) return chartText;  // nothing to do

  // Determine flat preference from target key
  let preferFlats = forcedFlats ?? false;
  if (toKey) preferFlats = shouldPreferFlats(toKey);
  else if (fromKey) {
    const targetRoot = indexToRoot(rootToIndex(fromKey.replace(/m$/,'')) + delta, false);
    preferFlats = shouldPreferFlats(targetRoot);
  }

  const lines = chartText.split('\n');
  const transposed = lines.map(line => {
    // Skip header lines (# Title – Key  Artist)
    if (line.trimStart().startsWith('#')) return line;
    // Skip BB meta lines
    if (line.trim().startsWith('BB:')) return line;
    // Skip tab lines
    if (/^[eEbBgGdD][|\-]/.test(line.trim())) return line;
    // Skip modulation lines: 'Whole step up', '1/2 step up'
    if (/^(whole|half|1\/2|\d+)\s+steps?\s+(up|down)$/i.test(line.trim())) return line;
    // Skip section refs [CHORUS] with no chords
    if (/^\s*\[.+\]\s*$/.test(line)) return line;

    // Section + chords: [Intro] G D C
    if (isSectionWithChords(line)) {
      return transposeSectionWithChords(line, delta, preferFlats);
    }
    // Inline chord notation: {D}lyrics
    if (INLINE_CHORD_RE.test(line)) {
      INLINE_CHORD_RE.lastIndex = 0;
      return transposeInlineChords(line, delta, preferFlats);
    }
    // Chord-only line
    if (isChordLine(line)) {
      return transposeChordLine(line, delta, preferFlats);
    }
    // Lyric line — no change
    return line;
  });

  return transposed.join('\n');
}

// ---------------------------------------------------------------------------
// Convenience: transpose by whole/half step labels
// ---------------------------------------------------------------------------
const STEP_MAP = { 'half': 1, '1/2': 1, 'whole': 2, '1': 1, '2': 2 };

function transposeByStep(chartText, stepLabel, direction = 'up', opts = {}) {
  const semis = STEP_MAP[stepLabel.toLowerCase()] ?? 1;
  return transposeChart(chartText, direction === 'up' ? semis : -semis, opts);
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------
module.exports = {
  transposeChart,
  transposeChord,
  transposeByStep,
  soundingKey,
  writtenKeyForSounding,
  keyDelta,
  shouldPreferFlats,
  // Internals exposed for testing
  rootToIndex,
  indexToRoot,
  SHARPS,
  FLATS,
};
