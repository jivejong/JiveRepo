import { describe, expect, it } from 'vitest';
import {
  keyAfter,
  keyDelta,
  soundingKey,
  transposeChart,
  transposeChord,
} from '../../transpose.js';

describe('transposeChord', () => {
  it('preserves chord quality while moving root and slash bass', () => {
    expect(transposeChord('Cmaj7/E', 2, 'D')).toBe('Dmaj7/F#');
  });

  it('spells flat target keys with flats', () => {
    expect(transposeChord('A#', 0, 'Bb')).toBe('Bb');
  });
});

describe('key helpers', () => {
  it('computes a signed shortest key delta', () => {
    expect(keyDelta('G', 'A')).toBe(2);
    expect(keyDelta('C', 'B')).toBe(-1);
  });

  it('derives conventional displayed and sounding keys', () => {
    expect(keyAfter('G', 3)).toBe('Bb');
    expect(soundingKey('D', 2)).toBe('E');
  });
});

describe('transposeChart', () => {
  it('transposes lyric and progression chords without changing lyrics', () => {
    const input = {
      sections: [{
        label: 'Verse',
        lines: [
          { type: 'lyric', text: 'Amazing grace', chords: [{ sym: 'G', pos: 0 }] },
          { type: 'progression', chords: ['G', 'C', 'D'] },
        ],
      }],
    };

    expect(transposeChart(input, 2, 'A')).toEqual({
      sections: [{
        label: 'Verse',
        lines: [
          { type: 'lyric', text: 'Amazing grace', chords: [{ sym: 'A', pos: 0 }] },
          { type: 'progression', chords: ['A', 'D', 'E'] },
        ],
      }],
    });
  });
});
