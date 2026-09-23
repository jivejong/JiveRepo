/**
 * Thin API layer.
 *
 * Every path is relative so Vite's dev proxy forwards it to Spring Boot on 8080 and the browser
 * only ever sees same-origin requests - no CORS handling anywhere in the app or the backend.
 *
 * The important distinction this layer preserves: a non-OK response with a parsed body is not the
 * same as a dead network. The 409 from an invalid stage transition carries the state machine's own
 * account of what is reachable, and that body is the point rather than an error to swallow.
 */

export class ApiError extends Error {
  constructor(message, { status = null, body = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }

  /** True when the failure was transport-level rather than an answer from the server. */
  get isNetworkFailure() {
    return this.status === null;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: options.body ? { 'Content-Type': 'application/json', ...options.headers } : options.headers,
    });
  } catch (cause) {
    if (cause.name === 'AbortError') throw cause;
    throw new ApiError(`Could not reach the API (${cause.message}). Is Spring Boot running on 8080?`);
  }

  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!response.ok) {
    const detail = body && typeof body === 'object' && body.error ? body.error : response.statusText;
    throw new ApiError(`${response.status} ${detail}`, { status: response.status, body });
  }
  return body;
}

export const api = {
  searchCandidates: (query, signal) =>
    request(query ? `/api/candidates?q=${encodeURIComponent(query)}` : '/api/candidates', { signal }),

  createRequisition: (payload, signal) =>
    request('/api/requisitions', { method: 'POST', body: JSON.stringify(payload), signal }),

  /** limit defaults to 20 server-side; passed explicitly so the demo is honest about what it asked for. */
  matches: (requisitionId, limit = 20, signal) =>
    request(`/api/requisitions/${requisitionId}/matches?limit=${limit}`, { signal }),

  createApplication: (candidateId, requisitionId, signal) =>
    request('/api/applications', {
      method: 'POST',
      body: JSON.stringify({ candidateId, requisitionId }),
      signal,
    }),

  /** Real Gemini call. Seconds, not milliseconds. */
  aiProfile: (applicationId, { refresh = false } = {}, signal) =>
    request(`/api/applications/${applicationId}/ai-profile?refresh=${refresh}`, {
      method: 'POST',
      signal,
    }),

  /** Real Gemini call, and the slower of the two - a whole interview in one generation. */
  mockInterview: (applicationId, signal) =>
    request(`/api/applications/${applicationId}/mock-interview`, { method: 'POST', signal }),

  /**
   * Extends a salary offer. Only legal at OFFER; anything else answers 409 from the same state
   * machine that governs transitions. Accepting lands on HIRED, declining on WITHDRAWN.
   */
  extendOffer: (applicationId, offerAmount, signal) =>
    request(`/api/applications/${applicationId}/offer`, {
      method: 'POST',
      body: JSON.stringify({ offerAmount }),
      signal,
    }),

  offer: (applicationId, signal) => request(`/api/applications/${applicationId}/offer`, { signal }),

  /**
   * `toStage` must be the enum constant in upper case - "HIRED", not "Hired". Jackson matches enum
   * names exactly, and the title-case form is rejected with a 400 before it ever reaches the state
   * machine. Verified against the live API.
   */
  transition: (applicationId, toStage, note, signal) =>
    request(`/api/applications/${applicationId}/transition`, {
      method: 'POST',
      body: JSON.stringify({ toStage, note }),
      signal,
    }),
};

export const STAGE = {
  SOURCED: 'SOURCED',
  SCREENING: 'SCREENING',
  INTERVIEWING: 'INTERVIEWING',
  OFFER: 'OFFER',
  HIRED: 'HIRED',
  REJECTED: 'REJECTED',
  WITHDRAWN: 'WITHDRAWN',
};
