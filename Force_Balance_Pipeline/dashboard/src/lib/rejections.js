// Rejection counts by reason and hour (doc 07 Phase 7, panel 6). Pure.

/** The sorted distinct reject reasons. */
export function reasons(byReason) {
  return [...new Set((byReason || []).map((r) => r.reject_reason))].sort();
}

/** One row per hour, ascending, with a key per reason (0 where the reason did not occur): {hour, <reason>: n, ...}. */
export function pivotByReason(byReason) {
  const names = reasons(byReason);
  const hours = new Map();
  for (const r of byReason || []) {
    if (!hours.has(r.hour)) hours.set(r.hour, Object.fromEntries(names.map((n) => [n, 0])));
    hours.get(r.hour)[r.reject_reason] += r.rejects;
  }
  return [...hours.keys()].sort().map((hour) => ({ hour, ...hours.get(hour) }));
}
