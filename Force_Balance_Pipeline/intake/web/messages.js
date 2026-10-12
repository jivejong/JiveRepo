// The words the page uses for what the server said. A 4xx answer carries a `message` meant for the reporter (intake_server.py), and
// it is used when present; these are the fallbacks, and the texts for the rules the page itself applies.

export const COULD_NOT_BE_ASSESSED = "This report could not be assessed.";
export const MAX_DESCRIPTION_CHARS = 8000; // intake_core.MAX_DESCRIPTION_CHARS

/** The reporter-facing text for a server answer that ends a report's journey (a 4xx other than 429). */
export function messageForDropped(status, body) {
  const fromServer = body && typeof body.message === "string" && body.message.trim() ? body.message.trim() : null;
  if (status === 409) return "That report number was already used for a different report, so this one was not sent.";
  if (status === 413) return "That report is too long to send.";
  if (status === 422) return fromServer || "That report could not be accepted.";
  return fromServer || `The report was refused (error ${status}).`;
}

/** For a 429: the server's text plus what the page will do about it. */
export function messageForRateLimited(body, waitMs) {
  const fromServer = body && typeof body.message === "string" && body.message.trim() ? body.message.trim() : "A daily limit has been reached.";
  return `${fromServer} It stays on this device and will be sent ${formatWait(waitMs)}.`;
}

export function messageForRetry(attempts, maxAttempts, waitMs) {
  return `The report could not be sent yet. It is saved on this device and will be tried again ${formatWait(waitMs)} (attempt ${attempts} of ${maxAttempts}).`;
}

/** "in 5 seconds", "in 2 minutes", "in about 3 hours". */
export function formatWait(ms) {
  const seconds = Math.max(1, Math.round(ms / 1000));
  if (seconds < 90) return `in ${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `in ${minutes} minutes`;
  const hours = Math.round(minutes / 60);
  return `in about ${hours} hour${hours === 1 ? "" : "s"}`;
}
