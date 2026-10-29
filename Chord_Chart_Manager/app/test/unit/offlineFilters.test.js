import 'fake-indexeddb/auto';
import { afterEach, describe, expect, it } from 'vitest';
import * as cache from '../../src/lib/cache.js';
import { filterSongList, mergePendingSongProjections } from '../../src/tagMutations.js';

const fixtureIds = [];
afterEach(async () => {
  for (const id of fixtureIds.splice(0)) {
    await cache.clearDirty(id);
    await cache.deleteCachedSong(id);
  }
});

describe('offline categorized filters', () => {
  it('FR-002 FR-057 overlays pending create/update/delete on a full server list before applying filters', () => {
    const server = [
      { id: 1, title: 'Changed into match', artist: 'Artist', genres: [{ name: 'Old', is_primary: true }], vibes: [], release_year: 1978 },
      { id: 2, title: 'Changed out of match', artist: 'Artist', genres: [{ name: 'Target', is_primary: true }], vibes: ['Calm'], release_year: 1978 },
      { id: 3, title: 'Deleted', artist: 'Artist', genres: [{ name: 'Target', is_primary: true }], vibes: ['Calm'], release_year: 1978 },
    ];
    const cached = [
      { ...server[0], genres: [{ name: 'Target', is_primary: true }], vibes: ['Calm'], _sync: { operation: 'update', base_updated_at: 'v1' } },
      { ...server[1], genres: [{ name: 'Other', is_primary: true }], vibes: [], release_year: null,
        _sync: { operation: 'update', base_updated_at: 'v2' } },
      { ...server[2], _sync: { operation: 'delete', base_updated_at: 'v3' } },
      { id: 'local-1', title: 'Offline create', artist: 'Artist', genres: [{ name: 'Other', is_primary: true }],
        vibes: ['Driving'], release_year: 1982, _sync: { operation: 'create' } },
    ];
    const merged = mergePendingSongProjections(server, cached);
    expect(merged).toHaveLength(3);
    expect(merged.find(song => song.id === 1).genres[0].name).toBe('Target');
    expect(merged.find(song => song.id === 2).release_year).toBeNull();
    expect(merged.some(song => song.id === 3)).toBe(false);
    const selected = [{ category: 'Genre', name: 'Target' }, { category: 'Vibe', name: 'Calm' }, { category: 'Era', name: '1970s' }];
    expect(filterSongList(merged, { q: 'changed', tag_filters: selected }).map(song => song.id)).toEqual([1]);
    expect(server[0].genres[0].name).toBe('Old');
    expect(server[1].genres[0].name).toBe('Target');
  });

  it('FR-002 FR-003 filters cached pending edits and local songs with online-equivalent OR/AND semantics', async () => {
    const suffix = `T18 ${Date.now()} ${Math.random()}`;
    const records = [
      { id: `local-${suffix}-edited`, title: `${suffix} edited`, genres: [
        { name: 'Primary', is_primary: true }, { name: 'Secondary', is_primary: false },
      ], vibes: ['Calm', 'Driving'], release_year: 1980, _sync: { operation: 'update', base_updated_at: 'v1' } },
      { id: `local-${suffix}-created`, title: `${suffix} created`, genres: [{ name: 'Alternate', is_primary: true }],
        vibes: ['Calm'], release_year: 1979, _sync: { operation: 'create' } },
      { id: `local-${suffix}-missing`, title: `${suffix} missing`, genres: [{ name: 'Secondary' }], vibes: ['Calm'],
        release_year: null, era: '1980s', _sync: { operation: 'create' } },
    ];
    fixtureIds.push(...records.map(song => song.id));
    for (const song of records) await cache.savePendingSong(song);

    const selected = [
      { category: 'Genre', name: 'Primary' }, { category: 'Genre', name: 'Secondary' },
      { category: 'Genre', name: 'Alternate' },
      { category: 'Vibe', name: 'Calm' }, { category: 'Vibe', name: 'Driving' },
      { category: 'Era', name: '1970s' }, { category: 'Era', name: '1980s' },
    ];
    const results = await cache.searchCached({ tag_filters: JSON.stringify(selected), q: suffix });
    expect(results.map(song => song.id).sort()).toEqual(records.slice(0, 2).map(song => song.id).sort());

    const secondaryOnly = await cache.searchCached({
      tag_filters: JSON.stringify([{ category: 'Genre', name: 'Secondary' }, { category: 'Vibe', name: 'Calm' }]), q: suffix,
    });
    expect(secondaryOnly.map(song => song.id)).toEqual([records[0].id, records[2].id].sort((a, b) => a.localeCompare(b)));
    const missingEra = await cache.searchCached({
      tag_filters: JSON.stringify([{ category: 'Era', name: '1980s' }]), q: records[2].title,
    });
    expect(missingEra).toEqual([]);
  });
});
