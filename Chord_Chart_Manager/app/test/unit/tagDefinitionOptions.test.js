import { describe, expect, it } from 'vitest';
import { matchingTagDefinitions, normalizeDefinitionName } from '../../src/lib/tagDefinitionOptions.js';

describe('managed tag definition autocomplete', () => {
  const definitions = [
    { id: 7, name: 'Pop Rock' },
    { id: 2, name: 'Rock' },
    { id: 3, name: 'Classic Rock' },
    { id: 4, name: 'Soft Rock' },
    { id: 5, name: 'Indie' },
  ];

  it('normalizes surrounding and repeated whitespace for matching', () => {
    expect(normalizeDefinitionName('  classic   rock  ')).toBe('classic rock');
    expect(matchingTagDefinitions(definitions, '  ROCK  ').map(item => item.name))
      .toEqual(['Rock', 'Classic Rock', 'Pop Rock', 'Soft Rock']);
  });

  it('puts prefix matches first and applies stable alphabetical and id ordering', () => {
    const matches = matchingTagDefinitions([
      { id: 9, name: 'Pop Rock' }, { id: 3, name: 'Pop Rock' },
      { id: 8, name: 'Popular' }, { id: 4, name: 'Rock Pop' },
    ], 'pop');
    expect(matches.map(item => `${item.name}:${item.id}`))
      .toEqual(['Pop Rock:3', 'Pop Rock:9', 'Popular:8', 'Rock Pop:4']);
  });

  it('excludes selected definitions and returns no options for an empty query', () => {
    expect(matchingTagDefinitions(definitions, 'rock', [' classic  rock ']).map(item => item.name))
      .toEqual(['Rock', 'Pop Rock', 'Soft Rock']);
    expect(matchingTagDefinitions(definitions, '   ')).toEqual([]);
  });
});
