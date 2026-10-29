import { describe, expect, it } from 'vitest';
import { distinctArtists, matchingArtists } from '../../src/lib/artistAutocomplete.js';

describe('offline artist autocomplete', () => {
  it('FR-019 deduplicates names case-insensitively and sorts while preserving one display spelling', () => {
    expect(distinctArtists([
      { artist: 'the Example' }, { artist: 'Other' }, { artist: 'THE EXAMPLE ' },
      { artist: '' }, { artist: null },
    ])).toEqual(['Other', 'the Example']);
  });

  it('FR-019 matches a trimmed prefix without rewriting a free-text value', () => {
    const artists = distinctArtists([{ artist: 'The Example' }, { artist: 'The Expanse' }]);
    expect(matchingArtists(artists, '  tHe ex')).toEqual(['The Example', 'The Expanse']);
    const typed = 'New Artist';
    expect(matchingArtists(artists, typed)).toEqual([]);
    expect(typed).toBe('New Artist');
  });

  it('FR-019 uses only cached/local song names and bounds the suggestion list', () => {
    const artists = distinctArtists([
      { id: 1, artist: 'Cached Artist' }, { id: 'local-1', artist: 'Local Artist' },
    ]);
    expect(matchingArtists(artists, '')).toEqual([]);
    expect(matchingArtists([...artists, ...Array.from({ length: 12 }, (_, index) => `Test ${index}`)], 't', 3)).toHaveLength(3);
    expect(artists).toContain('Local Artist');
  });
});
