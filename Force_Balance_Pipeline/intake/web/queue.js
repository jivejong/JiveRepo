// The browser's report queue (doc 04, "Client behavior": store-and-forward, the same pattern as the probe's buffer).
//
// A report is saved to the store BEFORE its first attempt and deleted ONLY after the server answers 200; nothing else removes it
// except the reporter dismissing a finished one. Rules, each pinned by a test:
//   * the request body is fixed at submit time (same report_id, same observed_at on every attempt)
//   * one attempt at a time, oldest report first; a newer report never overtakes an older one that is waiting
//   * a network error, a timeout (60 s) or any 5xx / unexpected status keeps it queued and counts as an attempt
//   * a 429 keeps it queued, waits at least Retry-After, and does NOT count as an attempt
//   * any other 4xx ends it ("dropped"), with a message; the server said no and asking again would get the same answer
//   * at most 5 attempts, and none after 24 h from the first submit; then it is marked "could not be assessed" and left alone
//   * Retry-After is honoured as a minimum wait
// The server makes a repeat of a finished report harmless (the stored answer, no second model call), so the one unavoidable
// repeat, a tab closed after the server answered but before the delete, is safe.

import { COULD_NOT_BE_ASSESSED, messageForDropped, messageForRateLimited, messageForRetry } from "./messages.js";
import { REPORT_URL } from "./api.js";

export const MAX_ATTEMPTS = 5;
export const WINDOW_MS = 24 * 60 * 60 * 1000;
export const FETCH_TIMEOUT_MS = 60 * 1000;
export const BACKOFF_MS = [5 * 1000, 30 * 1000, 2 * 60 * 1000, 10 * 60 * 1000]; // the wait after attempt 1, 2, 3, 4
export const DEFAULT_RATE_LIMIT_WAIT_MS = 60 * 1000; // a 429 without a usable Retry-After
export const MAX_RETRY_AFTER_MS = WINDOW_MS;
export const STATES = Object.freeze({ QUEUED: "queued", FAILED: "failed", DROPPED: "dropped" });

class TimeoutError extends Error {}

/** Seconds from a Retry-After header (a whole number; the server never sends a date) as milliseconds, or null. */
export function parseRetryAfter(value) {
  if (typeof value !== "string" || !/^\d{1,9}$/.test(value.trim())) return null;
  return Math.min(Number(value.trim()) * 1000, MAX_RETRY_AFTER_MS);
}

/** What kind of answer: "sent" (200), "rate_limited" (429), "dropped" (any other 4xx) or "retry" (5xx and anything unexpected). */
export function classify(status) {
  if (status === 200) return "sent";
  if (status === 429) return "rate_limited";
  if (status >= 400 && status < 500) return "dropped";
  return "retry";
}

const byAge = (a, b) => a.created_at - b.created_at || (a.report_id < b.report_id ? -1 : a.report_id > b.report_id ? 1 : 0);

export class ReportQueue {
  /**
   * @param store   {list(), put(entry), delete(id)} (async)
   * @param fetchFn fetch(url, init) -> Promise<Response>
   * @param lock    optional (fn) => Promise: runs fn while no other tab is draining (navigator.locks)
   * @param onEvent called with {type, entry, ...} for the page to show
   */
  constructor({ store, fetchFn, now, setTimeoutFn, clearTimeoutFn, lock = null,
    timeoutMs = FETCH_TIMEOUT_MS, url = REPORT_URL, onEvent = () => {} }) {
    Object.assign(this, { store, fetchFn, now, setTimeoutFn, clearTimeoutFn, lock, timeoutMs, url, onEvent });
    this._draining = null;
    this._again = false;
  }

  /** Save a report for sending. A report_id already in the store is not added again. */
  async enqueue({ report_id, sector_id, body }) {
    const existing = (await this.store.list()).find((e) => e.report_id === report_id);
    if (existing) return existing;
    const entry = { report_id, sector_id, body, created_at: this.now(), attempts: 0, next_attempt_at: 0, state: STATES.QUEUED,
      status: null, message: null };
    await this.store.put(entry);
    this.onEvent({ type: "queued", entry });
    return entry;
  }

  /**
   * Send what is due, oldest first. A call made while a drain is running joins it (never a second attempt at the same time); if a
   * report was queued meanwhile, the running drain looks again before it finishes.
   */
  drain() {
    if (this._draining) {
      this._again = true;
      return this._draining;
    }
    this._draining = (async () => {
      let outcomes = [];
      do {
        this._again = false;
        outcomes = outcomes.concat(await (this.lock ? this.lock(() => this._run()) : this._run()));
      } while (this._again);
      return outcomes;
    })().finally(() => {
      this._draining = null;
    });
    return this._draining;
  }

  async _run() {
    const outcomes = [];
    for (;;) {
      const head = (await this.store.list()).filter((e) => e.state === STATES.QUEUED).sort(byAge)[0];
      if (!head) break;
      if (this.now() - head.created_at > WINDOW_MS) {
        await this._finish(head, STATES.FAILED, "expired", COULD_NOT_BE_ASSESSED);
        outcomes.push({ kind: "failed", report_id: head.report_id });
        continue;
      }
      if (head.next_attempt_at > this.now()) {
        outcomes.push({ kind: "waiting", report_id: head.report_id });
        break;
      }
      const outcome = await this._attempt(head);
      outcomes.push(outcome);
      if (outcome.kind === "retry" || outcome.kind === "rate_limited") break; // the oldest is waiting: nothing overtakes it
    }
    return outcomes;
  }

