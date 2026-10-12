import assert from "node:assert/strict";

// Test doubles for the page's modules: a clock with timers, a store that logs every operation, a fetch that follows a script, a DOM that
// refuses innerHTML, and a small IndexedDB. Nothing here is loaded by the page.

export const tick = () => new Promise((resolve) => setImmediate(resolve));

export async function flush(times = 10) {
  for (let i = 0; i < times; i++) await tick();
}

/** The promise's outcome, or a rejection "never settled" if it has not settled in `ms`: a hung request fails a test by assertion, not by timeout. */
export function settles(promise, ms = 300) {
  let timer;
  const hung = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("never settled")), ms);
  });
  return Promise.race([promise, hung]).finally(() => clearTimeout(timer));
}

/** A clock plus timers that only move when advance() is called. */
export class FakeClock {
  constructor(start = 1_800_000_000_000) {
    this.t = start;
    this.timers = new Map();
    this.nextId = 1;
    this.setTimeoutFn = (fn, ms) => {
      const id = this.nextId++;
      this.timers.set(id, { fn, at: this.t + ms });
      return id;
    };
    this.clearTimeoutFn = (id) => {
      this.timers.delete(id);
    };
    this.now = () => this.t;
  }

  /** Move time forward, firing due timers in order (and letting promises settle between them). */
  async advance(ms) {
    const end = this.t + ms;
    for (;;) {
      const due = [...this.timers.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]);
      this.t = Math.max(this.t, due[1].at);
      due[1].fn();
      await flush(30);
    }
    this.t = end;
    await flush(30);
  }

  pendingTimers() {
    return this.timers.size;
  }
}

/** A store that keeps entries in a Map (copies, like IndexedDB's structured clone) and records every operation in `log`. */
export class FakeStore {
  constructor(log = []) {
    this.map = new Map();
    this.log = log;
    this.failPuts = false;
    this.persistent = true;
    this.operations = 0;
  }

  /** A queue that loops without end would starve the test runner; this turns it into an assertion failure. */
  _use() {
    this.operations += 1;
    if (this.operations > 5000) assert.fail("the store was used more than 5000 times: a runaway loop");
  }

  async list() {
    this._use();
    return [...this.map.values()].map((e) => structuredClone(e));
  }

  async put(entry) {
    this._use();
    if (this.failPuts) throw new Error("store is full");
    this.log.push(["put", entry.report_id, entry.state, entry.attempts]);
    this.map.set(entry.report_id, structuredClone(entry));
  }

  async delete(id) {
    this._use();
    this.log.push(["delete", id]);
    this.map.delete(id);
  }

  get deletes() {
    return this.log.filter((op) => op[0] === "delete");
  }

  entry(id) {
    return this.map.get(id);
  }
}

/** A response as fetch would give it. */
export function reply(status, json = {}, headers = {}) {
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  return {
    status,
    headers: { get: (name) => lower[name.toLowerCase()] ?? null },
    json: async () => {
      if (json === "BAD") throw new SyntaxError("not json");
      return json;
    },
  };
}

/**
 * A fetch that follows a script: each step is a reply(), {throws: Error}, or {hang: true} (never answers, but rejects when aborted).
 * After the script is spent it repeats `otherwise`. Every call is recorded in `calls`, and in `log` if one is given.
 */
export function scriptedFetch(steps, { log = null, otherwise = null } = {}) {
  const queue = [...steps];
  const calls = [];
  const fn = async (url, init = {}) => {
    calls.push({ url, init, body: init.body, signal: init.signal });
    if (calls.length > 60) assert.fail("fetch was called more than 60 times: a runaway loop");
    if (log) log.push(["fetch", url]);
    const step = queue.length ? queue.shift() : otherwise;
    if (!step) throw new Error("fetch script exhausted");
    if (step.throws) throw step.throws;
    if (step.hang) {
      return new Promise((_, reject) => {
        init.signal?.addEventListener("abort", () => reject(new Error("AbortError")));
      });
    }
    if (step.wait) await step.wait;
    return step.response ?? step;
  };
  fn.calls = calls;
  return fn;
}

export const okReport = (extra = {}) => ({
  relevance_score: 0.95, midichlorian_ppm: 26400, kyber_resonance: 30, dark_side_activity: 85, inference_rationale: "A named figure.",
  event_id: "01M4PVNTX39MGBBXJHK2F9ZCCK", event_time: "2026-10-14T14:59:00.000Z", inference_model: "m", ...extra,
});

