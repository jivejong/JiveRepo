const SETLIST_KEYS = new Set([
  'C', 'B#', 'C#', 'Db', 'D', 'D#', 'Eb', 'E', 'Fb', 'E#', 'F', 'F#',
  'Gb', 'G', 'G#', 'Ab', 'A', 'A#', 'Bb', 'B', 'Cb',
]);
for (const key of [...SETLIST_KEYS]) SETLIST_KEYS.add(`${key}m`);

function isDateOnly(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split('-').map(Number);
  if (month < 1 || month > 12 || day < 1) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const monthDays = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= monthDays[month - 1];
}

export function buildSetlistMetadataPayload(form) {
  const name = typeof form?.name === 'string' ? form.name.trim() : '';
  if (!name) throw new Error('Setlist name is required.');

  const rawDate = form.gig_date ?? '';
  if (rawDate !== '' && rawDate !== null && !isDateOnly(rawDate)) {
    throw new Error('Gig date must be a valid calendar date.');
  }
  if (form.notes != null && typeof form.notes !== 'string') {
    throw new Error('Setlist notes must be text.');
  }

  return {
    name,
    gig_date: rawDate || null,
    notes: form.notes?.trim() || null,
  };
}

export function buildSetlistEntryPayload(draft) {
  const payload = {};
  if (Object.hasOwn(draft || {}, 'transposed_key') && draft.transposed_key !== undefined) {
    const rawKey = draft.transposed_key;
    if (rawKey !== null && typeof rawKey !== 'string') {
      throw new Error('Key override must be a supported key or blank.');
    }
    const key = rawKey?.trim() || null;
    if (key && !SETLIST_KEYS.has(key)) {
      throw new Error('Key override must use a supported key name (for example, G or Bb).');
    }
    payload.transposed_key = key;
  }
  if (Object.hasOwn(draft || {}, 'capo_fret') && draft.capo_fret !== undefined) {
    const rawCapo = draft.capo_fret;
    const capo = rawCapo === '' || rawCapo === null ? null : Number(rawCapo);
    if (capo !== null && (!Number.isInteger(capo) || capo < 0 || capo > 11)) {
      throw new Error('Capo override must be a whole number from 0 to 11 or blank.');
    }
    payload.capo_fret = capo;
  }
  if (Object.hasOwn(draft || {}, 'notes') && draft.notes !== undefined) {
    if (draft.notes !== null && typeof draft.notes !== 'string') {
      throw new Error('Performance note must be text.');
    }
    payload.notes = draft.notes?.trim() || null;
  }
  if (!Object.keys(payload).length) throw new Error('Choose at least one entry field to update.');
  return payload;
}

export function setlistWriteError(error) {
  if (error?.status >= 500) {
    return 'The app server could not complete this change. Your values are still here; retry, and check the server log if the problem continues.';
  }
  if (error?.status) return error.message || 'The app server rejected this change.';
  if (error instanceof TypeError || error?.name === 'AbortError') {
    return 'Could not establish a connection to the app server. Your changes are still here; reconnect and try again.';
  }
  return error?.message || 'The change could not be saved.';
}
