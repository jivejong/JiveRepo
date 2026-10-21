// The dashboard's only network access (doc 05 "Serving (Phase 7)": "Dashboard backend"). Same origin, relative /api paths. Nothing else in
// src/ may call fetch.

/** The "data" of a GET /api/... answer; throws an Error whose message is the status on a non-2xx answer. */
export async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const body = await response.json();
  return body.data;
}

let cachedToken = null;

async function deployToken(refresh) {
  if (cachedToken && !refresh) return cachedToken;
  const response = await fetch("/api/deploy/token", { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  cachedToken = (await response.json()).token;
  return cachedToken;
}

/** POST a JSON body with the deploy token; resolves to {status, body} for any answer the server gives (a refusal is data, not an exception).
 *  A 403 bad_token is retried once with a fresh token (the backend was restarted). */
export async function postJson(path, body) {
  const send = async (refresh) => {
    const token = await deployToken(refresh);
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-Deploy-Token": token },
      body: JSON.stringify(body),
    });
    let parsed = null;
    try {
      parsed = await response.json();
    } catch (e) {
      parsed = null;
    }
    return { status: response.status, body: parsed };
  };
  const first = await send(false);
  if (first.status === 403 && first.body && first.body.error === "bad_token") return send(true);
  return first;
}
