const assert = require('node:assert/strict');

const COUNT_FIELDS = ['totalSongs', 'totalTags', 'totalSetlists'];

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

module.exports = { readMainCounts, assertMainCountsUnchanged };
