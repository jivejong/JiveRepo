// The dashboard's only network access (doc 05 "Serving (Phase 7)": "Dashboard backend"). Same origin, relative /api paths. Nothing else in
// src/ may call fetch.

/** The "data" of a GET /api/... answer; throws an Error whose message is the status on a non-2xx answer. */
export async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const body = await response.json();
  return body.data;
}
