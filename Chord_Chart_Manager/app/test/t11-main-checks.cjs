const assert = require('node:assert/strict');

const COUNT_FIELDS = ['totalSongs', 'totalTags', 'totalSetlists'];

// Every main-database table, keyed so T11 can prove each row survives unchanged (NFR-010).
const MAIN_TABLES = [
  ['artists', 'public.artists', 'id::text'], ['genres', 'public.genres', 'id::text'],
  ['vibes', 'public.vibes', 'id::text'], ['songs', 'public.songs', 'id::text'],
  ['song_genres', 'public.song_genres', "song_id::text || ':' || genre_id::text"],
  ['song_vibes', 'public.song_vibes', "song_id::text || ':' || vibe_id::text"],
  ['setlists', 'public.setlists', 'id::text'], ['setlist_songs', 'public.setlist_songs', 'id::text'],
  ['mutation_receipts', 'public.mutation_receipts', 'operation_id::text'],
  ['schema_migrations', 'public.schema_migrations', 'version::text'],
  ['song_enrichment_runs', 'public.song_enrichment_runs', 'operation_id::text'],
];

function readMainCounts(stats) {
  assert.ok(stats && typeof stats === 'object' && !Array.isArray(stats),
    'Main /api/stats must return a JSON object');
  const counts = {};
  for (const field of COUNT_FIELDS) {
    assert.ok(Number.isSafeInteger(stats[field]) && stats[field] >= 0,
      `Main /api/stats field ${field} must be a nonnegative integer`);
    counts[field] = stats[field];
  }
  return counts;
}

function assertMainCountsUnchanged(before, after) {
  assert.deepEqual(readMainCounts(after), readMainCounts(before),
    'Main /api/stats counts changed while T11 tests ran');
}

module.exports = { MAIN_TABLES, readMainCounts, assertMainCountsUnchanged };
