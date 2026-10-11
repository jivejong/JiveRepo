export function distinctArtists(songs = []) {
  const seen = new Set();
  const artists = [];
  for (const song of songs) {
    const name = typeof song?.artist === 'string' ? song.artist.trim() : '';
    const key = name.toLocaleLowerCase();
    if (name && !seen.has(key)) {
      seen.add(key);
      artists.push(name);
    }
  }
  return artists.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
}

export function matchingArtists(artists = [], query = '', limit = 8) {
  const text = String(query).trim().toLocaleLowerCase();
  if (!text) return [];
  return artists.filter(name => name.toLocaleLowerCase().startsWith(text)).slice(0, limit);
}
