/**
 * lib/chartRenderer.jsx
 *
 * Converts raw chart text into React elements for display.
 * Uses the same line classification logic as the server-side parser,
 * re-implemented here so the renderer runs entirely in the browser.
 *
 * Key design: renders chord lines in amber JetBrains Mono,
 * lyrics in white Inter — visually distinct at a glance on stage.
 */

import React from 'react';

// ── Line classifier (browser port of parser.js) ───────────────────────────────

const CHORD_TOKEN_RE = /^[A-G][b#]?(maj7?|min7?|m7?|M7?|dim7?|aug|sus[24]?|add[0-9]+|[0-9]+[a-z]*)?(\/[A-G][b#]?)?$/;
const INLINE_CHORD_RE = /\{([A-G][b#]?[^}]*)\}/g;
const TAB_RE = /^[eEbBgGdD][|\-][-\-0-9|\/\\hpbr()x ]{3,}/;
const MOD_RE = /^(whole|half|1\/2|\d+)\s+steps?\s+(up|down)$/i;
const REPEAT_RE = /\b(\d+)[Xx]\b|\(x\d+\)/i;

function isChordToken(word) {
  return CHORD_TOKEN_RE.test(word.replace(/[,.]$/, ''));
}

function classifyLine(line) {
  const s = line.trim();
  if (!s)                               return { type: 'blank' };
  if (s.startsWith('#'))                return { type: 'header', text: s };
  if (s.startsWith('BB:'))             return { type: 'bb_meta', text: s };
  if (TAB_RE.test(s))                  return { type: 'tab', text: s };
  if (MOD_RE.test(s))                  return { type: 'modulation', text: s };
  if (INLINE_CHORD_RE.test(s))         { INLINE_CHORD_RE.lastIndex=0; return { type: 'inline_chord_lyric', text: s }; }

  // Section with chords: [Intro] G D C  4X
  const secChord = s.match(/^(\[.+?\])\s*(.+)$/);
  if (secChord) {
    const after = secChord[2].trim();
    const tokens = after.split(/\s+/).filter(t => !REPEAT_RE.test(t));
    const chords = tokens.filter(isChordToken);
    if (tokens.length > 0 && chords.length / tokens.length >= 0.7) {
      return {
        type: 'section_with_chords',
        label: secChord[1],
        chords: chords.join(' '),
        repeat: after.match(REPEAT_RE)?.[0] ?? null,
      };
    }
  }

  if (/^\[.+\]$/.test(s))   return { type: 'section_ref', text: s };

  if (/^(verse|chorus|bridge|outro|intro|pre-?chorus|interlude|solo|link|tag|hook|coda|vamp)(\s+\d+)?[:\s]*$/i.test(s))
    return { type: 'plain_section', text: s };

  const tokens = s.split(/\s+/).filter(Boolean);
  const chordCount = tokens.filter(isChordToken).length;
  if (tokens.length > 0 && chordCount / tokens.length >= 0.8)
    return { type: 'chord', text: s, chords: tokens.filter(isChordToken) };

  return { type: 'lyric', text: s };
}

// ── Inline chord lyric renderer ────────────────────────────────────────────────
// Converts "{D}Well she was an {E7}American girl" into aligned chord+lyric rows

function renderInlineChordLyric(text, key) {
  // Extract chord positions and build two rows: chords and lyrics
  const chordRow = [];
  const lyricRow = [];

  let lyricText = text;
  const chordPositions = []; // { pos, chord }
  let offset = 0;

  // Replace {Chord} tokens, track their positions in the cleaned lyric
  const cleaned = text.replace(/\{([^}]+)\}/g, (match, chord, idx) => {
    chordPositions.push({ pos: idx - offset, chord });
    offset += match.length;
    return '';
  });

  // Build chord row: spaces up to position, then chord
  if (chordPositions.length > 0) {
    let chordLine = '';
    let cursor = 0;
    for (const { pos, chord } of chordPositions) {
      const spaces = Math.max(0, pos - cursor);
      chordLine += ' '.repeat(spaces) + chord;
      cursor = pos + chord.length;
    }
    return (
      <div key={key} className="inline-chord-lyric">
        <div className="icl-chord-row">{chordLine}</div>
        <div className="icl-lyric-row">{cleaned}</div>
      </div>
    );
  }

  return <div key={key} className="lyric-line">{text}</div>;
}

// ── Main renderer ─────────────────────────────────────────────────────────────

export function renderChart(chartContent) {
  if (!chartContent) return null;

  const lines = chartContent.split('\n');
  const classified = lines.map(classifyLine);
  const elements = [];
  let i = 0;
  let sectionKey = 0;

  while (i < classified.length) {
    const cl = classified[i];

    // Skip headers and BB meta — displayed in the topbar/meta-bar instead
    if (cl.type === 'header' || cl.type === 'bb_meta') { i++; continue; }
    if (cl.type === 'blank') { i++; continue; }

    if (cl.type === 'modulation') {
      const label = cl.text.replace(/^(whole|half|1\/2|\d+)\s+steps?\s+/i, '').toLowerCase() === 'up'
        ? `↑ ${cl.text}` : `↓ ${cl.text}`;
      elements.push(
        <div key={`mod-${i}`} className="modulation-line">
          <div className="mod-rule" />
          <div className="mod-badge">{cl.text}</div>
          <div className="mod-rule" />
        </div>
      );
      i++; continue;
    }

    if (cl.type === 'tab') {
      const tabLines = [];
      while (i < classified.length && classified[i].type === 'tab') {
        tabLines.push(classified[i].text);
        i++;
      }
      elements.push(
        <div key={`tab-${i}`} className="tab-block">
          {tabLines.map((tl, j) => <div key={j} className="tab-line">{tl}</div>)}
        </div>
      );
      continue;
    }

    if (cl.type === 'section_ref') {
      elements.push(
        <div key={`ref-${i}`} className="section-ref-line">
          {cl.text.replace(/^\[|\]$/g, '')}
        </div>
      );
      i++; continue;
    }

    if (cl.type === 'plain_section') {
      elements.push(
        <div key={`ps-${i}`} className="chart-section-label">{cl.text}</div>
      );
      i++; continue;
    }

    if (cl.type === 'section_with_chords') {
      elements.push(
        <div key={`swc-${i}`} className="section-with-chords-line">
          <span className="section-with-chords-label">
            {cl.label.replace(/^\[|\]$/g, '')}
          </span>
          <span className="section-with-chords-chords">{cl.chords}</span>
          {cl.repeat && <span className="repeat-badge">{cl.repeat}</span>}
        </div>
      );
      i++; continue;
    }

    if (cl.type === 'inline_chord_lyric') {
      elements.push(renderInlineChordLyric(cl.text, `icl-${i}`));
      i++; continue;
    }

    if (cl.type === 'chord') {
      // Look ahead for lyric
      let j = i + 1;
      while (j < classified.length && classified[j].type === 'blank') j++;
      const next = classified[j];

      if (next && (next.type === 'lyric' || next.type === 'inline_chord_lyric')) {
        if (next.type === 'inline_chord_lyric') {
          elements.push(
            <div key={`cl-${i}`}>
              <div className="chord-line">{lines[i]}</div>
              {renderInlineChordLyric(next.text, `icl-${j}`)}
            </div>
          );
        } else {
          elements.push(
            <div key={`cl-${i}`}>
              <div className="chord-line">{lines[i]}</div>
              <div className="lyric-line">{next.text}</div>
            </div>
          );
        }
        i = j + 1;
      } else {
        elements.push(
          <div key={`co-${i}`} className="chord-only-line">{lines[i]}</div>
        );
        i++;
      }
      continue;
    }

    if (cl.type === 'lyric') {
      elements.push(
        <div key={`ly-${i}`} className="lyric-line">{cl.text}</div>
      );
      i++; continue;
    }

    i++;
  }

  return <div className="chart-body">{elements}</div>;
}
