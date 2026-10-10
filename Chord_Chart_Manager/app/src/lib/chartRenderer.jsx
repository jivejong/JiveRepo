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

import React, { useLayoutEffect, useRef, useState } from "react";

function orderedChords(chords) {
  return (chords || []).map((chord, index) => ({
    ...chord,
    pos: Number.isFinite(chord.pos) ? Math.max(0, Math.trunc(chord.pos)) : 0,
    index,
  }));
}

function graphemeBoundary(text, position) {
  if (position > text.length) return { offset: text.length, nextOffset: text.length, warning: null };
  if (typeof Intl.Segmenter !== "function") {
    // Code-point boundaries are the safe fallback when grapheme segmentation
    // is unavailable. Stored positions remain untouched in either case.
    let offset = 0;
    for (const point of text) {
      const next = offset + point.length;
      if (position > offset && position < next) {
        return { offset, nextOffset: next, warning: "inside-grapheme" };
      }
      offset = next;
    }
    const nextOffset = position < text.length
      ? position + (text.codePointAt(position) > 0xffff ? 2 : 1) : position;
    return { offset: position, nextOffset, warning: null };
  }
  const segments = new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(text);
  for (const segment of segments) {
    const end = segment.index + segment.segment.length;
    if (position > segment.index && position < end) {
      const first = text.charCodeAt(segment.index);
      const second = text.charCodeAt(segment.index + 1);
      const splitsSurrogate = position === segment.index + 1
        && first >= 0xd800 && first <= 0xdbff
        && second >= 0xdc00 && second <= 0xdfff;
      return { offset: segment.index, nextOffset: end, warning: splitsSurrogate
        ? "inside-surrogate-pair" : "inside-grapheme" };
    }
  }
  const segment = [...segments].find(item => item.index === position);
  return { offset: position, nextOffset: segment ? segment.index + segment.segment.length : position,
    warning: null };
}

function measureChartLine(lyric, chordLine, text, chords) {
  if (!lyric || !chordLine) return null;
  const node = lyric.firstChild;
  const lyricRect = lyric.getBoundingClientRect();
  const chordRect = chordLine.getBoundingClientRect();
  const chordStyle = getComputedStyle(chordLine);
  const localBorderBoxWidth = parseFloat(chordStyle.width)
    + (chordStyle.boxSizing === "border-box" ? 0
      : parseFloat(chordStyle.paddingLeft) + parseFloat(chordStyle.paddingRight)
        + parseFloat(chordStyle.borderLeftWidth) + parseFloat(chordStyle.borderRightWidth));
  // DOM rectangles are viewport CSS coordinates. Convert their x distances
  // back to the positioned chord row's local CSS pixels. This ratio reflects
  // CSS zoom and transforms on the row/ancestors; devicePixelRatio does not
  // describe this coordinate conversion.
  const measuredScaleX = localBorderBoxWidth > 0 ? chordRect.width / localBorderBoxWidth : 1;
  const scaleX = Number.isFinite(measuredScaleX) && measuredScaleX > 0 ? measuredScaleX : 1;
  const originX = chordRect.left + parseFloat(chordStyle.borderLeftWidth) * scaleX
    - chordLine.scrollLeft * scaleX;
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (context) context.font = chordStyle.font;
  const cellWidth = context?.measureText("0").width || parseFloat(chordStyle.fontSize) * 0.6;
  const lyricEnd = document.createRange();
  if (node?.nodeType === Node.TEXT_NODE && text.length) {
    lyricEnd.setStart(node, text.length);
    lyricEnd.collapse(true);
  }
  const endRect = lyricEnd.getBoundingClientRect();
  const endX = text.length && endRect.width + endRect.height > 0
    ? (endRect.left - originX) / scaleX
    : (lyricRect.left - originX) / scaleX;
  const layout = {};
  let reservedWidth = Math.max(0, endX);
  for (const chord of chords) {
    const mapped = graphemeBoundary(text, chord.pos);
    let left;
    if (chord.pos > text.length) {
      left = endX + (chord.pos - text.length) * cellWidth;
    } else if (node?.nodeType === Node.TEXT_NODE && text.length) {
      const range = document.createRange();
      range.setStart(node, mapped.offset);
      const next = mapped.nextOffset;
      range.setEnd(node, next > mapped.offset ? next : mapped.offset);
      const rect = range.getBoundingClientRect();
      left = rect.width + rect.height > 0 ? (rect.left - originX) / scaleX : endX;
    } else {
      left = 0;
    }
    const measuredChord = context?.measureText(String(chord.sym || "")).width
      || String(chord.sym || "").length * cellWidth;
    layout[chord.index] = { left, warning: mapped.warning, width: measuredChord };
  }
  const groups = [];
  const byPosition = [...chords].sort((a, b) =>
    layout[a.index].left - layout[b.index].left || a.index - b.index);
  for (const chord of byPosition) {
    const item = layout[chord.index];
    const previous = groups.at(-1);
    if (previous && item.left < previous.right) {
      previous.members.push(chord);
      previous.right = Math.max(previous.right, item.left + item.width);
    } else {
      groups.push({ left: item.left, right: item.left + item.width, members: [chord] });
    }
  }
  const measureGroup = group => {
    group.members.sort((a, b) => a.index - b.index);
    group.text = group.members.map(member => String(member.sym || "")).join(" | ");
    group.left = layout[group.members[0].index].left;
    group.width = context?.measureText(group.text).width || group.text.length * cellWidth;
  };
  groups.forEach(measureGroup);
  let merged = true;
  while (merged) {
    merged = false;
    groups.sort((a, b) => a.left - b.left || a.members[0].index - b.members[0].index);
    for (let index = 1; index < groups.length; index += 1) {
      const previous = groups[index - 1];
      const current = groups[index];
      if (current.left < previous.left + previous.width) {
        previous.members.push(...current.members);
        measureGroup(previous);
        groups.splice(index, 1);
        merged = true;
        break;
      }
    }
  }
  for (const group of groups) {
    group.warning = layout[group.members[0].index].warning;
    group.members.forEach(member => {
      layout[member.index].grouped = group.members.length > 1;
    });
    reservedWidth = Math.max(reservedWidth, group.left + group.width);
  }
  return { anchors: layout, groups, width: reservedWidth, cellWidth };
}

