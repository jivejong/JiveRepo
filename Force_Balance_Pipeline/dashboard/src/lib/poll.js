// Polling cadence of the dashboard (doc 07 Phase 7): once a minute, backing off while the backend fails. Pure.

export const POLL_MS = 60000;
export const MAX_DELAY_MS = 600000;

/** The wait before the next poll after `failures` failures in a row: 0 gives 60 s, 1 gives 120 s, 2 gives 240 s, ... capped at 600 s. */
export function nextDelay(failures) {
  const n = Number.isInteger(failures) && failures > 0 ? failures : 0;
  return Math.min(POLL_MS * 2 ** n, MAX_DELAY_MS);
}
