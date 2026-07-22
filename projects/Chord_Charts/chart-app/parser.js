/**
 * chart-parser/parser.js  v2
 *
 * Handles all formatting patterns found in real charts:
 *   - Header: title – key (sounding)  artist  original_key
 *   - BB line: BB: structure   Capo N  or  KEY-BPM
 *   - Chord-above-lyric (standard)
 *   - Inline chord notation: {D}lyric {G}lyric
 *   - Section markers: [CHORUS], [Intro] G D C (4x), [Pre-Chorus 1]
 *   - Plain section headers: Verse 1, Chorus, Outro
 *   - Guitar tab lines: e--5-2-0--|
 *   - Modulation lines: Whole step up, 1/2 step up
 *   - Repeat annotations: 4X, 12X appended to lines
 */

'use strict';

// ---------------------------------------------------------------------------
// Chord token regex
// Matches: A Am Am7 Amaj7 A7sus4 A/E F#m7b5 Bb Cadd9 etc.
// ---------------------------------------------------------------------------
const CHORD_TOKEN_RE = /^[A-G][b#]?(maj7?|min7?|m7?|M7?|dim7?|aug|sus[24]?|add[0-9]+|[0-9]+[a-z]*)?(\/[A-G][b#]?)?$/;

function isChordToken(word) {
  return CHORD_TOKEN_RE.test(word.replace(/[,.]$/, ''));
}

// ---------------------------------------------------------------------------
// Line type constants
// ---------------------------------------------------------------------------
const T = {
  BLANK:               'BLANK',
  HEADER:              'HEADER',
  BB_META:             'BB_META',
  CHORD:               'CHORD',
  LYRIC:               'LYRIC',
  INLINE_CHORD_LYRIC:  'INLINE_CHORD_LYRIC',  // {D}Well she was an {E7}American girl
  SECTION_REF:         'SECTION_REF',          // [CHORUS] standalone
  SECTION_WITH_CHORDS: 'SECTION_WITH_CHORDS',  // [Intro] G D C  4X
  PLAIN_SECTION:       'PLAIN_SECTION',         // Verse 1, Chorus, Outro (no brackets)
  TAB:                 'TAB',                   // e--5-2-0--|
  MODULATION:          'MODULATION',            // Whole step up, 1/2 step up
};

// ---------------------------------------------------------------------------
// Classifiers
// ---------------------------------------------------------------------------

// Guitar tab: starts with a string letter and dashes/numbers/pipes
const TAB_RE = /^[eEbBgGdD][|\-][-\-0-9|\/\\hpbr()x ]{3,}/;

// Modulation
const MOD_RE = /^(whole|half|1\/2|\d+)\s+steps?\s+(up|down)$/i;

// Inline chord lyric: contains {ChordToken}
const INLINE_CHORD_RE = /\{[A-G][^}]*\}/;

// Repeat annotation: ends with 4X, 12X, x4, (x4) etc.
const REPEAT_RE = /\b(\d+)[Xx]\b|\(x\d+\)/;

// Plain section headers: "Verse 1", "Chorus", "Bridge", "Outro", "Intro"
// Must be short (< 5 words), no chord tokens dominating
const PLAIN_SECTION_RE = /^(verse|chorus|bridge|outro|intro|pre-?chorus|interlude|middle|solo|link|tag|hook|refrain|coda|vamp|breakdown)(\s+\d+)?[:\s]*$/i;

function classifyLine(line) {
  const stripped = line.trim();

  if (!stripped)                        return { type: T.BLANK, raw: line };
  if (stripped.startsWith('#'))         return { type: T.HEADER, raw: line, text: stripped };
  if (stripped.startsWith('BB:'))       return { type: T.BB_META, raw: line, text: stripped };
  if (TAB_RE.test(stripped))            return { type: T.TAB, raw: line, text: stripped };
  if (MOD_RE.test(stripped))            return { type: T.MODULATION, raw: line, text: stripped };
  if (INLINE_CHORD_RE.test(stripped))   return { type: T.INLINE_CHORD_LYRIC, raw: line, text: stripped };

  // Section with inline chords: [Intro] G D C  or  [Solo] G D  4X
  const sectionChordMatch = stripped.match(/^(\[.+?\])\s*(.+)$/);
  if (sectionChordMatch) {
    const afterBracket = sectionChordMatch[2].trim();
    const tokens = afterBracket.split(/\s+/).filter(Boolean);
    // Filter out repeat markers and annotation words
    const nonRepeat = tokens.filter(t => !REPEAT_RE.test(t) && !/^(end|on|fade|last|piece)$/i.test(t));
    const chordCount = nonRepeat.filter(isChordToken).length;
    if (nonRepeat.length > 0 && chordCount / nonRepeat.length >= 0.7) {
      const repeat = afterBracket.match(REPEAT_RE);
      return {
        type: T.SECTION_WITH_CHORDS,
        raw: line,
        label: sectionChordMatch[1],
        chords: nonRepeat.filter(isChordToken).join(' '),
        repeat: repeat ? repeat[0] : null,
        annotation: afterBracket,
      };
    }
  }

  // Pure section ref: [CHORUS], [BRIDGE], [Chorus 2], [Pre-Chorus 1]
  if (/^\[.+\]$/.test(stripped))        return { type: T.SECTION_REF, raw: line, text: stripped };

  // Plain section header (no brackets): "Verse 1", "Chorus", etc.
  if (PLAIN_SECTION_RE.test(stripped))  return { type: T.PLAIN_SECTION, raw: line, text: stripped };

  // Chord-only line
  const tokens = stripped.split(/\s+/).filter(Boolean);
  const chordCount = tokens.filter(isChordToken).length;
  if (tokens.length > 0 && chordCount / tokens.length >= 0.8) {
    return { type: T.CHORD, raw: line, text: stripped, chords: tokens.filter(isChordToken) };
  }

  return { type: T.LYRIC, raw: line, text: stripped };
}

