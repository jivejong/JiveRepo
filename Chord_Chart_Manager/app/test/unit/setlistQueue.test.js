import { describe, expect, it } from 'vitest';
import {
  discardSetlistOperations, enqueueSetlistOperation, rebaseSetlistOperations,
  resolveSetlistOperationIds,
} from '../../src/setlistQueue.js';

let sequence = 0;
const make = (type, setlistId = 'local-list', payload = {}, sent = false) => ({
  entity: 'setlist', setlist_id: setlistId, type, payload, sent,
  base_updated_at: 'v1', op_id: `${type}-${++sequence}`,
});

describe('typed setlist queue rules', () => {
  it('coalesces metadata into an unsent create and preserves omitted versus null fields', () => {
    const queue = [make('create', 'local-list', { name: 'First', notes: 'keep' })];
    enqueueSetlistOperation(queue, make('metadata', 'local-list', { name: 'Second' }));
    enqueueSetlistOperation(queue, make('metadata', 'local-list', { notes: null }));
    expect(queue).toHaveLength(1);
    expect(queue[0].payload).toEqual({ name: 'Second', notes: null });
  });

  it('coalesces same-entry edits without changing explicit zero or null', () => {
    const queue = [make('entry_edit', '42', { entry_id: '7', capo_fret: 0 })];
    enqueueSetlistOperation(queue, make('entry_edit', '42', { entry_id: '7', transposed_key: 'D' }));
    enqueueSetlistOperation(queue, make('entry_edit', '42', { entry_id: '7', capo_fret: null }));
    expect(queue).toHaveLength(1);
    expect(queue[0].payload).toEqual({ entry_id: '7', capo_fret: null, transposed_key: 'D' });
  });

  it('cancels an unsent local add/remove and removes that ID from pending reorder snapshots', () => {
    const queue = [
      make('entry_add', 'local-list', { entry_id: 'local-entry-a', song_id: 2 }),
      make('reorder', 'local-list', { entry_ids: ['local-entry-a', 'local-entry-b'] }),
    ];
    enqueueSetlistOperation(queue, make('entry_remove', 'local-list', { entry_id: 'local-entry-a' }));
    expect(queue).toHaveLength(1);
    expect(queue[0].payload.entry_ids).toEqual(['local-entry-b']);
  });

  it('cancels edits that depend on an unsent add when that entry is removed', () => {
    const queue = [
      make('entry_add', 'local-list', { entry_id: 'local-entry-a', song_id: 9 }),
      make('entry_edit', 'local-list', { entry_id: 'local-entry-a', capo_fret: 0 }),
      make('entry_add', 'local-list', { entry_id: 'local-entry-b', song_id: 10 }),
      make('entry_edit', 'local-list', { entry_id: 'local-entry-b', notes: 'keep' }),
    ];
    enqueueSetlistOperation(queue, make('entry_remove', 'local-list', { entry_id: 'local-entry-a' }));
    expect(queue.map(item => [item.type, item.payload.entry_id])).toEqual([
      ['entry_add', 'local-entry-b'], ['entry_edit', 'local-entry-b'],
    ]);
  });

  it('retains a dispatched add, its edits, and the later remove while outcome is uncertain', () => {
    const queue = [make('entry_add', 'local-list', { entry_id: 'local-entry-a', song_id: 9 }, true)];
    enqueueSetlistOperation(queue, make('entry_edit', 'local-list', { entry_id: 'local-entry-a', capo_fret: 0 }));
    enqueueSetlistOperation(queue, make('entry_remove', 'local-list', { entry_id: 'local-entry-a' }));
    expect(queue.map(item => [item.type, item.sent, item.payload.entry_id])).toEqual([
      ['entry_add', true, 'local-entry-a'],
      ['entry_edit', false, 'local-entry-a'],
      ['entry_remove', false, 'local-entry-a'],
    ]);
  });

  it('places the latest reorder after intervening adds and leaves sent envelopes immutable', () => {
    const sent = make('reorder', '42', { entry_ids: ['1'] }, true);
    const queue = [sent, make('entry_add', '42', { entry_id: 'local-entry', song_id: 'local-song' }),
      make('reorder', '42', { entry_ids: ['1'] })];
    enqueueSetlistOperation(queue, make('reorder', '42', { entry_ids: ['1', 'local-entry'] }));
    expect(queue.map(item => item.type)).toEqual(['reorder', 'entry_add', 'reorder']);
    expect(queue[0]).toBe(sent);
    expect(queue[2].payload.entry_ids).toEqual(['1', 'local-entry']);
  });

  it('cancels an unsent local setlist create and all its dependents when deleted', () => {
    const queue = [make('create'), make('entry_add', 'local-list', { entry_id: 'local-entry' })];
    const result = enqueueSetlistOperation(queue, make('delete'));
    expect(result.cancelledLocalSetlist).toBe(true);
    expect(queue).toEqual([]);
  });

  it('rebases only same-setlist operations and discards the entire conflicting aggregate', () => {
    const queue = [make('metadata', '12'), make('entry_edit', '12'), make('metadata', '13')];
    rebaseSetlistOperations(queue, '12', 'v2-exact');
    expect(queue.map(item => item.base_updated_at)).toEqual(['v2-exact', 'v2-exact', 'v1']);
    expect(discardSetlistOperations(queue, 12)).toEqual([queue[2]]);
  });

  it('resolves local song and entry IDs and rejects an unresolved song dependency', () => {
    expect(resolveSetlistOperationIds('entry_add', {
      song_id: 'local-song', entry_id: 'local-entry', capo_fret: 0,
    }, { songs: { 'local-song': 19 } })).toEqual({ song_id: 19, entry_id: 'local-entry', capo_fret: 0 });
    expect(resolveSetlistOperationIds('entry_edit', { entry_id: 'local-entry', notes: null }, {
      entries: { 'local-entry': '27' },
    })).toEqual({ entry_id: '27', notes: null });
    expect(() => resolveSetlistOperationIds('entry_add', { song_id: 'local-missing' }))
      .toThrow(/has not synced/);
  });
});
