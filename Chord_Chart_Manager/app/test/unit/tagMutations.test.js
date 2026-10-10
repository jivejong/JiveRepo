import { describe, expect, it } from 'vitest';
import { buildCachedTagOptions, eraForYear, matchesSongTagFilters, normalizeSongTags, normalizeTagNames } from '../../src/tagMutations.js';
import { editablePayload } from '../../src/lib/cache.js';

describe('tag editing data', () => {
  it('trims and collapses whitespace and removes case-insensitive duplicates', () => {
    expect(normalizeTagNames(['  Indie   Rock ', 'indie rock', 'Alt Pop']))
      .toEqual(['Indie Rock', 'Alt Pop']);
  });

  it('preserves the selected primary genre while normalizing assignments', () => {
    expect(normalizeSongTags([
      { name: '  Indie  ', is_primary: false },
      { name: ' Rock ', is_primary: true },
      { name: 'rock', is_primary: false },
    ], ['  Dream Pop ', 'dream pop'])).toEqual({
      genres: [{ name: 'Indie', is_primary: false }, { name: 'Rock', is_primary: true }],
      vibes: ['Dream Pop'],
    });
  });

  it('derives Era from an exact release year and leaves missing years unset', () => {
    expect(eraForYear(1977)).toBe('1970s');
    expect(eraForYear('2003')).toBe('2000s');
    expect(eraForYear('')).toBeNull();
    expect(eraForYear('not a year')).toBeNull();
  });

  it('carries complete tag sets in song updates and preserves omitted fields', () => {
    const complete = editablePayload({ genres: [], vibes: [], release_year: null });
    expect(complete.genres).toEqual([]);
    expect(complete.vibes).toEqual([]);
    expect(complete.release_year).toBeNull();
    const omitted = JSON.parse(JSON.stringify(editablePayload({ title: 'Example' })));
    expect(Object.hasOwn(omitted, 'genres')).toBe(false);
    expect(Object.hasOwn(omitted, 'vibes')).toBe(false);
  });

  it('keeps cached unassigned category values available and adds offline values from songs', () => {
    const options = buildCachedTagOptions([
      { genres: [{ name: 'New Genre', is_primary: true }], vibes: ['Offline Vibe'], release_year: 2003 },
    ], { genres: ['Existing Genre'], vibes: ['Existing Vibe'], eras: ['1970s'] });
    expect(options).toContainEqual(expect.objectContaining({ name: 'Existing Genre', category: 'Genre', song_count: 0 }));
    expect(options).toContainEqual(expect.objectContaining({ name: 'Existing Vibe', category: 'Feel', song_count: 0 }));
    expect(options).toContainEqual(expect.objectContaining({ name: 'New Genre', category: 'Genre', song_count: 1 }));
    expect(options).toContainEqual(expect.objectContaining({ name: 'Offline Vibe', category: 'Feel', song_count: 1 }));
    expect(options).toContainEqual(expect.objectContaining({ name: '1970s', category: 'Era', song_count: 0 }));
  });

  it('keeps secondary genres and equal names in different categories distinct', () => {
    const song = {
      genres: [{ name: 'Rock', is_primary: true }, { name: 'Shared', is_primary: false }],
      vibes: ['Shared'], release_year: 2003,
    };
    expect(matchesSongTagFilters(song, [{ category: 'Genre', name: 'Shared' }])).toBe(true);
    expect(matchesSongTagFilters(song, [{ category: 'Feel', name: 'Shared' }])).toBe(true);
    expect(matchesSongTagFilters(song, [
      { category: 'Genre', name: 'Shared' }, { category: 'Feel', name: 'Shared' },
      { category: 'Era', name: '2000s' },
    ])).toBe(true);
    expect(matchesSongTagFilters({ ...song, vibes: [] }, [{ category: 'Feel', name: 'Shared' }])).toBe(false);
    expect(matchesSongTagFilters({ ...song, release_year: null, era: '2000s' },
      [{ category: 'Era', name: '2000s' }])).toBe(false);
  });
});