// ---------------------------------------------------------------------------
// Header parser
// Formats seen in real charts:
//   # Already Gone – D (E)    Eagles
//   # All I Want Is You – G   U2    Ab
//   # Angel Eyes – G (B)      Jeff Healey   C
//   # American Girl – D       Tom Petty
//   # Back Where You Belong – G   38 Special
//
// Tab slots (after collapsing multiple tabs):
//   [0] title + key info
//   [1] artist
//   [2] original key (optional)
// ---------------------------------------------------------------------------
function parseHeader(text) {
  const raw = text.replace(/^#+\s*/, '').trim();
  const tabs = raw.split('\t').map(s => s.trim()).filter(Boolean);

  const titleKeyPart = tabs[0] || '';
  const artist = tabs[1] || null;
  // Tab slot [2]: a lone key token = original key
  const tab2 = tabs[2] || null;
  const originalKeyFromTab = tab2 && isChordToken(tab2) ? tab2 : null;

  // Parse title – key (soundingKey) from slot 0
  const keyMatch = titleKeyPart.match(/[–\-]\s*([A-G][b#]?m?)\s*(?:\(([A-G][b#]?m?)\))?/);

  let title = titleKeyPart;
  let chartWrittenKey = null;
  let soundingKeyFromHeader = null;

  if (keyMatch) {
    title = titleKeyPart.slice(0, keyMatch.index).trim();
    chartWrittenKey = keyMatch[1] || null;
    soundingKeyFromHeader = keyMatch[2] || null;
  }

  return {
    title,
    artist,
    chartWrittenKey,
    soundingKeyFromHeader,  // from parens in header
    originalKey: originalKeyFromTab, // from tab slot after artist
  };
}

// ---------------------------------------------------------------------------
// BB meta parser
// Formats:
//   BB: Intro / VS-CH / Modulation    Capo 2
//   BB: VS / BR / End                 D-114
// ---------------------------------------------------------------------------
function parseBBMeta(text) {
  // Capo
  const capoMatch = text.match(/Capo\s+(\d+)/i);
  const defaultCapo = capoMatch ? parseInt(capoMatch[1], 10) : 0;

  // BPM: KEY-NUMBER pattern e.g. D-114, or just a number
  const bpmMatch = text.match(/[A-Z]-(\d{2,3})\b/);
  const bpm = bpmMatch ? parseInt(bpmMatch[1], 10) : null;

  // BB structure: everything after "BB:" before the tab separator
  const bbMatch = text.match(/BB:\s*([^\t]+)/i);
  let beatbuddyStructure = null;
  if (bbMatch) {
    // Strip the BPM token if present
    beatbuddyStructure = bbMatch[1].replace(/[A-Z]-\d{2,3}/, '').trim();
  }

  return { defaultCapo, bpm, beatbuddyStructure };
}

// ---------------------------------------------------------------------------
// Sounding key computation
// ---------------------------------------------------------------------------
const CHROMATIC = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
const ENHARMONIC = { Db:'C#', Eb:'D#', Fb:'E', Gb:'F#', Ab:'G#', Bb:'A#', Cb:'B' };

function normalizeKey(key) {
  if (!key) return null;
  const root = key.replace(/m$/, '');
  const isMinor = key.endsWith('m') && !key.endsWith('maj') && !key.endsWith('dim');
  const normalized = ENHARMONIC[root] || root;
  return isMinor ? normalized + 'm' : normalized;
}

function computeSoundingKey(writtenKey, capoFret) {
  if (!writtenKey || !capoFret) return writtenKey;
  const isMinor = writtenKey.endsWith('m');
  const root = writtenKey.replace(/m$/, '');
  const normalized = ENHARMONIC[root] || root;
  const idx = CHROMATIC.indexOf(normalized);
  if (idx === -1) return writtenKey;
  const sounding = CHROMATIC[(idx + capoFret) % 12];
  return isMinor ? sounding + 'm' : sounding;
}

// ---------------------------------------------------------------------------
// Section builder
// ---------------------------------------------------------------------------
function buildSections(classifiedLines) {
  const sections = [];
  let current = { label: null, lines: [] };
  let i = 0;

  const pushCurrent = () => {
    if (current.lines.length > 0 || current.label) {
      sections.push({ ...current });
      current = { label: null, lines: [] };
    }
  };

  while (i < classifiedLines.length) {
    const cl = classifiedLines[i];

    if (cl.type === T.BLANK) { i++; continue; }

    if (cl.type === T.MODULATION) {
      pushCurrent();
      sections.push({ type: 'modulation', text: cl.text });
      i++; continue;
    }

    if (cl.type === T.TAB) {
      // Collect consecutive tab lines into a block
      const tabLines = [];
      while (i < classifiedLines.length && classifiedLines[i].type === T.TAB) {
        tabLines.push(classifiedLines[i].text);
        i++;
      }
      current.lines.push({ type: 'tab', lines: tabLines });
      continue;
    }

    if (cl.type === T.SECTION_REF) {
      pushCurrent();
      sections.push({ type: 'ref', ref: cl.text });
      i++; continue;
    }

    if (cl.type === T.PLAIN_SECTION) {
      pushCurrent();
      current.label = cl.text;
      i++; continue;
    }

    if (cl.type === T.SECTION_WITH_CHORDS) {
      pushCurrent();
      sections.push({
        type: 'section_with_chords',
        label: cl.label,
        chords: cl.chords,
        repeat: cl.repeat,
        annotation: cl.annotation,
      });
      i++; continue;
    }

    if (cl.type === T.CHORD) {
      // Look ahead for lyric (skip blanks)
      let j = i + 1;
      while (j < classifiedLines.length && classifiedLines[j].type === T.BLANK) j++;
      const next = classifiedLines[j];
      if (next && (next.type === T.LYRIC || next.type === T.INLINE_CHORD_LYRIC)) {
        current.lines.push({ type: 'chord_lyric', chords: cl.text, lyric: next.text, lyricType: next.type });
        i = j + 1;
      } else {
        current.lines.push({ type: 'chord_only', chords: cl.text });
        i++;
      }
      continue;
    }

    if (cl.type === T.LYRIC || cl.type === T.INLINE_CHORD_LYRIC) {
      current.lines.push({ type: cl.type === T.INLINE_CHORD_LYRIC ? 'inline_chord_lyric' : 'lyric', text: cl.text });
      i++; continue;
    }

    i++;
  }

  pushCurrent();
  return sections;
}

// ---------------------------------------------------------------------------
// Main parse function
// ---------------------------------------------------------------------------
function parseChart(rawText) {
  const lines = rawText.split('\n');
  const classified = lines.map(classifyLine);

  const headerLine = classified.find(l => l.type === T.HEADER);
  const { title, artist, chartWrittenKey, soundingKeyFromHeader, originalKey } =
    headerLine ? parseHeader(headerLine.text) : {};

  const bbLine = classified.find(l => l.type === T.BB_META);
  const { defaultCapo, bpm, beatbuddyStructure } =
    bbLine ? parseBBMeta(bbLine.text) : { defaultCapo: 0, bpm: null, beatbuddyStructure: null };

  // Sounding key: prefer header parens, else compute from capo
  const soundingKey = soundingKeyFromHeader || computeSoundingKey(chartWrittenKey, defaultCapo);

  const contentLines = classified.filter(
    l => l.type !== T.HEADER && l.type !== T.BB_META
  );
  const sections = buildSections(contentLines);

  return {
    title:              title || 'Unknown',
    artist:             artist || null,
    chartWrittenKey:    chartWrittenKey || null,
    soundingKey:        soundingKey || null,
    originalKey:        originalKey || null,
    preferredKey:       chartWrittenKey || null,
    defaultCapo,
    bpm,
    beatbuddyStructure,
    chartContent:       rawText,
    sections,
    genre: null, feel: null, era: null, tempoFeel: null,
    aiConfidence: null, sourceFile: null, cloudProvider: null,
  };
}

module.exports = { parseChart, classifyLine, parseHeader, parseBBMeta, isChordToken, T };
