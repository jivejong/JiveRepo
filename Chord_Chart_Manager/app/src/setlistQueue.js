export function enqueueSetlistOperation(queue, operation) {
  const sameSetlist = item => item.entity === 'setlist'
    && String(item.setlist_id) === String(operation.setlist_id);
  const unsent = item => !item.sent;

  if (operation.type === 'metadata') {
    const create = queue.find(item => sameSetlist(item) && item.type === 'create' && unsent(item));
    if (create) Object.assign(create.payload, operation.payload);
    else {
      const pending = queue.find(item => sameSetlist(item) && item.type === 'metadata' && unsent(item));
      if (pending) Object.assign(pending.payload, operation.payload);
      else queue.push(operation);
    }
  } else if (operation.type === 'entry_edit') {
    const pending = queue.find(item => sameSetlist(item) && item.type === 'entry_edit'
      && unsent(item) && String(item.payload.entry_id) === String(operation.payload.entry_id));
    if (pending) Object.assign(pending.payload, operation.payload);
    else queue.push(operation);
  } else if (operation.type === 'reorder') {
    queue.splice(0, queue.length, ...queue.filter(item =>
      !(sameSetlist(item) && item.type === 'reorder' && unsent(item))));
    queue.push(operation);
  } else if (operation.type === 'entry_remove') {
    const addIndex = queue.findIndex(item => sameSetlist(item) && item.type === 'entry_add'
      && unsent(item) && String(item.payload.entry_id) === String(operation.payload.entry_id));
    if (String(operation.payload.entry_id).startsWith('local-') && addIndex >= 0) {
      const removedId = String(operation.payload.entry_id);
      queue.splice(addIndex, 1);
      // An unsent add cannot have a server-side entry to edit. Remove edits
      // for that temporary entry with it. A dispatched add is deliberately
      // retained above: its outcome may be uncertain, so its later edits and
      // removal must follow the same operation ID through acknowledgement.
      queue.splice(0, queue.length, ...queue.filter(item => !(sameSetlist(item)
        && !item.sent && item.type === 'entry_edit'
        && String(item.payload.entry_id) === removedId)));
      for (const item of queue) {
        if (sameSetlist(item) && item.type === 'reorder' && unsent(item)) {
          item.payload.entry_ids = item.payload.entry_ids.filter(id =>
            String(id) !== removedId);
        }
      }
    } else queue.push(operation);
  } else if (operation.type === 'delete' && String(operation.setlist_id).startsWith('local-')) {
    const createIndex = queue.findIndex(item => sameSetlist(item) && item.type === 'create' && unsent(item));
    if (createIndex >= 0) {
      queue.splice(0, queue.length, ...queue.filter(item => !sameSetlist(item)));
      return { cancelledLocalSetlist: true };
    }
    queue.push(operation);
  } else queue.push(operation);
  return { cancelledLocalSetlist: false };
}

export function rebaseSetlistOperations(queue, setlistId, exactToken) {
  for (const item of queue) {
    if (item.entity === 'setlist' && String(item.setlist_id) === String(setlistId)) {
      item.base_updated_at = exactToken;
    }
  }
}

export function discardSetlistOperations(queue, setlistId) {
  return queue.filter(item => !(item.entity === 'setlist'
    && String(item.setlist_id) === String(setlistId)));
}

export function resolveSetlistOperationIds(type, payload, { songs = {}, entries = {} } = {}) {
  const resolved = { ...payload };
  if (type === 'entry_add') {
    resolved.song_id = songs[resolved.song_id] ?? resolved.song_id;
    if (String(resolved.song_id).startsWith('local-')) {
      const error = new Error('A setlist entry depends on a song that has not synced.');
      error.status = 409;
      throw error;
    }
  }
  if (type === 'entry_edit' || type === 'entry_remove') {
    resolved.entry_id = entries[resolved.entry_id] ?? resolved.entry_id;
  }
  if (type === 'reorder') {
    resolved.entry_ids = resolved.entry_ids.map(id => entries[id] ?? id);
  }
  return resolved;
}
