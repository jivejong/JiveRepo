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
  const primary = genres.find(tag => typeof tag === 'object' && tag?.is_primary)?.name;
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
  return selected.every(tag => have.has(`${tag.category}:${tag.name.trim().toLocaleLowerCase()}`))
    && legacyNames.every(name => names.has(name.toLocaleLowerCase()));
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
