import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { chartToText, parseChartBody } = require('../../server/chartParser.js');

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

  it('parses inline braces and round-trips structured chart text', () => {
    const chart = parseChartBody('[Chorus]\n{C}Sing {G}loud\n');

    expect(chart.sections[0].lines[0]).toEqual({
      type: 'lyric',
      text: 'Sing loud',
      chords: [{ sym: 'C', pos: 0 }, { sym: 'G', pos: 5 }],
      confidence: 'high',
    });
    expect(chartToText(chart)).toBe('[Chorus]\nC    G\nSing loud\n');
  });
});
