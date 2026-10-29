import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { readMainCounts, assertMainCountsUnchanged } = require('../t11-main-checks.cjs');

describe('T11 main count preservation', () => {
  it('NFR-010 accepts unchanged non-default counts and rejects any drift', () => {
    const before = readMainCounts({ totalSongs: 9, totalTags: 4, totalSetlists: 0 });
    expect(() => assertMainCountsUnchanged(before,
      { totalSongs: 9, totalTags: 4, totalSetlists: 0 })).not.toThrow();
    expect(() => assertMainCountsUnchanged(before,
      { totalSongs: 10, totalTags: 4, totalSetlists: 0 }))
      .toThrow(/Main \/api\/stats counts changed while T11 tests ran/);
  });

  it('NFR-010 rejects missing or invalid captured count fields', () => {
    expect(() => readMainCounts({ totalSongs: 9, totalTags: 4 }))
      .toThrow(/totalSetlists must be a nonnegative integer/);
    expect(() => readMainCounts({ totalSongs: -1, totalTags: 4, totalSetlists: 0 }))
      .toThrow(/totalSongs must be a nonnegative integer/);
  });
});