  async _attempt(entry) {
    const attempting = { ...entry, attempts: entry.attempts + 1 };
    await this.store.put(attempting); // counted before it is sent, so a tab that dies mid-request still used the attempt
    this.onEvent({ type: "sending", entry: attempting });
    let answer;
    try {
      answer = await this._roundTrip(attempting);
    } catch (e) {
      return this._retryOrFail(attempting, e instanceof TimeoutError ? "timeout" : "network", null);
    }
    const kind = classify(answer.status);
    if (kind === "sent") {
      await this.store.delete(attempting.report_id); // the only place a delivered report leaves the store
      this.onEvent({ type: "sent", entry: attempting, response: answer.json });
      return { kind: "sent", report_id: attempting.report_id, response: answer.json };
    }
    if (kind === "rate_limited") {
      const wait = answer.retryAfterMs ?? DEFAULT_RATE_LIMIT_WAIT_MS;
      const waiting = { ...attempting, attempts: entry.attempts, next_attempt_at: this.now() + wait, status: 429,
        message: messageForRateLimited(answer.json, wait) };
      await this.store.put(waiting);
      this.onEvent({ type: "rate_limited", entry: waiting, waitMs: wait });
      return { kind: "rate_limited", report_id: waiting.report_id, waitMs: wait };
    }
    if (kind === "dropped") {
      await this._finish(attempting, STATES.DROPPED, String(answer.status), messageForDropped(answer.status, answer.json), answer.status);
      return { kind: "dropped", report_id: attempting.report_id, status: answer.status };
    }
    return this._retryOrFail(attempting, `status ${answer.status}`, answer.retryAfterMs, answer.status);
  }

  async _retryOrFail(entry, reason, retryAfterMs, status = null) {
    if (entry.attempts >= MAX_ATTEMPTS) {
      await this._finish(entry, STATES.FAILED, reason, COULD_NOT_BE_ASSESSED, status);
      return { kind: "failed", report_id: entry.report_id };
    }
    const wait = Math.max(BACKOFF_MS[Math.min(entry.attempts, BACKOFF_MS.length) - 1], retryAfterMs ?? 0);
    const waiting = { ...entry, next_attempt_at: this.now() + wait, status, message: messageForRetry(entry.attempts, MAX_ATTEMPTS, wait) };
    await this.store.put(waiting);
    this.onEvent({ type: "retry", entry: waiting, waitMs: wait, reason });
    return { kind: "retry", report_id: waiting.report_id, waitMs: wait };
  }

  async _finish(entry, state, reason, message, status = null) {
    const done = { ...entry, state, reason, message, status, finished_at: this.now() };
    await this.store.put(done); // kept, so the reporter sees what happened even after a reload
    this.onEvent({ type: state, entry: done });
  }

  async _roundTrip(entry) {
    const controller = new AbortController();
    let timer;
    const timedOut = new Promise((_, reject) => {
      timer = this.setTimeoutFn(() => {
        controller.abort();
        reject(new TimeoutError("no answer in time"));
      }, this.timeoutMs);
    });
    const exchange = (async () => {
      const response = await this.fetchFn(this.url, { method: "POST", headers: { "Content-Type": "application/json" }, body: entry.body,
        signal: controller.signal, cache: "no-store" });
      let json = null;
      try {
        json = await response.json();
      } catch (e) {
        json = null;
      }
      return { status: response.status, json, retryAfterMs: parseRetryAfter(response.headers?.get?.("Retry-After") ?? null) };
    })();
    exchange.catch(() => {}); // an attempt that lost the race may still fail later; nobody is waiting for it
    try {
      return await Promise.race([exchange, timedOut]);
    } finally {
      this.clearTimeoutFn(timer);
    }
  }

  /** The reporter acknowledges a finished (failed or dropped) report; a report still waiting cannot be dismissed. */
  async dismiss(reportId) {
    const entry = (await this.store.list()).find((e) => e.report_id === reportId);
    if (!entry || entry.state === STATES.QUEUED) return false;
    await this.store.delete(reportId);
    return true;
  }

  /** What the page shows: waiting reports (oldest first), finished ones, and when to look again. */
  async snapshot() {
    const all = (await this.store.list()).sort(byAge);
    const queued = all.filter((e) => e.state === STATES.QUEUED);
    const finished = all.filter((e) => e.state !== STATES.QUEUED);
    return { queued, finished, wakeAt: this.wakeAt(queued) };
  }

  /** When the oldest waiting report is next due, or when its 24 hours end if that comes first; null if nothing waits. */
  wakeAt(queued) {
    const head = [...queued].sort(byAge)[0];
    return head ? Math.min(Math.max(head.next_attempt_at, 0), head.created_at + WINDOW_MS + 1) : null;
  }
}
