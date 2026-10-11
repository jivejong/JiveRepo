/** Return a real local creation time only for a song created in this save. */
export function offlineSongCreatedAt(song, operation, now = () => new Date().toISOString()) {
  if (song?.created_at) return song.created_at;
  if (!song && operation === 'create') return now();
  return undefined;
}

/** Keep the list-sort projection aligned with the explicit Genre assignments. */
export function primaryGenreFromAssignments(genres = []) {
  return genres.find(genre => genre && typeof genre === 'object' && genre.is_primary)?.name || null;
}
