import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
import { transposeChord } from '../../transpose.js';
const {
  parseChartBody,
  isDecoratedChordLine, decoratedChordTokenSpans, decoratedChordAnalysis,
  transposeDecoratedRawLines,
} = require('../../server/chartParser.js');
const orderedRowCases = JSON.parse(readFileSync(
  new URL('../../../pipeline/tests/fixtures/ordered_chord_rows.json', import.meta.url), 'utf8'));

describe('shared chart parser', () => {
  it('parses sections and chord-over-lyric lines', () => {
    expect(parseChartBody('[Verse]\nG      C\nAmazing grace\n')).toEqual({
      sections: [{
        label: 'Verse',
        lines: [{
          type: 'lyric',
          text: 'Amazing grace',
          chords: [{ sym: 'G', pos: 0 }, { sym: 'C', pos: 7 }],
          confidence: 'low',
        }],
      }],
    });
  });

  it('preserves normalized internal blank lines in ordinary chart text', () => {
    const parsed = parseChartBody('\n[Verse]\n\nC   G\nInvented first line\n\n\nInvented second line\n\n[Chorus]\nInvented chorus line\n\n');
    expect(parsed.sections).toEqual([
      { label: 'Verse', lines: [
        { type: 'spacer' },
        { type: 'lyric', text: 'Invented first line',
          chords: [{ sym: 'C', pos: 0 }, { sym: 'G', pos: 4 }], confidence: 'low' },
        { type: 'spacer' },
        { type: 'lyric', text: 'Invented second line', chords: [], confidence: 'high' },
      ] },
      { label: 'Chorus', lines: [
        { type: 'lyric', text: 'Invented chorus line', chords: [], confidence: 'high' },
      ] },
    ]);

    const separatorBeforeSection = parseChartBody('[Verse]\nInvented verse line\n\n[Chorus]\nInvented chorus line');
    expect(separatorBeforeSection.sections[0].lines.map(line => line.type)).toEqual(['lyric']);
    expect(separatorBeforeSection.sections[1].lines.map(line => line.type)).toEqual(['lyric']);

    const chordBeforeBlank = parseChartBody('[Verse]\nC G\n\nInvented unpaired lyric');
    expect(chordBeforeBlank.sections[0].lines.map(line => line.type)).toEqual(['progression', 'spacer', 'lyric']);
  });

  it('preserves lyric and raw trailing spaces in ordinary chart text', () => {
    expect(parseChartBody('[Verse]\nC    G\nInvented line  \ne|--0-2--  \n')).toEqual({
      sections: [{ label: 'Verse', lines: [
        { type: 'lyric', text: 'Invented line  ',
          chords: [{ sym: 'C', pos: 0 }, { sym: 'G', pos: 5 }], confidence: 'low' },
        { type: 'raw', text: 'e|--0-2--  ' },
      ] }],
    });
  });

  it('parses inline brace chords', () => {
    const chart = parseChartBody('[Chorus]\n{C}Sing {G}loud\n');

    expect(chart.sections[0].lines[0]).toEqual({
      type: 'lyric',
      text: 'Sing loud',
      chords: [{ sym: 'C', pos: 0 }, { sym: 'G', pos: 5 }],
      confidence: 'high',
    });
  });

  it('parses the ordinary plain-text body shape emitted by DOCX ingestion', () => {
    const source = '[Verse]\nC G 4x ending cue\n(Chorus) x2\ne|--0-2--\n'
      + '{C}{G}🎸echo  \n        Am\ntail\n';
    expect(parseChartBody(source)).toEqual({ sections: [{ label: 'Verse', lines: [
      { type: 'progression', chords: ['C', 'G'], repeat: 4, note: 'ending cue' },
      { type: 'repeat', ref: 'Chorus', times: 2 },
      { type: 'raw', text: 'e|--0-2--' },
      { type: 'lyric', text: '🎸echo  ', chords: [
        { sym: 'C', pos: 0 }, { sym: 'G', pos: 0 },
      ], confidence: 'high' },
      { type: 'lyric', text: 'tail', chords: [{ sym: 'Am', pos: 8 }], confidence: 'low' },
    ] }] });
    expect(parseChartBody('[Verse]\nC G you and I are walking\n').sections[0].lines[0].type)
      .toBe('lyric');
  });

  it('keeps decorated chord-only rows literal and transposes only clear chord tokens in place', () => {
    const source = 'G  D | C G | Em F | G';
    const parsed = parseChartBody(source);
    expect(parsed.sections[0].lines).toEqual([{ type: 'raw', text: source }]);
    expect(isDecoratedChordLine(source)).toBe(true);

    const moved = transposeDecoratedRawLines(parsed, symbol => ({
      G: 'A', D: 'E', C: 'D', Em: 'F#m', F: 'G',
    })[symbol] || symbol);
    expect(moved.sections[0].lines[0].text).toBe('A  E | D A | F#m G | A');
    expect(transposeDecoratedRawLines(parsed, symbol => transposeChord(symbol, 2, 'A'))
      .sections[0].lines[0].text).toBe('A  E | D A | F#m G | A');

    const slash = parseChartBody('A/C# | D G');
    expect(transposeDecoratedRawLines(slash, symbol => ({
      'A/C#': 'B/D#', D: 'E', G: 'A',
    })[symbol] || symbol).sections[0].lines[0].text).toBe('B/D# | E A');

    const annotated = 'G D (hold Am) | Em F x2 | C';
    const annotatedChart = parseChartBody(annotated);
    expect(annotatedChart.sections[0].lines[0]).toEqual({ type: 'raw', text: annotated });
    expect(transposeDecoratedRawLines(annotatedChart, symbol => ({
      G: 'A', D: 'E', Em: 'F#m', F: 'G', C: 'D',
    })[symbol] || symbol).sections[0].lines[0].text)
      .toBe('A E (hold Am) | F#m G x2 | D');

    const ambiguous = 'G and D | C';
    const ambiguousChart = parseChartBody(ambiguous);
    expect(ambiguousChart.sections[0].lines[0]).toEqual({ type: 'raw', text: ambiguous });
    const unresolved = transposeDecoratedRawLines(ambiguousChart, symbol => `X${symbol}`);
    expect(unresolved).toEqual(ambiguousChart);
  });

  it('matches the shared Python ordered-row recognition and chord spans', () => {
    for (const fixture of orderedRowCases) {
      const text = fixture.analysis_text || fixture.text;
      expect(isDecoratedChordLine(text), fixture.text).toBe(fixture.literal);
      expect(decoratedChordTokenSpans(text).map(span => [span.start, span.end, span.sym]), fixture.text)
        .toEqual(fixture.spans);
      const analysis = decoratedChordAnalysis(text);
      if (Object.hasOwn(fixture, 'partial_transposition')) {
        expect(analysis.partialTransposition).toBe(fixture.partial_transposition);
        expect(analysis.unresolvedTokens.map(token => token.text)).toEqual(fixture.unresolved_tokens || []);
        const moved = transposeDecoratedRawLines(parseChartBody(fixture.text),
          symbol => transposeChord(symbol, 2, 'D'));
        expect(moved.sections[0].lines[0].text).toBe(fixture.expected_transposed);
        expect(moved.sections[0].lines[0].transposition_status).toBe(fixture.expected_status);
      }
      const parsed = parseChartBody(fixture.text);
      const firstLine = parsed.sections[0].lines[0];
      if (fixture.literal) {
        expect(firstLine).toEqual({ type: 'raw', text: fixture.section_tail || fixture.text });
      } else if (fixture.expected_type) {
        expect(firstLine.type).toBe(fixture.expected_type);
        expect(firstLine.chords).toEqual([]);
        expect(firstLine.text).toBe(fixture.analysis_text || fixture.text);
        const moved = transposeDecoratedRawLines(parsed,
          symbol => transposeChord(symbol, 2, 'D'));
        expect(moved).toEqual(parsed);
      } else {
        expect(firstLine).toEqual({ type: 'progression', chords: ['G', 'C', 'G'] });
      }
    }
  });

  it('preserves and transposes interleaved bracketed section tails in place', () => {
    const source = '[Intro] | G | % | Em | % | 2X';
    const tail = ' | G | % | Em | % | 2X';
    const expected = ' | A | % | F#m | % | 2X';
    const parsed = parseChartBody(source);
    expect(parsed.sections).toEqual([{ label: 'Intro', lines: [{ type: 'raw', text: tail }] }]);
    expect(decoratedChordTokenSpans(tail).map(span => [span.start, span.end, span.sym]))
      .toEqual([[3, 4, 'G'], [11, 13, 'Em']]);
    const moved = transposeDecoratedRawLines(parsed, symbol => ({ G: 'A', Em: 'F#m' })[symbol] || symbol);
    expect(moved.sections[0].lines[0].text).toBe(expected);
    const beforeOther = tail.replace('G', '').replace('Em', '');
    const afterOther = expected.replace('A', '').replace('F#m', '');
    expect(afterOther).toBe(beforeOther);
  });

  it('transposes ordered raw chord spans while preserving punctuation and opaque annotations', () => {
    const cases = [
      ['G - C', 'A - D'],
      ['G – C', 'A – D'],
      ['G C (4x)', 'A D (4x)'],
      ['G C 4X', 'A D 4X'],
      ['(D/E A/E E (E repeat))', '(D/E A/E E (E repeat))'],
      ['[Verse] C / G \\ D * Am (2x)', '[Verse] D / A \\ E * Bm (2x)'],
      ['A/C# | Dm7 - G', 'B/D# | Em7 - A'],
      ['G  G   C', 'A  A   D'],
      ['G C (hold Am)', 'A D (hold Am)'],
    ];
    const move = symbol => ({
      G: 'A', C: 'D', D: 'E', Am: 'Bm', 'A/C#': 'B/D#', Dm7: 'Em7',
    })[symbol] || symbol;
    for (const [source, expected] of cases) {
      const parsed = parseChartBody(source);
      expect(transposeDecoratedRawLines(parsed, move).sections[0].lines[0].text).toBe(
        expected.startsWith('[Verse]') ? expected.slice(expected.indexOf(']') + 1) : expected);
    }
    const partialSource = 'C - G - D/G5x';
    const partialChart = parseChartBody(partialSource);
    const partial = transposeDecoratedRawLines(partialChart,
      symbol => ({ C: 'D', G: 'A' })[symbol] || symbol);
    expect(partial.sections[0].lines[0]).toMatchObject({
      text: 'D - A - D/G5x', transposition_status: 'partial',
    });
    expect(partial.sections[0].lines[0].text.slice(-5)).toBe('D/G5x');
    expect(transposeDecoratedRawLines(partialChart,
      symbol => ({ C: 'D', G: 'A' })[symbol] || symbol)).toEqual(partial);
    expect(transposeDecoratedRawLines(partial,
      symbol => ({ D: 'E', A: 'B' })[symbol] || symbol).sections[0].lines[0].text)
      .toBe('E - B - D/G5x');
    expect(transposeDecoratedRawLines(partialChart,
      symbol => transposeChord(symbol, 4, 'E')).sections[0].lines[0].text)
      .toBe('E - B - D/G5x');

    const unsupportedLargerToken = parseChartBody('C - G - D/Guitar5x');
    expect(transposeDecoratedRawLines(unsupportedLargerToken,
      symbol => ({ C: 'D', G: 'A' })[symbol] || symbol).sections[0].lines[0].text)
      .toBe('C - G - D/Guitar5x');
    const ambiguous = parseChartBody('G and D | C');
    expect(transposeDecoratedRawLines(ambiguous, symbol => `X${symbol}`)).toEqual(ambiguous);
  });

  it('keeps literal chord spans attached to the following lyric', () => {
    const source = '  C - G/D  (4x)  \n  lyric placeholder  ';
    const parsed = parseChartBody(source);
    const [raw, lyric] = parsed.sections[0].lines;
    expect(raw).toMatchObject({ type: 'raw', paired_to_next: true });
    expect(raw.chord_spans).toEqual([
      { symbol: 'C', start: 2, end: 3 },
      { symbol: 'G/D', start: 6, end: 9 },
    ]);
    expect(lyric).toMatchObject({ type: 'lyric', text: '  lyric placeholder  ', chords: [] });
    const moved = transposeDecoratedRawLines(parsed,
      symbol => ({ C: 'D', 'G/D': 'A/E' })[symbol] || symbol);
    expect(moved.sections[0].lines[0]).toMatchObject({
      text: '  D - A/E  (4x)  ', paired_to_next: true,
    });
    expect(moved.sections[0].lines[1].text).toBe(lyric.text);
  });

  it('does not transpose prose, section labels, tablature, or raw lines without a clear pipe chart pattern', () => {
    const prose = 'G is the first letter; D is another';
    const chart = parseChartBody(`[Chorus]\n${prose}\ne|--0-2-3--|\n`);
    expect(chart.sections[0].label).toBe('Chorus');
    expect(chart.sections[0].lines.map(line => line.type)).toEqual(['lyric', 'raw']);
    expect(transposeDecoratedRawLines(chart, symbol => `X${symbol}`)).toEqual(chart);
  });

  it('uses UTF-16 columns for ordinary chord positions', () => {
    const ordinary = parseChartBody('[Verse]\nC          G\nx');
    expect(ordinary.sections[0].lines[0].chords.map(chord => chord.pos)).toEqual([0, 11]);
    expect(parseChartBody('[Verse]\n{C}🎸{G}x').sections[0].lines[0].chords)
      .toEqual([{ sym: 'C', pos: 0 }, { sym: 'G', pos: 2 }]);
  });
});
