const https = require("node:https");
const http = require("node:http");
const { URL } = require("node:url");

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_RESPONSE_BYTES = 1_048_576;

function invalidRequest(message = "Provider request is not allowed.") {
  const error = new TypeError(message);
  error.code = "INVALID_PROVIDER_REQUEST";
  return error;
}

function validateRequest(request) {
  if (!request || typeof request !== "object" || request.method !== "GET" ||
      request.body !== undefined && request.body !== null && request.body.length !== 0) {
    throw invalidRequest();
  }
  let url;
  try { url = new URL(request.url); } catch { throw invalidRequest(); }
  if (url.protocol !== "https:" || url.username || url.password || url.hash ||
      url.port && url.port !== "443") throw invalidRequest();

  const entries = [...url.searchParams.entries()];
  if (new Set(entries.map(([key]) => key)).size !== entries.length) throw invalidRequest();
  const parameterMap = new Map(entries);
  const searchParametersValid = entries.length === 4 &&
    [...parameterMap.keys()].every(key => ["fmt", "limit", "query", "offset"].includes(key)) &&
    parameterMap.get("fmt") === "json" && parameterMap.get("limit") === "100" &&
    typeof parameterMap.get("query") === "string" && parameterMap.get("query").length <= 2000 &&
    parameterMap.get("offset") === "0";
  const uuidPath = /^\/api\/v1\/([0-9a-f-]{36})\/(count|low-level)$/.exec(url.pathname);
  const hasSubmissionCount = Object.hasOwn(request, "acousticbrainzSubmissionCount");
  const acousticOffset = parameterMap.get("n");
  const acousticOffsetNumber = typeof acousticOffset === "string" && /^\d+$/.test(acousticOffset)
    ? Number(acousticOffset) : NaN;
  const validatedSubmissionCount = request.acousticbrainzSubmissionCount;
  const musicBrainz = url.hostname === "musicbrainz.org" &&
    !hasSubmissionCount && (url.pathname === "/ws/2/recording/" && request.method === "GET" &&
      searchParametersValid ||
     /^\/ws\/2\/recording\/[0-9a-f-]{36}$/i.test(url.pathname) && request.method === "GET" &&
      entries.length === 2 && parameterMap.get("fmt") === "json" && parameterMap.get("inc") === "artist-credits genres tags" &&
      UUID.test(url.pathname.slice("/ws/2/recording/".length)));
  const acoustic = url.hostname === "acousticbrainz.org" && uuidPath && UUID.test(uuidPath[1]) &&
    (uuidPath[2] === "count" && entries.length === 0 && !hasSubmissionCount || uuidPath[2] === "low-level" &&
      entries.length === 1 && Number.isSafeInteger(acousticOffsetNumber) && Number.isSafeInteger(validatedSubmissionCount) &&
      validatedSubmissionCount > 0 && acousticOffsetNumber < validatedSubmissionCount);
  if (!musicBrainz && !acoustic) throw invalidRequest();

  const headers = request.headers;
  if (!headers || typeof headers !== "object" || Array.isArray(headers)) throw invalidRequest();
  const cleanHeaders = {};
  for (const [name, value] of Object.entries(headers)) {
    const lower = name.toLowerCase();
    if (!["accept", "user-agent"].includes(lower) || typeof value !== "string" || /[\r\n]/.test(value)) {
      throw invalidRequest();
    }
    cleanHeaders[lower === "user-agent" ? "User-Agent" : "Accept"] = value;
  }
  if (cleanHeaders.Accept !== "application/json" ||
      musicBrainz && (typeof cleanHeaders["User-Agent"] !== "string" || !cleanHeaders["User-Agent"].trim()) ||
      acoustic && Object.hasOwn(cleanHeaders, "User-Agent")) throw invalidRequest();
  try {
    for (const [name, value] of Object.entries(cleanHeaders)) http.validateHeaderValue(name, value);
  } catch { throw invalidRequest("Provider request headers are invalid."); }

  const timeoutMs = request.timeoutMs;
  const maxResponseBytes = request.maxResponseBytes;
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0 ||
      !Number.isSafeInteger(maxResponseBytes) || maxResponseBytes <= 0 ||
      !request.signal || typeof request.signal.addEventListener !== "function" ||
      typeof request.signal.aborted !== "boolean") throw invalidRequest();
  return { url, headers: cleanHeaders, timeoutMs, maxResponseBytes, signal: request.signal };
}

