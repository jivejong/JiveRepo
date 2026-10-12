// What the page asks the server and how it builds a report. The rules in validateDescription are the server's own
// (intake_core.parse_request): the page applies them first only to save a round trip; the server decides.

import { MAX_DESCRIPTION_CHARS } from "./messages.js";

export const SECTORS_URL = "/api/sectors";
export const REPORTS_URL = "/api/reports";
export const REPORT_URL = "/api/report";
// C0 and C1 control characters except tab, newline and carriage return (the server rejects the same set)
const CONTROL_RE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/;

/** null if the text may be sent, else {code, message}. The text itself is never changed: it is stored verbatim. */
export function validateDescription(text) {
  if (typeof text !== "string" || text.trim() === "") return { code: "empty", message: "Describe what you saw first." };
  if (Array.from(text).length > MAX_DESCRIPTION_CHARS) {
    return { code: "too_long", message: `That is too long: the limit is ${MAX_DESCRIPTION_CHARS} characters.` };
  }
  if (CONTROL_RE.test(text)) return { code: "invalid_characters", message: "The text contains control characters that cannot be sent." };
  return null;
}

/**
 * A report as it will be queued: the exact request body is fixed here, so every attempt sends the same bytes (same report_id, same
 * observed_at). observed_at is the submit time, not the time of a later retry.
 */
export function buildReport({ sectorId, description, reporterId, nowMs, newUlid }) {
  const reportId = newUlid(nowMs);
  const body = JSON.stringify({
    sector_id: sectorId,
    description,
    report_id: reportId,
    reporter_id: reporterId,
    observed_at: new Date(nowMs).toISOString(),
  });
  return { report_id: reportId, sector_id: sectorId, body };
}

/** GET a JSON document. Returns {ok, status, body}; a network failure or unreadable body gives {ok: false, status: 0, body: null}. */
export async function getJson(fetchFn, url) {
  try {
    const response = await fetchFn(url, { method: "GET", headers: { Accept: "application/json" }, cache: "no-store" });
    let body = null;
    try {
      body = await response.json();
    } catch (e) {
      body = null;
    }
    return { ok: response.status === 200 && body !== null, status: response.status, body };
  } catch (e) {
    return { ok: false, status: 0, body: null };
  }
}

export const loadSectors = (fetchFn) => getJson(fetchFn, SECTORS_URL);
export const loadRecent = (fetchFn, sectorId) => getJson(fetchFn, `${REPORTS_URL}?sector_id=${encodeURIComponent(sectorId)}`);
