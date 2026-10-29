import { describe, expect, it } from 'vitest';
import { assignedGenreNames } from '../../src/lib/songMetadata';

describe('assignedGenreNames', () => {
  it('FR-030 shows assigned genres with primary first, then secondary assignments', () => {
    expect(assignedGenreNames([
      { name: 'Folk', is_primary: false },
      { name: 'Pop', is_primary: true },
    ])).toEqual(['Pop', 'Folk']);
  });

  it('FR-030 does not create a genre from aliases or unassigned inputs', () => {
    expect(assignedGenreNames([])).toEqual([]);
    expect(assignedGenreNames(null)).toEqual([]);
    expect(assignedGenreNames(undefined)).toEqual([]);
    expect(assignedGenreNames([{ name: '  ' }])).toEqual([]);
  });

  it('FR-030 trims and suppresses duplicate assignment names without changing assignment order', () => {
    expect(assignedGenreNames([
      { name: 'Folk', is_primary: false },
      { name: ' pop ', is_primary: true },
      { name: 'FOLK', is_primary: false },
    ])).toEqual(['pop', 'Folk']);
  });
});
