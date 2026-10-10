import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const {
  chartToText, isGeneratedChartSource, isGeneratedSourceUnchanged, parseChartBody,
} = require('../../server/chartParser.js');

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

  it('parses inline braces and round-trips structured chart text', () => {
    const chart = parseChartBody('[Chorus]\n{C}Sing {G}loud\n');

    expect(chart.sections[0].lines[0]).toEqual({
      type: 'lyric',
      text: 'Sing loud',
      chords: [{ sym: 'C', pos: 0 }, { sym: 'G', pos: 5 }],
      confidence: 'high',
    });
    expect(chartToText(chart)).toContain('[[CCM-CHART:2]]');
    expect(parseChartBody(chartToText(chart))).toEqual(chart);
  });

  it('round-trips every structured generated line, escaped text and boundary/colliding anchors', () => {
    const structured = {
      sections: [{ label: 'Verse [one]', lines: [
        { type: 'progression', chords: ['C', 'G'], repeat: 4, note: 'invented [[note]] cue' },
        { type: 'repeat', ref: 'Verse 1', times: 2 },
        { type: 'raw', text: 'e|--[tab]\\' },
        { type: 'spacer' },
        { type: 'lyric', text: '{invented} phrase', chords: [
          { sym: 'C#', pos: 0 }, { sym: 'G', pos: 0 }, { sym: 'Am', pos: 16 },
        ], confidence: 'low' },
        { type: 'lyric', text: 'Ends here', chords: [{ sym: 'F', pos: 9 }], confidence: 'high' },
      ] }],
    };
    const source = chartToText(structured);
    expect(source.startsWith('[[CCM-CHART:2]]\n')).toBe(true);
    expect(parseChartBody(source)).toEqual(structured);
    expect(chartToText(parseChartBody(source))).toBe(source);
  });

  it('keeps an unrelated progression intact when a generated lyric is deliberately edited', () => {
    const structured = { sections: [{ label: 'Invented section', lines: [
      { type: 'progression', chords: ['C', 'G'], note: 'invented ending cue' },
      { type: 'lyric', text: 'Invented harmless phrase', confidence: 'high',
        chords: [{ sym: 'Am', pos: 18 }] },
      { type: 'raw', text: 'tab|--invented' },
    ] }] };
    const source = chartToText(structured);
    const edited = source.replace('harmless', 'gentle');
    const parsed = parseChartBody(edited);

    expect(parsed.sections[0].lines).toEqual([
      structured.sections[0].lines[0],
      { ...structured.sections[0].lines[1], text: 'Invented gentle phrase' },
      structured.sections[0].lines[2],
    ]);
  });

  it('continues to read legacy version 1 generated source', () => {
    const legacy = '[[CCM-CHART:1]]\n[[CCM-SECTION]]Verse\n[[CCM-LYRIC:low]]A[[C:G]]B\n';
    expect(parseChartBody(legacy).sections[0].lines[0]).toEqual({
      type: 'lyric', text: 'AB', chords: [{ sym: 'G', pos: 1 }], confidence: 'low',
    });
  });

  it('uses UTF-16 columns and preserves anchors beyond lyric end', () => {
    const structured = { sections: [{ label: 'Unicode', lines: [
      { type: 'lyric', text: '🎸x', chords: [{ sym: 'Am', pos: 4 }], confidence: 'high' },
    ] }] };
    const source = chartToText(structured);
    expect(parseChartBody(source)).toEqual(structured);
    expect(source).toContain('[[C:4:Am]]');
    const ordinary = parseChartBody('[Verse]\nC          G\nx');
    expect(ordinary.sections[0].lines[0].chords.map(chord => chord.pos)).toEqual([0, 11]);
    expect(parseChartBody('[Verse]\n{C}🎸{G}x').sections[0].lines[0].chords)
      .toEqual([{ sym: 'C', pos: 0 }, { sym: 'G', pos: 2 }]);
    expect(() => chartToText({ sections: [{ label: null, lines: [
      { type: 'lyric', text: 'x', chords: [{ sym: 'C', pos: -1 }] },
    ] }] })).toThrow(/UTF-16 column/);
  });

  it('round-trips explicit spacers and removes boundary duplicates', () => {
    const chart = { sections: [
      { label: 'Verse', lines: [
        { type: 'lyric', text: 'one', chords: [], confidence: 'high' },
        { type: 'spacer' },
        { type: 'lyric', text: 'two', chords: [], confidence: 'high' },
        { type: 'spacer' },
      ] },
      { label: 'Chorus', lines: [
        { type: 'spacer' },
        { type: 'lyric', text: 'three', chords: [], confidence: 'high' },
      ] },
    ] };
    const parsed = parseChartBody(chartToText(chart));
    expect(parsed.sections[0].lines.map(line => line.type)).toEqual(['lyric', 'spacer', 'lyric']);
    expect(parsed.sections[1].lines.map(line => line.type)).toEqual(['spacer', 'lyric']);
  });

  it('rejects missing or malformed generated type markers instead of reclassifying them', () => {
    const generated = chartToText({ sections: [{ label: 'Verse', lines: [
      { type: 'progression', chords: ['C', 'G'] },
    ] }] });
    expect(() => parseChartBody(generated.replace('[[CCM-PROGRESSION]]', '')))
      .toThrow(/missing or unsupported line marker/);
    expect(parseChartBody('[Verse]\nC   G\nInvented line\n').sections[0].lines[0].chords)
      .toEqual([{ sym: 'C', pos: 0 }, { sym: 'G', pos: 4 }]);
  });

  it('preserves unchanged chart source for generated and human-authored songs', () => {
    const structured = {
      sections: [{ label: 'Invented section', lines: [
        { type: 'progression', chords: ['C', 'G'], note: 'invented ending cue' },
      ] }],
    };
    const generated = chartToText(structured);
    const authored = '[Verse]\nC   G\nInvented line\n';

    expect(isGeneratedChartSource(generated)).toBe(true);
    expect(isGeneratedChartSource(authored)).toBe(false);
    expect(isGeneratedSourceUnchanged(null, structured, generated)).toBe(true);
    expect(isGeneratedSourceUnchanged(generated, structured, generated)).toBe(true);
    expect(isGeneratedSourceUnchanged(authored, structured, authored)).toBe(true);
    expect(isGeneratedSourceUnchanged(null, structured, `${generated}edited`)).toBe(false);
  });
});
