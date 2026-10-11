export const SONG_SORTS = Object.freeze([
  { value: 'title', label: 'Title' },
  { value: 'created_at', label: 'Date added' },
  { value: 'artist', label: 'Artist' },
  { value: 'genre', label: 'Genre' },
  { value: 'era', label: 'Era' },
  { value: 'vibe', label: 'Vibe' },
]);

export const SONG_SORT_STORAGE_KEY = 'ccm-song-sort';
const VALID_SORTS = new Set(SONG_SORTS.map(item => item.value));
const collator = new Intl.Collator(undefined, { sensitivity: 'base', numeric: true });

function storage() {
  try { return globalThis.localStorage; } catch { return null; }
}

export function readSongSortPreference(target = undefined) {
  const store = target === undefined ? storage() : target;
  try {
    const parsed = JSON.parse(store?.getItem(SONG_SORT_STORAGE_KEY) || 'null');
    if (parsed && VALID_SORTS.has(parsed.key) && (parsed.direction === 'asc' || parsed.direction === 'desc')) {
      return { key: parsed.key, direction: parsed.direction };
    }
  } catch { /* Missing or inaccessible local storage uses the default. */ }
  return { key: 'title', direction: 'asc' };
}

export function writeSongSortPreference(preference, target = undefined) {
  if (!preference || !VALID_SORTS.has(preference.key)
      || !['asc', 'desc'].includes(preference.direction)) return false;
  const store = target === undefined ? storage() : target;
  try {
    if (!store) return false;
    store.setItem(SONG_SORT_STORAGE_KEY, JSON.stringify(preference));
    return true;
  } catch { return false; }
}

function textCompare(left, right) {
  const a = String(left);
  const b = String(right);
  return collator.compare(a, b);
}

function primaryGenre(song) {
  if (Object.hasOwn(song, 'primary_genre')) {
    return typeof song.primary_genre === 'string' && song.primary_genre.trim() ? song.primary_genre : null;
  }
  const genres = Array.isArray(song.genres) ? song.genres : [];
  if (genres.length) {
    const primary = genres.find(item => item && typeof item === 'object' && item.is_primary);
    return primary?.name || null;
  }
  return typeof song.genre === 'string' && song.genre.trim() ? song.genre : null;
}

function firstVibe(song) {
  const vibes = (Array.isArray(song.vibes) ? song.vibes : [])
    .map(item => typeof item === 'string' ? item : item?.name)
    .filter(value => typeof value === 'string' && value.trim());
  return vibes.sort(textCompare)[0] || null;
}

function sortValue(song, key) {
  switch (key) {
    case 'created_at': {
      if (!song.created_at) return null;
      const timestamp = Date.parse(song.created_at);
      return Number.isFinite(timestamp) ? timestamp : null;
    }
    case 'artist': return typeof song.artist === 'string' && song.artist.trim() ? song.artist : null;
    case 'genre': return primaryGenre(song);
    case 'era': {
      if (typeof song.era === 'string' && song.era.trim()) return song.era;
      const year = Number(song.release_year);
      return Number.isInteger(year) && year > 0 ? `${Math.floor(year / 10) * 10}s` : null;
    }
    case 'vibe': return firstVibe(song);
    case 'title':
    default: return typeof song.title === 'string' && song.title.trim() ? song.title : null;
  }
}

function compareIds(left, right) {
  const a = String(left ?? '');
  const b = String(right ?? '');
  if (/^\d+$/.test(a) && /^\d+$/.test(b)) {
    const na = BigInt(a), nb = BigInt(b);
    return na < nb ? -1 : na > nb ? 1 : 0;
  }
  return textCompare(a, b);
}

export function sortSongs(songs, { key = 'title', direction = 'asc' } = {}) {
  const safeKey = VALID_SORTS.has(key) ? key : 'title';
  const sign = direction === 'desc' ? -1 : 1;
  return [...songs].sort((left, right) => {
    const a = sortValue(left, safeKey);
    const b = sortValue(right, safeKey);
    const aMissing = a == null || a === '';
    const bMissing = b == null || b === '';
    if (aMissing !== bMissing) return aMissing ? 1 : -1;
    let primary = 0;
    if (!aMissing) primary = typeof a === 'number' && typeof b === 'number' ? a - b : textCompare(a, b);
    if (primary) return primary * sign;
    if (safeKey !== 'title') {
      const title = textCompare(sortValue(left, 'title') ?? '', sortValue(right, 'title') ?? '');
      if (title) return title;
    }
    return compareIds(left.id, right.id);
  });
}