function LyricLine({ line, i }) {
  const hasChords = line.chords && line.chords.length > 0;
  const chords = orderedChords(line.chords);
  const lyricRef = useRef(null);
  const chordRef = useRef(null);
  const measurementRevision = useRef(0);
  const measureKey = JSON.stringify([line.text || "", chords.map(({ sym, pos }) => [sym, pos])]);
  const [layout, setLayout] = useState(null);
  useLayoutEffect(() => {
    if (!hasChords) return undefined;
    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const measured = measureChartLine(lyricRef.current, chordRef.current,
          line.text || "", chords);
        if (measured) {
          chordRef.current.dataset.measurementRevision = String(++measurementRevision.current);
          const next = { ...measured, key: measureKey };
          setLayout(current => JSON.stringify(current) === JSON.stringify(next) ? current : next);
        }
      });
    };
    measure();
    const resize = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    if (lyricRef.current) resize?.observe(lyricRef.current);
    if (chordRef.current) resize?.observe(chordRef.current);
    window.addEventListener("resize", measure);
    document.fonts?.addEventListener?.("loadingdone", measure);
    document.fonts?.ready?.then(measure);
    return () => {
      cancelAnimationFrame(frame);
      resize?.disconnect();
      window.removeEventListener("resize", measure);
      document.fonts?.removeEventListener?.("loadingdone", measure);
    };
  }, [hasChords, measureKey]);
  const activeLayout = layout?.key === measureKey ? layout : null;
  return (
    <div key={i} className="chart-line">
      {hasChords && (
        <div ref={chordRef} className="chord-line" style={{
          minWidth: activeLayout ? `${activeLayout.width}px` : undefined,
        }}>
          {(activeLayout?.groups || chords.map(chord => ({
            left: 0,
            members: [chord],
            text: String(chord.sym || ""),
            warning: undefined,
          }))).map((group, groupIndex) => {
            const first = group.members[0];
            return (
              <span
                key={`${first.pos}-${first.sym}-${first.index}-${groupIndex}`}
                className="chord-anchor"
                data-pos={first.pos}
                data-group-index={groupIndex}
                data-group-size={group.members.length}
                data-anchor-warning={group.warning || undefined}
                style={{ left: activeLayout ? `${group.left}px` : "0px" }}
              >
                {group.members.map((chord, index) => (
                  <React.Fragment key={`${chord.pos}-${chord.sym}-${chord.index}`}>
                    {index > 0 && <span className="chord-anchor-separator" aria-hidden="true"> | </span>}
                    <span className="chord-anchor-symbol" data-pos={chord.pos}
                      data-symbol-index={chord.index}
                      data-anchor-warning={activeLayout?.anchors?.[chord.index]?.warning || undefined}
                      title={activeLayout?.anchors?.[chord.index]?.warning === 'inside-surrogate-pair'
                        ? 'Stored column splits a surrogate pair; shown at the character’s leading edge.'
                        : activeLayout?.anchors?.[chord.index]?.warning === 'inside-grapheme'
                          ? 'Stored column splits a visible grapheme; shown at its leading edge.'
                          : undefined}>
                      {chord.sym}
                    </span>
                  </React.Fragment>
                ))}
              </span>
            );
          })}
        </div>
      )}
      <div
        ref={lyricRef}
        className={`lyric-line${hasChords ? ' lyric-line-anchored' : ''}`}
        style={hasChords && activeLayout ? { minWidth: `${activeLayout.width}px` } : undefined}
      >
        {line.text || "\u00a0"}
      </div>
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
    case "spacer":
      return <div key={i} className="chart-spacer" aria-hidden="true" />;
    default:
      return null;
  }
}

export function renderChart(chartContent) {
  if (!chartContent || !Array.isArray(chartContent.sections)) return null;
  return (
    <div className="chart-body">
      {chartContent.sections.map((sec, si) => {
        const nextHasLeadingSpacer = chartContent.sections[si + 1]?.lines?.[0]?.type === "spacer";
        return (
          <div key={si} className={`chart-section${nextHasLeadingSpacer ? ' chart-section-before-spacer' : ''}`}>
            {sec.label && <div className="chart-section-label">{sec.label}</div>}
            {(sec.lines || []).map((line, li) => renderLine(line, `${si}-${li}`))}
          </div>
        );
      })}
    </div>
  );
}
