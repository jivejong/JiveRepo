import { describe, expect, it } from 'vitest';
import {
  buildSetlistEntryPayload,
  buildSetlistMetadataPayload,
  setlistWriteError,
} from '../../src/setlistMutations.js';

describe('setlist metadata payloads', () => {
  it('trims the name and preserves a date-only value without conversion', () => {
    expect(buildSetlistMetadataPayload({
      name: '  Friday show  ', gig_date: '2026-10-09', notes: '  Doors at 7  ',
    })).toEqual({ name: 'Friday show', gig_date: '2026-10-09', notes: 'Doors at 7' });
  });

  it('clears optional date and notes', () => {
    expect(buildSetlistMetadataPayload({ name: 'Friday show', gig_date: '', notes: '' }))
      .toEqual({ name: 'Friday show', gig_date: null, notes: null });
  });

  it('rejects blank names and invalid calendar dates', () => {
    expect(() => buildSetlistMetadataPayload({ name: '  ' })).toThrow(/name is required/i);
    expect(() => buildSetlistMetadataPayload({ name: 'Show', gig_date: '2026-02-30' }))
      .toThrow(/valid calendar date/i);
  });
});

describe('setlist entry partial updates', () => {
  it('keeps omitted fields omitted and trims only the supplied note', () => {
    expect(buildSetlistEntryPayload({ notes: '  Entrance on stage left  ' }))
      .toEqual({ notes: 'Entrance on stage left' });
  });

  it('clears key, capo, and note independently with null', () => {
    expect(buildSetlistEntryPayload({ transposed_key: '', capo_fret: '', notes: '' }))
      .toEqual({ transposed_key: null, capo_fret: null, notes: null });
  });

  it('preserves explicit capo zero', () => {
    expect(buildSetlistEntryPayload({ capo_fret: '0' })).toEqual({ capo_fret: 0 });
  });

  it('validates key names and capo range', () => {
    expect(() => buildSetlistEntryPayload({ transposed_key: 'H' })).toThrow(/supported key/i);
    expect(() => buildSetlistEntryPayload({ capo_fret: '1.5' })).toThrow(/whole number/i);
    expect(() => buildSetlistEntryPayload({ capo_fret: '12' })).toThrow(/0 to 11/i);
  });

  it('gives a connection-specific message without losing the form state', () => {
    expect(setlistWriteError(new TypeError('Failed to fetch'))).toMatch(/connection to the app server/i);
    expect(setlistWriteError(Object.assign(new Error('Invalid value'), { status: 400 })))
      .toBe('Invalid value');
  });
});