let reportCounter = 0;
export function makeReport(overrides = {}) {
  reportCounter += 1;
  const id = `01KX${String(reportCounter).padStart(22, "0")}`;
  return { report_id: id, sector_id: "coruscant",
    body: JSON.stringify({ sector_id: "coruscant", description: "Saw Darth Vader", report_id: id, reporter_id: "a3f2b7c9d1e4",
      observed_at: "2026-10-14T14:59:00.000Z" }), ...overrides };
}

// ---- a DOM that refuses to be written as HTML --------------------------------------------------------------
export class FakeElement {
  constructor(tag, doc) {
    this.tagName = tag.toUpperCase();
    this.doc = doc;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this._text = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.className = "";
  }

  get textContent() {
    return this._text + this.children.map((c) => c.textContent).join("");
  }

  set textContent(value) {
    this.children = [];
    this._text = String(value);
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  replaceChildren(...nodes) {
    this._text = "";
    this.children = [...nodes];
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }

  async fire(type, event = {}) {
    for (const fn of this.listeners[type] || []) await fn({ preventDefault() {}, ...event });
  }

  find(predicate, found = []) {
    if (predicate(this)) found.push(this);
    for (const child of this.children) child.find(predicate, found);
    return found;
  }

  set innerHTML(_) {
    assert.fail("innerHTML must never be used");
  }

  set outerHTML(_) {
    assert.fail("outerHTML must never be used");
  }

  insertAdjacentHTML() {
    assert.fail("insertAdjacentHTML must never be used");
  }
}

export class FakeDocument {
  constructor(ids = []) {
    this.byId = {};
    for (const id of ids) this.byId[id] = new FakeElement(id === "report-form" ? "form" : "div", this);
  }

  getElementById(id) {
    return this.byId[id] || null;
  }

  createElement(tag) {
    return new FakeElement(tag, this);
  }
}

export const PAGE_IDS = ["report-form", "planet", "description", "counter", "submit", "message", "confirmation", "recent-list", "recent-note",
  "queue-status", "queue-list"];

// ---- IndexedDB, as much as store.js uses ------------------------------------------------------------------
export class FakeIDB {
  constructor({ failOpen = false, blocked = false, failTx = false, deferComplete = false } = {}) {
    Object.assign(this, { failOpen, blocked, failTx, deferComplete });
    this.stores = new Map();
    this.created = [];
    this.pendingCompletes = [];
    this.opens = 0;
    this.transactions = 0;
  }

  open(name, version) {
    this.opens += 1;
    const request = {};
    setImmediate(() => {
      if (this.failOpen) {
        request.error = new Error("open failed");
        return request.onerror?.();
      }
      if (this.blocked) return request.onblocked?.();
      const db = {
        objectStoreNames: { contains: (n) => this.stores.has(n) },
        createObjectStore: (n, opts) => {
          this.stores.set(n, new Map());
          this.created.push({ name: n, opts });
        },
        transaction: (storeName, mode) => this._tx(storeName, mode),
      };
      request.result = db;
      if (!this.stores.has("queue")) request.onupgradeneeded?.();
      request.onsuccess?.();
    });
    return request;
  }

  _tx(storeName, mode) {
    this.transactions += 1;
    if (this.transactions > 5000) assert.fail("IndexedDB was used more than 5000 times: a runaway loop");
    const tx = { error: null };
    const data = this.stores.get(storeName);
    const request = (work) => {
      const req = {};
      setImmediate(() => {
        if (this.failTx) {
          tx.error = new Error("transaction failed");
          return (this.failTx === "error" ? tx.onerror : tx.onabort)?.();
        }
        req.result = work();
        req.onsuccess?.();
        const finish = () => tx.oncomplete?.();
        if (this.deferComplete) this.pendingCompletes.push(finish);
        else setImmediate(finish);
      });
      return req;
    };
    tx.objectStore = () => ({
      getAll: () => request(() => [...data.values()].map((v) => structuredClone(v))),
      put: (entry) => request(() => {
        data.set(entry.report_id, structuredClone(entry));
        return entry.report_id;
      }),
      delete: (id) => request(() => {
        data.delete(id);
      }),
    });
    return tx;
  }

  completeAll() {
    for (const finish of this.pendingCompletes.splice(0)) finish();
  }
}