function createSongEnrichmentTransport({ request: httpsRequest = https.request, setTimer = setTimeout,
  clearTimer = clearTimeout } = {}) {
  if (typeof httpsRequest !== "function" || typeof setTimer !== "function" || typeof clearTimer !== "function") {
    throw new TypeError("HTTPS transport dependencies must be functions.");
  }
  return async function songEnrichmentTransport(request) {
    const validated = validateRequest(request);
    const { url, headers, timeoutMs, maxResponseBytes, signal } = validated;
    const responseByteLimit = Math.min(maxResponseBytes, MAX_RESPONSE_BYTES);
    if (signal.aborted) return Promise.reject(Object.assign(new Error("Provider request cancelled."), { name: "AbortError" }));

    return new Promise((resolve, reject) => {
      let req;
      let response;
      let timer;
      let settled = false;
      let responseEnded = false;
      const chunks = [];
      let byteLength = 0;
      const clearOperation = () => {
        if (timer !== undefined) {
          try { clearTimer(timer); } catch { /* timer cleanup is best effort */ }
          timer = undefined;
        }
        signal.removeEventListener("abort", onAbort);
      };
      const finish = (error, value) => {
        if (settled) return;
        settled = true;
        clearOperation();
        if (error) reject(error); else resolve(value);
      };
      const destroyResources = () => {
        try { response?.destroy(); } catch { /* response cleanup is best effort */ }
        try { req?.destroy(); } catch { /* request cleanup is best effort */ }
      };
      const onAbort = () => {
        const error = Object.assign(new Error("Provider request cancelled."), { name: "AbortError" });
        finish(error);
        destroyResources();
      };
      const onRequestError = () => {
        if (settled) return;
        finish(new Error("Provider HTTPS request failed."));
        destroyResources();
      };
      const onResponseError = () => {
        if (settled) return;
        finish(new Error("Provider HTTPS response failed."));
        destroyResources();
      };
      const onResponseAborted = () => onResponseError();
      const onRedirectError = () => {};
      const onRequestClose = () => {
        req?.removeListener("error", onRequestError);
        req?.removeListener("close", onRequestClose);
      };
      const onResponseClose = () => {
        if (!responseEnded && !settled) onResponseError();
        response?.removeListener("error", onResponseError);
        response?.removeListener("error", onRedirectError);
        response?.removeListener("aborted", onResponseAborted);
        response?.removeListener("data", onData);
        response?.removeListener("end", onEnd);
        response?.removeListener("close", onResponseClose);
      };
      const onData = chunk => {
        if (settled) return;
        const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
        if (byteLength + bytes.byteLength > responseByteLimit) {
          const error = new Error("Provider response exceeded its byte limit.");
          error.code = "RESPONSE_TOO_LARGE";
          finish(error);
          destroyResources();
          return;
        }
        byteLength += bytes.byteLength;
        chunks.push(bytes);
      };
      const onEnd = () => {
        if (settled) return;
        responseEnded = true;
        finish(null, { status: response.statusCode, headers: response.headers || {}, body: Buffer.concat(chunks, byteLength) });
      };

      signal.addEventListener("abort", onAbort, { once: true });
      timer = setTimer(() => {
        const error = new Error("Provider request timed out.");
        error.code = "ETIMEDOUT";
        finish(error);
        destroyResources();
      }, timeoutMs);
      try {
        req = httpsRequest(url, { method: "GET", headers }, incoming => {
          response = incoming;
          if (settled) {
            // The request can race with timeout/cancellation and still deliver a response.
            const lateResponse = response;
            const onLateError = () => {};
            lateResponse.on("error", onLateError);
            lateResponse.once("close", () => lateResponse.removeListener("error", onLateError));
            try { lateResponse.destroy(); } catch { /* late response is already unusable */ }
            return;
          }
          response.on("error", onResponseError);
          response.on("aborted", onResponseAborted);
          response.once("close", onResponseClose);
          if (response.statusCode >= 300 && response.statusCode < 400) {
            // Redirects are returned without following or buffering their body.
            response.on("error", onRedirectError);
            try { response.destroy(); } catch { /* redirect response is discarded */ }
            finish(null, { status: response.statusCode, headers: response.headers || {}, body: Buffer.alloc(0) });
            return;
          }
          response.on("data", onData);
          response.on("end", onEnd);
        });
        req.on("error", onRequestError);
        req.once("close", onRequestClose);
        req.end();
      } catch {
        finish(new Error("Provider HTTPS request could not be started."));
        destroyResources();
      }
    });
  };
}

module.exports = { MAX_RESPONSE_BYTES, createSongEnrichmentTransport, validateRequest };
