import { describe, expect, it } from 'vitest';
import { readSongSortPreference, sortSongs, SONG_SORT_STORAGE_KEY, writeSongSortPreference } from '../../src/lib/songSort.js';

const song = (id, title, fields = {}) => ({ id, title, ...fields });

describe('song list sorting', () => {
  it('defaults to title A–Z and uses numeric ID as the final stable tie-breaker', () => {
    const rows = [song(12, 'Alpha'), song(20, 'beta'), song(2, 'ALPHA')];
    expect(sortSongs(rows).map(item => item.id)).toEqual([2, 12, 20]);
    expect(rows.map(item => item.id)).toEqual([12, 20, 2]);
  });

  it('includes offline-created and pending-edited local records without changing their cached state', () => {
    const rows = [song('local-2', 'Zulu draft', { _sync: { operation: 'update' }, updated_at: '2026-10-11T12:00:00.000001Z' }),
      song('local-1', 'Alpha draft', { _sync: { operation: 'create' }, created_at: '2026-10-11T12:00:00Z' })];
    const before = structuredClone(rows);
    const sorted = sortSongs(rows);
    expect(sorted.map(item => item.id)).toEqual(['local-1', 'local-2']);
    expect(rows).toEqual(before);
    expect(sorted.find(item => item.id === 'local-2')._sync.operation).toBe('update');
  });

  it('sorts creation timestamps oldest-first and keeps missing or invalid timestamps last in either direction', () => {
    const rows = [song(3, 'Third', { created_at: 'invalid' }), song(2, 'Second', { created_at: '2024-01-02T00:00:00Z' }),
      song(1, 'First', { created_at: '2024-01-01T00:00:00Z' }), song(4, 'No date')];
    expect(sortSongs(rows, { key: 'created_at' }).map(item => item.id)).toEqual([1, 2, 4, 3]);
    expect(sortSongs(rows, { key: 'created_at', direction: 'desc' }).map(item => item.id)).toEqual([2, 1, 4, 3]);
  });

  it('sorts by artist case-insensitively, with title tie-break and missing artists last', () => {
    const rows = [song(1, 'Bravo', { artist: 'Same' }), song(2, 'Alpha', { artist: 'same' }),
      song(3, 'No artist'), song(4, 'Zed', { artist: 'Zulu' })];
    expect(sortSongs(rows, { key: 'artist' }).map(item => item.id)).toEqual([2, 1, 4, 3]);
    expect(sortSongs(rows, { key: 'artist', direction: 'desc' }).map(item => item.id)).toEqual([4, 2, 1, 3]);
  });

  it('uses only the primary genre value and leaves unassigned songs last', () => {
    const rows = [song(1, 'Song', { genres: [{ name: 'Zebra', is_primary: false }, { name: 'Folk', is_primary: true }] }),
      song(2, 'No genre'), song(3, 'Alias', { primary_genre: 'Jazz' }),
      song(4, 'Secondary only', { primary_genre: null, genre: 'Fable', genres: [{ name: 'Fable', is_primary: false }] })];
    expect(sortSongs(rows, { key: 'genre' }).map(item => item.id)).toEqual([1, 3, 2, 4]);
    expect(sortSongs(rows, { key: 'genre', direction: 'desc' }).map(item => item.id)).toEqual([3, 1, 2, 4]);
  });

  it('uses the alphabetically first case-insensitive vibe, including local string/object assignments', () => {
    const rows = [song(1, 'Zed', { vibes: ['zesty', 'Ballad'] }),
      song(2, 'Alpha', { vibes: [{ name: 'Cove' }, { name: 'Rock' }] }), song(3, 'No vibe')];
    expect(sortSongs(rows, { key: 'vibe' }).map(item => item.id)).toEqual([1, 2, 3]);
    expect(sortSongs(rows, { key: 'vibe', direction: 'desc' }).map(item => item.id)).toEqual([2, 1, 3]);
  });

  it('sorts derived eras and keeps missing release years last', () => {
    const rows = [song(1, 'Eighties', { release_year: 1988 }), song(2, 'Unknown'),
      song(3, 'Sixties', { release_year: 1965 })];
    expect(sortSongs(rows, { key: 'era' }).map(item => item.id)).toEqual([3, 1, 2]);
    expect(sortSongs(rows, { key: 'era', direction: 'desc' }).map(item => item.id)).toEqual([1, 3, 2]);
  });

  it('persists validated sort choices for offline/reload use and safely defaults on invalid or inaccessible storage', () => {
    const values = new Map();
    const store = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
    expect(readSongSortPreference(store)).toEqual({ key: 'title', direction: 'asc' });
    expect(writeSongSortPreference({ key: 'vibe', direction: 'desc' }, store)).toBe(true);
    expect(readSongSortPreference(store)).toEqual({ key: 'vibe', direction: 'desc' });
    values.set(SONG_SORT_STORAGE_KEY, '{bad json');
    expect(readSongSortPreference(store)).toEqual({ key: 'title', direction: 'asc' });
    expect(readSongSortPreference({ getItem() { throw new Error('blocked'); } }))
      .toEqual({ key: 'title', direction: 'asc' });
    expect(writeSongSortPreference({ key: 'unknown', direction: 'asc' }, store)).toBe(false);
  });
});
