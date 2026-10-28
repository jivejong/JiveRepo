export function assignedGenreNames(genres) {
  if (!Array.isArray(genres)) return [];
  const ordered = genres
    .filter(genre => genre && typeof genre === 'object' && typeof genre.name === 'string')
    .map((genre, index) => ({
      name: genre.name.trim(),
      primary: genre.is_primary === true,
      index,
    }))
    .filter(genre => genre.name)
    .sort((left, right) => Number(right.primary) - Number(left.primary) || left.index - right.index);
  const seen = new Set();
  return ordered
    .filter(genre => {
      const key = genre.name.toLocaleLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map(genre => genre.name);
}
