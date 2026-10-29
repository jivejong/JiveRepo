import { describe, expect, it } from 'vitest';
import { buildCachedTagOptions, eraForYear, matchesSongTagFilters, normalizeSongTags, normalizeTagNames } from '../../src/tagMutations.js';
import { editablePayload } from '../../src/lib/cache.js';

describe('tag editing data', () => {
  it('FR-015 trims and collapses whitespace and removes case-insensitive duplicates', () => {
    expect(normalizeTagNames(['  Indie   Rock ', 'indie rock', 'Alt Pop']))
      .toEqual(['Indie Rock', 'Alt Pop']);
  });

  it('FR-017 FR-018 preserves the selected primary genre while normalizing assignments', () => {
    expect(normalizeSongTags([
      { name: '  Indie  ', is_primary: false },
      { name: ' Rock ', is_primary: true },
      { name: 'rock', is_primary: false },
    ], ['  Dream Pop ', 'dream pop'])).toEqual({
      genres: [{ name: 'Indie', is_primary: false }, { name: 'Rock', is_primary: true }],
      vibes: ['Dream Pop'],
    });
  });

  it('FR-005 FR-014 derives Era from an exact release year and leaves missing years unset', () => {
    expect(eraForYear(1977)).toBe('1970s');
    expect(eraForYear('2003')).toBe('2000s');
    expect(eraForYear('')).toBeNull();
    expect(eraForYear('not a year')).toBeNull();
  });

  it('FR-015 carries complete tag sets in song updates and preserves omitted fields', () => {
    const complete = editablePayload({ genres: [], vibes: [], release_year: null });
    expect(complete.genres).toEqual([]);
    expect(complete.vibes).toEqual([]);
    expect(complete.release_year).toBeNull();
    const omitted = JSON.parse(JSON.stringify(editablePayload({ title: 'Example' })));
    expect(Object.hasOwn(omitted, 'genres')).toBe(false);
    expect(Object.hasOwn(omitted, 'vibes')).toBe(false);
    const unchangedAssignments = editablePayload({ genres: [{ name: 'Rock', is_primary: true }], vibes: ['Moody'],
      _sync: { operation: 'update', genres_unchanged: true, vibes_unchanged: true } });
    expect(unchangedAssignments).not.toHaveProperty('genres');
    expect(unchangedAssignments).not.toHaveProperty('vibes');
    const changedAssignment = editablePayload({ genres: [{ name: 'Rock', is_primary: true }], vibes: ['Moody'],
      _sync: { operation: 'update', genres_unchanged: false, vibes_unchanged: true } });
    expect(changedAssignment.genres).toEqual([{ name: 'Rock', is_primary: true }]);
    expect(changedAssignment).not.toHaveProperty('vibes');
    const createPayload = editablePayload({ genres: [], vibes: [],
      _sync: { operation: 'create', genres_unchanged: true, vibes_unchanged: true } });
    expect(createPayload).toMatchObject({ genres: [], vibes: [] });
  });

  it('FR-002 FR-056 keeps cached unassigned category values available and adds offline values from songs', () => {
    const options = buildCachedTagOptions([
      { genres: [{ name: 'New Genre', is_primary: true }], vibes: ['Offline Vibe'], release_year: 2003 },
    ], { genres: ['Existing Genre'], vibes: ['Existing Vibe'], eras: ['1970s'] });
    expect(options).toContainEqual(expect.objectContaining({ name: 'Existing Genre', category: 'Genre', song_count: 0 }));
    expect(options).toContainEqual(expect.objectContaining({ name: 'Existing Vibe', category: 'Feel', song_count: 0 }));
    expect(options).toContainEqual(expect.objectContaining({ name: 'New Genre', category: 'Genre', song_count: 1 }));
    expect(options).toContainEqual(expect.objectContaining({ name: 'Offline Vibe', category: 'Feel', song_count: 1 }));
    expect(options).toContainEqual(expect.objectContaining({ name: '1970s', category: 'Era', song_count: 0 }));
  });

  it('FR-003 FR-004 keeps secondary genres and equal names in different categories distinct', () => {
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

  it('FR-002 FR-005 uses OR within each selected category, AND across categories, and derives Era at decade boundaries', () => {
    const song = {
      genres: [{ name: 'Primary', is_primary: true }, { name: 'Secondary', is_primary: false }],
      vibes: ['Calm', 'Driving'], release_year: 1980,
    };
    expect(matchesSongTagFilters(song, [
      { category: 'Genre', name: 'Missing' }, { category: 'Genre', name: 'Secondary' },
      { category: 'Vibe', name: 'Other' }, { category: 'Vibe', name: 'Calm' },
      { category: 'Era', name: '1970s' }, { category: 'Era', name: '1980s' },
    ])).toBe(true);
    expect(matchesSongTagFilters(song, [
      { category: 'Genre', name: 'Secondary' }, { category: 'Vibe', name: 'Missing' },
    ])).toBe(false);
    expect(eraForYear(1979)).toBe('1970s');
    expect(eraForYear(1980)).toBe('1980s');
    expect(matchesSongTagFilters({ ...song, release_year: undefined }, [
      { category: 'Era', name: '1980s' },
    ])).toBe(false);
  });
});
