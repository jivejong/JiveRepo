import { primaryGenreFromAssignments } from './lib/offlineSongFields.js';

export function normalizeTagNames(values = []) {
  if (!Array.isArray(values)) throw new Error('Tags must be a list');
  const seen = new Set();
  const names = [];
  for (const value of values) {
    const name = String(value?.name ?? value ?? '').trim().replace(/\s+/g, ' ');
    if (!name) continue;
    const key = name.toLocaleLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      names.push(name);
    }
  }
  return names;
}

export function normalizeSongTags(genres = [], vibes = []) {
  const primary = primaryGenreFromAssignments(genres);
  const genreNames = normalizeTagNames(genres);
  const primaryKey = String(primary || '').trim().toLocaleLowerCase();
  return {
    genres: genreNames.map((name, index) => ({
      name,
      is_primary: primaryKey ? name.toLocaleLowerCase() === primaryKey : index === 0,
    })),
    vibes: normalizeTagNames(vibes),
  };
}

export function eraForYear(year) {
  const value = year === '' || year === null || year === undefined ? null : Number(year);
  return Number.isInteger(value) ? `${Math.floor(value / 10) * 10}s` : null;
}

export function matchesSongTagFilters(song, selected = [], legacyNames = []) {
  const have = new Set();
  const names = new Set();
  for (const genre of song.genres || []) {
    const name = String(genre.name || genre).toLocaleLowerCase();
    have.add(`Genre:${name}`); names.add(name);
  }
  for (const vibe of song.vibes || []) {
    const name = String(vibe).toLocaleLowerCase();
    have.add(`Feel:${name}`); names.add(name);
  }
  const era = eraForYear(song.release_year);
  if (era) {
    const name = era.toLocaleLowerCase();
    have.add(`Era:${name}`); names.add(name);
  }
  const byCategory = new Map();
  for (const tag of selected) {
    const category = tag.category === 'Vibe' ? 'Feel' : tag.category;
    if (!byCategory.has(category)) byCategory.set(category, []);
    byCategory.get(category).push(tag.name.trim().toLocaleLowerCase());
  }
  // A selected category matches any one of its selected values; distinct
  // categories still combine with AND. This mirrors the online SQL filters.
  return [...byCategory].every(([category, values]) =>
    values.some(name => have.has(`${category}:${name}`)))
    && legacyNames.every(name => names.has(name.toLocaleLowerCase()));
}

// Build the list view from an unfiltered server snapshot plus only local rows
// with pending mutations. This lets an offline edit enter or leave a filter
// before its push is acknowledged without making a filtered server response
// authoritative for the whole local library.
export function mergePendingSongProjections(serverSongs = [], cachedSongs = []) {
  const effective = new Map(serverSongs.map(song => [String(song.id), song]));
  for (const song of cachedSongs) {
    const operation = song?._sync?.operation;
    if (!operation) continue;
    const id = String(song.id);
    if (operation === 'delete') effective.delete(id);
    else effective.set(id, song);
  }
  return [...effective.values()];
}

export function filterSongList(songs = [], filters = {}) {
  const text = String(filters.q ?? filters.query ?? '').trim().toLocaleLowerCase();
  const legacyNames = (typeof filters.tags === 'string' ? filters.tags.split(',') : (filters.tags || []))
    .map(tag => String(tag).trim().toLocaleLowerCase()).filter(Boolean);
  const selected = filters.tag_filters
    ? (typeof filters.tag_filters === 'string' ? JSON.parse(filters.tag_filters) : filters.tag_filters)
    : [];
  return songs.filter(song => {
    if (song?._sync?.operation === 'delete') return false;
    if (text && !String(song.title || '').toLocaleLowerCase().includes(text)
        && !String(song.artist || '').toLocaleLowerCase().includes(text)) return false;
    return matchesSongTagFilters(song, selected, legacyNames);
  });
}

export function buildCachedTagOptions(songs = [], facets = {}) {
  const options = new Map();
  const add = (category, name, count = false) => {
    const value = String(name ?? '').trim();
    if (!value) return null;
    const key = `${category}:${value.toLocaleLowerCase()}`;
    const option = options.get(key) || { id: key, name: value, category, song_count: 0 };
    if (count) option.song_count += 1;
    options.set(key, option);
    return key;
  };

  (facets.genres || []).forEach(name => add('Genre', name));
  (facets.vibes || []).forEach(name => add('Feel', name));
  (facets.eras || []).forEach(name => add('Era', name));
  for (const song of songs) {
    if (song._sync?.operation === 'delete') continue;
    const seen = new Set();
    for (const genre of song.genres || []) {
      const name = genre.name || genre;
      const key = `Genre:${String(name).trim().toLocaleLowerCase()}`;
      if (!seen.has(key)) { add('Genre', name, true); seen.add(key); }
    }
    for (const vibe of song.vibes || []) {
      const key = `Feel:${String(vibe).trim().toLocaleLowerCase()}`;
      if (!seen.has(key)) { add('Feel', vibe, true); seen.add(key); }
    }
    const era = eraForYear(song.release_year);
    if (era) {
      const key = `Era:${era.toLocaleLowerCase()}`;
      if (!seen.has(key)) { add('Era', era, true); seen.add(key); }
    }
  }
  return [...options.values()];
}
