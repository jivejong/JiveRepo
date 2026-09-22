/*
 * lib/chartRenderer.jsx
 *
 * Renders the STRUCTURED chart_content ({ sections: [{ label, lines }] })
 * into React elements. (Previously this re-parsed raw text in the browser;
 * with dual storage the server derives the structure, so the renderer just
 * displays it — and it shares transpose.js instead of duplicating parse logic.)
 *
 * Line shapes it renders:
 *   { type:"lyric", text, chords:[{sym,pos}] }
 *   { type:"progression", chords:[...], repeat?, note?, section? }
 *   { type:"repeat", ref, times? }
 *   { type:"raw", text }
 * Section: { label, lines:[...] }
 *
 * Class names match index.css: chord-line / lyric-line, section-with-chords-*,
 * chart-section-label, section-ref-line, tab-block/tab-line, repeat-badge.
 */

import React from "react";

// Build the monospace chord row that sits above a lyric, placing each chord
// at its character position. A non-breaking space keeps empty rows from
// collapsing.
function chordRow(chords) {
  if (!chords || !chords.length) return "\u00a0";
  const sorted = [...chords].sort((a, b) => a.pos - b.pos);
  let row = "";
  for (const c of sorted) {
    const pos = Math.max(0, c.pos | 0);
    row += pos < row.length ? " " + c.sym : " ".repeat(pos - row.length) + c.sym;
  }
  return row;
}

function LyricLine({ line, i }) {
  const hasChords = line.chords && line.chords.length > 0;
  return (
    <div key={i} className="chart-line">
      {hasChords && <div className="chord-line">{chordRow(line.chords)}</div>}
      <div className="lyric-line">{line.text || "\u00a0"}</div>
    </div>
  );
}

function ProgressionLine({ line, i }) {
  return (
    <div key={i} className="section-with-chords-line">
      <span className="section-with-chords-chords">{(line.chords || []).join("   ")}</span>
      {line.repeat && <span className="repeat-badge">{line.repeat}x</span>}
      {line.note && <span className="repeat-badge" style={{ background: "transparent" }}>{line.note}</span>}
    </div>
  );
}

function renderLine(line, i) {
  switch (line.type) {
    case "lyric":
      return <LyricLine key={i} line={line} i={i} />;
    case "progression":
      return <ProgressionLine key={i} line={line} i={i} />;
    case "repeat":
      return (
        <div key={i} className="section-ref-line">
          {line.ref}{line.times ? ` ×${line.times}` : ""}
        </div>
      );
    case "raw":
      return <div key={i} className="tab-line">{line.text}</div>;
    default:
      return null;
  }
}

export function renderChart(chartContent) {
  if (!chartContent || !Array.isArray(chartContent.sections)) return null;
  return (
    <div className="chart-body">
      {chartContent.sections.map((sec, si) => (
        <div key={si} className="chart-section">
          {sec.label && <div className="chart-section-label">{sec.label}</div>}
          {(sec.lines || []).map((line, li) => renderLine(line, `${si}-${li}`))}
        </div>
      ))}
    </div>
  );
}
