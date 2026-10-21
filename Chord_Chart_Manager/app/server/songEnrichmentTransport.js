const https = require("node:https");
const http = require("node:http");
const { URL } = require("node:url");

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_RESPONSE_BYTES = 1_048_576;

function invalidRequest(message = "Provider request is not allowed.") {
  const error = new TypeError(message);
  error.code = "INVALID_PROVIDER_REQUEST";
  error.failure_details = { failure_phase: "validation", https_started: false };
  return error;
}

const SAFE_TRANSPORT_CODES = new Set(["ETIMEDOUT", "ECONNRESET", "ECONNREFUSED", "EHOSTUNREACH", "ENETUNREACH",
  "EAI_AGAIN", "ENOTFOUND", "EPIPE", "RESPONSE_TOO_LARGE", "INVALID_PROVIDER_REQUEST"]);
function transportFailure(error, phase, httpsStarted, timeoutSource = undefined, httpStatus = undefined) {
  const code = SAFE_TRANSPORT_CODES.has(error?.code) ? error.code : undefined;
  return Object.assign(new Error("Provider transport failed."), { ...(code ? { code } : {}), failure_details: {
    failure_phase: phase, https_started: httpsStarted, ...(code ? { transport_error_code: code } : {}),
    ...(Number.isInteger(httpStatus) && httpStatus >= 100 && httpStatus <= 599 ? { http_status: httpStatus } : {}),
    ...(timeoutSource ? { timeout: true, timeout_source: timeoutSource } : {}),
  } });
}

function validateRequest(request) {
  if (!request || typeof request !== "object" || !["GET", "POST"].includes(request.method)) {
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
  const gemini = url.hostname === "generativelanguage.googleapis.com" && url.pathname === "/v1beta/interactions" &&
    request.method === "POST" && entries.length === 0 && request.body !== undefined && request.body !== null;
  if (request.method === "GET" && request.body !== undefined && request.body !== null && request.body.length !== 0 ||
      request.method === "POST" && !gemini) throw invalidRequest();
  if (gemini) {
    const body = Buffer.isBuffer(request.body) ? request.body : request.body instanceof Uint8Array
      ? Buffer.from(request.body) : typeof request.body === "string" ? Buffer.from(request.body, "utf8") : null;
    if (!body || body.byteLength > 16_384) throw invalidRequest("Gemini request body is invalid or exceeds its byte limit.");
    let parsed;
    try { parsed = JSON.parse(body.toString("utf8")); } catch { throw invalidRequest("Gemini request body is invalid."); }
    const expectedKeys = ["generation_config", "input", "model", "response_format", "store", "system_instruction"];
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) ||
        Object.keys(parsed).sort().join(",") !== expectedKeys.join(",") || parsed.model !== "gemini-3.1-flash-lite" ||
        typeof parsed.input !== "string" || typeof parsed.system_instruction !== "string" || parsed.store !== false ||
        !parsed.generation_config || Object.keys(parsed.generation_config).length !== 1 ||
        !Number.isSafeInteger(parsed.generation_config.max_output_tokens) || parsed.generation_config.max_output_tokens < 1 ||
        parsed.generation_config.max_output_tokens > 1024 ||
        !parsed.response_format || Object.keys(parsed.response_format).sort().join(",") !== "mime_type,schema,type" ||
        parsed.response_format.type !== "text" || parsed.response_format.mime_type !== "application/json" ||
        !parsed.response_format.schema || parsed.response_format.schema.type !== "object" ||
        parsed.response_format.schema.additionalProperties !== false ||
        !Array.isArray(parsed.response_format.schema.required) || parsed.response_format.schema.required.length !== 5 ||
        !["release_year", "bpm", "original_key", "genres", "vibes"].every(field => parsed.response_format.schema.required.includes(field)) ||
        !parsed.response_format.schema.properties || Object.keys(parsed.response_format.schema.properties).sort().join(",") !== "bpm,genres,original_key,release_year,vibes") {
      throw invalidRequest("Gemini request does not match the approved structured-output contract.");
    }
  }
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
  if (!musicBrainz && !acoustic && !gemini) throw invalidRequest();

  const headers = request.headers;
  if (!headers || typeof headers !== "object" || Array.isArray(headers)) throw invalidRequest();
  const cleanHeaders = {};
  for (const [name, value] of Object.entries(headers)) {
    const lower = name.toLowerCase();
    if (!["accept", "user-agent", "content-type", "x-goog-api-key"].includes(lower) || typeof value !== "string" || /[\r\n]/.test(value)) {
      throw invalidRequest();
    }
    cleanHeaders[lower === "user-agent" ? "User-Agent" : lower === "accept" ? "Accept" : lower === "content-type" ? "Content-Type" : "x-goog-api-key"] = value;
  }
  if (cleanHeaders.Accept !== "application/json" ||
      musicBrainz && (typeof cleanHeaders["User-Agent"] !== "string" || !cleanHeaders["User-Agent"].trim()) ||
      acoustic && Object.hasOwn(cleanHeaders, "User-Agent") ||
      gemini && (cleanHeaders["Content-Type"] !== "application/json" || typeof cleanHeaders["x-goog-api-key"] !== "string" || !cleanHeaders["x-goog-api-key"].trim() || Object.hasOwn(cleanHeaders, "User-Agent")) ||
      !gemini && (Object.hasOwn(cleanHeaders, "Content-Type") || Object.hasOwn(cleanHeaders, "x-goog-api-key"))) throw invalidRequest();
  try {
    for (const [name, value] of Object.entries(cleanHeaders)) http.validateHeaderValue(name, value);
  } catch { throw invalidRequest("Provider request headers are invalid."); }

  const timeoutMs = request.timeoutMs;
  const maxResponseBytes = request.maxResponseBytes;
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0 ||
      !Number.isSafeInteger(maxResponseBytes) || maxResponseBytes <= 0 ||
      !request.signal || typeof request.signal.addEventListener !== "function" ||
      typeof request.signal.aborted !== "boolean") throw invalidRequest();
  return { url, headers: cleanHeaders, timeoutMs, maxResponseBytes, signal: request.signal,
    method: request.method, body: gemini ? request.body : undefined };
}

function createSongEnrichmentTransport({ request: httpsRequest = https.request, setTimer = setTimeout,
  clearTimer = clearTimeout } = {}) {
  if (typeof httpsRequest !== "function" || typeof setTimer !== "function" || typeof clearTimer !== "function") {
    throw new TypeError("HTTPS transport dependencies must be functions.");
  }
  return async function songEnrichmentTransport(request) {
    let validated;
    try { validated = validateRequest(request); }
    catch (error) { throw transportFailure(error, "validation", false); }
    const { url, headers, timeoutMs, maxResponseBytes, signal, method, body } = validated;
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
      const onRequestError = error => {
        if (settled) return;
        finish(transportFailure(error, "request", true));
        destroyResources();
      };
      const onResponseError = error => {
        if (settled) return;
        finish(transportFailure(error, "response", true, undefined, response?.statusCode));
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
          finish(transportFailure(error, "response", true, undefined, response?.statusCode));
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
          finish(transportFailure(error, "timeout", true, "transport", response?.statusCode));
        destroyResources();
      }, timeoutMs);
      try {
        req = httpsRequest(url, { method, headers }, incoming => {
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
        if (body !== undefined) req.end(body); else req.end();
      } catch (error) {
        finish(transportFailure(error, "request_start", true));
        destroyResources();
      }
    });
  };
}

module.exports = { MAX_RESPONSE_BYTES, createSongEnrichmentTransport, validateRequest };
