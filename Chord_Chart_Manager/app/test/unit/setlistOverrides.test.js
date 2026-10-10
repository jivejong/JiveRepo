import { describe, expect, it } from 'vitest';
import {
  buildPerformanceSaveTarget,
  resolveStartingPerformance,
} from '../../src/setlistOverrides.js';

const song = {
  id: 42,
  chart_written_key: 'D',
  preferred_key: 'G',
  default_capo: 3,
};

describe('setlist entry starting performance', () => {
  it('uses both entry overrides when present', () => {
    expect(resolveStartingPerformance(song, {
      transposed_key: 'A', capo_fret: 5,
    })).toEqual({ key: 'A', capo: 5 });
  });

  it('uses the song default capo when only the key is overridden', () => {
    expect(resolveStartingPerformance(song, {
      transposed_key: 'Bb', capo_fret: null,
    })).toEqual({ key: 'Bb', capo: 3 });
  });

  it('uses the song preferred key when only the capo is overridden', () => {
    expect(resolveStartingPerformance(song, {
      transposed_key: null, capo_fret: 4,
    })).toEqual({ key: 'G', capo: 4 });
  });

  it('falls back to preferred key and capo when there are no overrides', () => {
    expect(resolveStartingPerformance(song, {
      transposed_key: null, capo_fret: null,
    })).toEqual({ key: 'G', capo: 3 });
  });

  it('preserves an explicit zero capo override', () => {
    expect(resolveStartingPerformance(song, {
      transposed_key: null, capo_fret: 0,
    })).toEqual({ key: 'G', capo: 0 });
  });

  it('uses written key and capo zero when song preferences are absent', () => {
    expect(resolveStartingPerformance({ chart_written_key: 'Eb' })).toEqual({
      key: 'Eb', capo: 0,
    });
  });

  it('resolves each destination entry as a new reset baseline', () => {
    const destinations = [
      { transposed_key: 'C', capo_fret: 0 },
      { transposed_key: null, capo_fret: 6 },
      { transposed_key: null, capo_fret: null },
    ];
    expect(destinations.map(entry => resolveStartingPerformance(song, entry)))
      .toEqual([{ key: 'C', capo: 0 }, { key: 'G', capo: 6 }, { key: 'G', capo: 3 }]);
  });
});

describe('performance save target', () => {
  it('saves setlist context to the specific setlist entry', () => {
    expect(buildPerformanceSaveTarget({
      song,
      setlistId: 9,
      setlistEntry: { position: 2 },
    }, 'Bb', 0)).toEqual({
      kind: 'setlist',
      setlistId: 9,
      position: 2,
      payload: { transposed_key: 'Bb', capo_fret: 0 },
    });
  });

  it('keeps song-list saves on the song preference fields', () => {
    expect(buildPerformanceSaveTarget({ song }, 'A', 2)).toEqual({
      kind: 'song',
      songId: 42,
      payload: { preferred_key: 'A', default_capo: 2 },
    });
  });
});
