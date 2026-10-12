// The report queue: every rule in queue.js's header, against a fake store, a fake fetch and a fake clock.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { BACKOFF_MS, DEFAULT_RATE_LIMIT_WAIT_MS, FETCH_TIMEOUT_MS, MAX_ATTEMPTS, MAX_RETRY_AFTER_MS, ReportQueue, STATES, WINDOW_MS, classify,
  parseRetryAfter } from "../queue.js";
import { FakeClock, FakeStore, flush, makeReport, okReport, reply, scriptedFetch } from "./helpers.js";

function setup({ steps = [], otherwise = null, lock = null, store = null, log = [] } = {}) {
  const clock = new FakeClock();
  const theStore = store || new FakeStore(log);
  const fetchFn = scriptedFetch(steps, { log, otherwise });
  const events = [];
  const queue = new ReportQueue({ store: theStore, fetchFn, now: clock.now, setTimeoutFn: clock.setTimeoutFn, clearTimeoutFn: clock.clearTimeoutFn,
    lock, onEvent: (e) => events.push(e) });
  return { log, clock, store: theStore, fetchFn, queue, events };
}

describe("the numbers", () => {
  it("are the ones decided: 5 attempts, 24 hours, a 60 second timeout", () => {
    assert.equal(MAX_ATTEMPTS, 5);
    assert.equal(WINDOW_MS, 24 * 60 * 60 * 1000);
    assert.equal(FETCH_TIMEOUT_MS, 60_000);
    assert.deepEqual(BACKOFF_MS, [5000, 30_000, 120_000, 600_000]);
    assert.equal(DEFAULT_RATE_LIMIT_WAIT_MS, 60_000);
  });

  it("classify sorts every status into sent / rate_limited / dropped / retry", () => {
    const expected = { 200: "sent", 429: "rate_limited", 400: "dropped", 403: "dropped", 404: "dropped", 409: "dropped", 413: "dropped",
      415: "dropped", 422: "dropped", 499: "dropped", 500: "retry", 502: "retry", 503: "retry", 504: "retry", 599: "retry", 0: "retry",
      201: "retry", 204: "retry", 301: "retry", 100: "retry" };
    for (const [status, kind] of Object.entries(expected)) assert.equal(classify(Number(status)), kind, status);
  });

  it("Retry-After is a whole number of seconds, capped at the 24 hour window; anything else is ignored", () => {
    assert.equal(parseRetryAfter("30"), 30_000);
    assert.equal(parseRetryAfter(" 30 "), 30_000);
    assert.equal(parseRetryAfter("0"), 0);
    assert.equal(parseRetryAfter("999999999"), MAX_RETRY_AFTER_MS);
    for (const bad of ["abc", "-5", "1.5", "", "99999999999", "Wed, 21 Oct 2026 07:28:00 GMT", null, undefined, 30]) {
      assert.equal(parseRetryAfter(bad), null, String(bad));
    }
  });
});

describe("saving and sending", () => {
  it("saves the report before anything is sent", async () => {
    const t = setup({ steps: [reply(200, okReport())] });
    const report = makeReport();
    await t.queue.enqueue(report);
    assert.equal(t.fetchFn.calls.length, 0);
    const entry = t.store.entry(report.report_id);
    assert.deepEqual([entry.state, entry.attempts, entry.body], ["queued", 0, report.body]);
  });

  it("does not add a report_id that is already there", async () => {
    const t = setup();
    const report = makeReport();
    const first = await t.queue.enqueue(report);
    const second = await t.queue.enqueue({ ...report, body: "different" });
    assert.equal(second.body, first.body);
    assert.equal((await t.store.list()).length, 1);
  });

  it("sends the saved body as JSON to /api/report and deletes the report only after the 200", async () => {
    const t = setup({ steps: [reply(200, okReport())] });
    const report = makeReport();
    await t.queue.enqueue(report);
    const outcomes = await t.queue.drain();
    assert.deepEqual(outcomes.map((o) => o.kind), ["sent"]);
    const call = t.fetchFn.calls[0];
    assert.equal(call.url, "/api/report");
    assert.equal(call.init.method, "POST");
    assert.equal(call.init.headers["Content-Type"], "application/json");
    assert.equal(call.body, report.body);
    assert.equal(t.store.entry(report.report_id), undefined);
    const ops = t.log.map((op) => op[0]);
    assert.ok(ops.indexOf("fetch") < ops.indexOf("delete"), "the delete came before the answer");
    assert.deepEqual(t.log.filter((op) => op[0] === "delete"), [["delete", report.report_id]]);
  });

  it("counts the attempt in the store before it sends, so a tab that dies mid-request still used it", async () => {
    const t = setup({ steps: [reply(200, okReport())] });
    const report = makeReport();
    await t.queue.enqueue(report);
    await t.queue.drain();
    const ops = t.log;
    const putWithAttempt = ops.findIndex((op) => op[0] === "put" && op[3] === 1);
    assert.ok(putWithAttempt !== -1 && putWithAttempt < ops.findIndex((op) => op[0] === "fetch"));
  });

  it("never sends a report it could not save first", async () => {
    const t = setup({ steps: [reply(200, okReport())] });
    await t.queue.enqueue(makeReport());
    t.store.failPuts = true;
    await assert.rejects(t.queue.drain(), /store is full/);
    assert.equal(t.fetchFn.calls.length, 0);
  });

  it("tells the page: queued, sending, sent (with the server's answer)", async () => {
    const t = setup({ steps: [reply(200, okReport({ relevance_score: 0.5 }))] });
    await t.queue.enqueue(makeReport());
    await t.queue.drain();
    assert.deepEqual(t.events.map((e) => e.type), ["queued", "sending", "sent"]);
    assert.equal(t.events[2].response.relevance_score, 0.5);
  });

  it("deletes a report that was delivered even when the answer's body cannot be read", async () => {
    const t = setup({ steps: [reply(200, "BAD")] });
    const report = makeReport();
    await t.queue.enqueue(report);
    const outcomes = await t.queue.drain();
    assert.equal(outcomes[0].kind, "sent");
    assert.equal(outcomes[0].response, null);
    assert.equal(t.store.entry(report.report_id), undefined);
  });

  it("sends exactly the same body and report_id on every attempt", async () => {
    const t = setup({ steps: [reply(503), reply(503), reply(200, okReport())] });
    const report = makeReport();
    await t.queue.enqueue(report);
    await t.queue.drain();
    await t.clock.advance(BACKOFF_MS[0]);
    await t.queue.drain();
    await t.clock.advance(BACKOFF_MS[1]);
    await t.queue.drain();
    assert.equal(t.fetchFn.calls.length, 3);
    assert.deepEqual(new Set(t.fetchFn.calls.map((c) => c.body)), new Set([report.body]));
  });
});

describe("what keeps a report in the store", () => {
  const stays = {
    "a 500": reply(500), "a 502": reply(502), "a 503": reply(503, { error: "x" }, { "Retry-After": "15" }), "a 504": reply(504),
    "a network error": { throws: new TypeError("Failed to fetch") }, "an unexpected 204": reply(204), "a redirect status": reply(301),
    "a 429": reply(429, { error: "daily_cap" }, { "Retry-After": "60" }),
    "a 400": reply(400), "a 403": reply(403), "a 409": reply(409), "a 413": reply(413), "a 422": reply(422), "a 404": reply(404),
  };
  for (const [name, step] of Object.entries(stays)) {
    it(`never deletes on ${name}`, async () => {
      const t = setup({ steps: [step] });
      const report = makeReport();
      await t.queue.enqueue(report);
      await t.queue.drain();
      assert.equal(t.store.deletes.length, 0);
      assert.ok(t.store.entry(report.report_id));
    });
  }

  it("never deletes on a timeout either", async () => {
    const t = setup({ steps: [{ hang: true }] });
    const report = makeReport();
    await t.queue.enqueue(report);
    const drained = t.queue.drain();
    await flush();
    await t.clock.advance(FETCH_TIMEOUT_MS);
    await drained;
    assert.equal(t.store.deletes.length, 0);
    assert.equal(t.store.entry(report.report_id).state, STATES.QUEUED);
  });
});

describe("retrying", () => {
  it("keeps a report that met a 5xx, counts the attempt and waits before the next", async () => {
    const t = setup({ steps: [reply(503)] });
    const report = makeReport();
    await t.queue.enqueue(report);
    const outcomes = await t.queue.drain();
    assert.deepEqual(outcomes.map((o) => o.kind), ["retry"]);
    const entry = t.store.entry(report.report_id);
    assert.deepEqual([entry.state, entry.attempts, entry.status], ["queued", 1, 503]);
    assert.equal(entry.next_attempt_at, t.clock.now() + BACKOFF_MS[0]);
    assert.match(entry.message, /attempt 1 of 5/);
    assert.equal(t.events.at(-1).type, "retry");
  });

  it("does not try again before the wait is over, and does after", async () => {
    const t = setup({ steps: [reply(503), reply(200, okReport())] });
    await t.queue.enqueue(makeReport());
    await t.queue.drain();
    await t.clock.advance(BACKOFF_MS[0] - 1);
    assert.deepEqual((await t.queue.drain()).map((o) => o.kind), ["waiting"]);
    assert.equal(t.fetchFn.calls.length, 1);
    await t.clock.advance(1);
    assert.deepEqual((await t.queue.drain()).map((o) => o.kind), ["sent"]);
  });

  it("backs off 5 s, 30 s, 2 min, 10 min", async () => {
    const t = setup({ otherwise: reply(500) });
    const report = makeReport();
    await t.queue.enqueue(report);
    const waits = [];
    for (let i = 0; i < 4; i++) {
      const before = t.clock.now();
      await t.queue.drain();
      waits.push(t.store.entry(report.report_id).next_attempt_at - before);
      await t.clock.advance(waits[i]);
    }
    assert.deepEqual(waits, BACKOFF_MS);
  });

  it("honours Retry-After as a minimum, never a shortcut", async () => {
    const t = setup({ steps: [reply(503, {}, { "Retry-After": "120" }), reply(503, {}, { "Retry-After": "1" })] });
    const report = makeReport();
    await t.queue.enqueue(report);
    await t.queue.drain();
    assert.equal(t.store.entry(report.report_id).next_attempt_at - t.clock.now(), 120_000);
    await t.clock.advance(120_000);
    await t.queue.drain();
    assert.equal(t.store.entry(report.report_id).next_attempt_at - t.clock.now(), BACKOFF_MS[1]);
  });

  it("treats a network error like a 5xx", async () => {
    const t = setup({ steps: [{ throws: new TypeError("Failed to fetch") }, reply(200, okReport())] });
    const report = makeReport();
    await t.queue.enqueue(report);
    assert.equal((await t.queue.drain())[0].kind, "retry");
    assert.equal(t.store.entry(report.report_id).attempts, 1);
    await t.clock.advance(BACKOFF_MS[0]);
    assert.equal((await t.queue.drain())[0].kind, "sent");
  });

  it("treats a status other than 200 that is not a 4xx as a failed attempt, not a delivery", async () => {
    for (const status of [201, 204, 301]) {
      const t = setup({ steps: [reply(status)] });
      const report = makeReport();
      await t.queue.enqueue(report);
      assert.equal((await t.queue.drain())[0].kind, "retry", String(status));
      assert.equal(t.store.entry(report.report_id).attempts, 1);
    }
  });
});

describe("the timeout", () => {
  it("gives up on a request after 60 seconds, aborts it, counts the attempt and clears its timer", async () => {
    const t = setup({ steps: [{ hang: true }, reply(200, okReport())] });
    const report = makeReport();
    await t.queue.enqueue(report);
    const drained = t.queue.drain();
    await flush();
    await t.clock.advance(FETCH_TIMEOUT_MS - 1);
    assert.equal(t.fetchFn.calls[0].signal.aborted, false);
    await t.clock.advance(1);
    const outcomes = await drained;
    assert.equal(t.fetchFn.calls[0].signal.aborted, true);
    assert.deepEqual(outcomes.map((o) => o.kind), ["retry"]);
    assert.equal(t.store.entry(report.report_id).attempts, 1);
    assert.equal(t.clock.pendingTimers(), 0);
  });

  it("clears the timer after a quick answer", async () => {
    const t = setup({ steps: [reply(200, okReport())] });
    await t.queue.enqueue(makeReport());
    await t.queue.drain();
    assert.equal(t.clock.pendingTimers(), 0);
  });

  it("uses the timeout it was given", async () => {
    const t = setup({ steps: [{ hang: true }] });
    t.queue.timeoutMs = 1000;
    await t.queue.enqueue(makeReport());
    const drained = t.queue.drain();
    await flush();
    await t.clock.advance(1000);
    assert.equal((await drained)[0].kind, "retry");
  });
});

describe("at most 5 attempts, and none after 24 hours", () => {
  it("marks the report could-not-be-assessed after the fifth failed attempt and stops", async () => {
    const t = setup({ otherwise: reply(503) });
    const report = makeReport();
    await t.queue.enqueue(report);
    for (let i = 1; i <= MAX_ATTEMPTS; i++) {
      await t.queue.drain();
      if (i < MAX_ATTEMPTS) await t.clock.advance(BACKOFF_MS[i - 1]);
    }
    assert.equal(t.fetchFn.calls.length, 5);
    const entry = t.store.entry(report.report_id);
    assert.deepEqual([entry.state, entry.attempts, entry.message], ["failed", 5, "This report could not be assessed."]);
    assert.equal(t.events.at(-1).type, "failed");
    await t.clock.advance(2 * 60 * 60 * 1000);
    assert.deepEqual(await t.queue.drain(), []);
    assert.equal(t.fetchFn.calls.length, 5);
    assert.equal(t.store.deletes.length, 0);
  });

  it("does not fail a report on its fourth failed attempt", async () => {
    const t = setup({ otherwise: reply(503) });
    const report = makeReport();
    await t.queue.enqueue(report);
    for (let i = 1; i <= 4; i++) {
      await t.queue.drain();
      await t.clock.advance(BACKOFF_MS[i - 1]);
    }
    assert.equal(t.store.entry(report.report_id).state, "queued");
  });

  it("sends nothing once 24 hours have passed since the first submit", async () => {
    const t = setup({ steps: [reply(200, okReport())] });
    const report = makeReport();
    await t.queue.enqueue(report);
    await t.clock.advance(WINDOW_MS + 1);
    assert.deepEqual((await t.queue.drain()).map((o) => o.kind), ["failed"]);
    assert.equal(t.fetchFn.calls.length, 0);
    const entry = t.store.entry(report.report_id);
    assert.deepEqual([entry.state, entry.reason, entry.message], ["failed", "expired", "This report could not be assessed."]);
  });

  it("still sends at exactly 24 hours", async () => {
    const t = setup({ steps: [reply(200, okReport())] });
    await t.queue.enqueue(makeReport());
    await t.clock.advance(WINDOW_MS);
    assert.deepEqual((await t.queue.drain()).map((o) => o.kind), ["sent"]);
  });

  it("does not let a failed report block the ones behind it", async () => {
    const t = setup({ steps: [reply(500), reply(200, okReport())] });
    const first = makeReport();
    const second = makeReport();
    await t.queue.enqueue(first);
    await t.clock.advance(1);
    await t.queue.enqueue(second);
    t.store.map.get(first.report_id).attempts = MAX_ATTEMPTS - 1;
    const kinds = (await t.queue.drain()).map((o) => o.kind);
    assert.deepEqual(kinds, ["failed", "sent"]);
  });
});

describe("a 429", () => {
  it("keeps the report, waits at least Retry-After, and does not count it as an attempt", async () => {
    const t = setup({ steps: [reply(429, { error: "reporter_daily_cap", message: "You have filed as many reports as are allowed today." },
      { "Retry-After": "300" })] });
    const report = makeReport();
    await t.queue.enqueue(report);
    const outcomes = await t.queue.drain();
    assert.deepEqual([outcomes[0].kind, outcomes[0].waitMs], ["rate_limited", 300_000]);
    const entry = t.store.entry(report.report_id);
    assert.deepEqual([entry.state, entry.attempts, entry.status], ["queued", 0, 429]);
    assert.equal(entry.next_attempt_at, t.clock.now() + 300_000);
    assert.match(entry.message, /You have filed as many reports as are allowed today\. It stays on this device and will be sent in 5 minutes\./);
    assert.equal(t.events.at(-1).type, "rate_limited");
  });

  it("does not try again early", async () => {
    const t = setup({ steps: [reply(429, {}, { "Retry-After": "300" }), reply(200, okReport())] });
    await t.queue.enqueue(makeReport());
    await t.queue.drain();
    await t.clock.advance(299_999);
    assert.deepEqual((await t.queue.drain()).map((o) => o.kind), ["waiting"]);
    await t.clock.advance(1);
    assert.deepEqual((await t.queue.drain()).map((o) => o.kind), ["sent"]);
  });

  it("can happen more than five times without failing the report", async () => {
    const t = setup({ steps: [...Array(7).fill(reply(429, {}, { "Retry-After": "30" })), reply(200, okReport())] });
    const report = makeReport();
    await t.queue.enqueue(report);
    for (let i = 0; i < 7; i++) {
      await t.queue.drain();
      assert.equal(t.store.entry(report.report_id).attempts, 0);
      assert.equal(t.store.entry(report.report_id).state, "queued");
      await t.clock.advance(30_000);
    }
    assert.deepEqual((await t.queue.drain()).map((o) => o.kind), ["sent"]);
    assert.equal(t.fetchFn.calls.length, 8);
  });

  it("waits a minute when Retry-After is missing or unusable", async () => {
    for (const header of [{}, { "Retry-After": "soon" }, { "Retry-After": "-1" }]) {
      const t = setup({ steps: [reply(429, {}, header)] });
      const report = makeReport();
      await t.queue.enqueue(report);
      await t.queue.drain();
      assert.equal(t.store.entry(report.report_id).next_attempt_at - t.clock.now(), 60_000, JSON.stringify(header));
    }
  });

  it("still ends at 24 hours from the first submit", async () => {
    const t = setup({ steps: [reply(429, {}, { "Retry-After": "86400" })] });
    const report = makeReport();
    await t.queue.enqueue(report);
    await t.clock.advance(60_000);
    await t.queue.drain();
    assert.equal(t.store.entry(report.report_id).next_attempt_at, t.clock.now() + WINDOW_MS);
    const snapshot = await t.queue.snapshot();
    assert.equal(snapshot.wakeAt, t.store.entry(report.report_id).created_at + WINDOW_MS + 1);
    await t.clock.advance(WINDOW_MS);
    assert.deepEqual((await t.queue.drain()).map((o) => o.kind), ["failed"]);
  });
});

describe("a 4xx other than 429 ends the report", () => {
  const cases = [
    [409, { error: "report_id_reused", message: "server text" }, "That report number was already used for a different report, so this one was not sent."],
    [413, { error: "body_too_large", message: "server text" }, "That report is too long to send."],
    [422, { error: "blocked_by_model", message: "This report could not be assessed. Try describing what you saw in different words." },
      "This report could not be assessed. Try describing what you saw in different words."],
    [422, { error: "empty_description", message: "the description is empty" }, "the description is empty"],
    [422, {}, "That report could not be accepted."],
    [400, {}, "The report was refused (error 400)."],
    [403, { message: "This service answers only on its own address." }, "This service answers only on its own address."],
    [404, "BAD", "The report was refused (error 404)."],
  ];
  for (const [status, body, message] of cases) {
    it(`drops it on ${status}${body && body.error ? ` ${body.error}` : ""}, with a message, and never asks again`, async () => {
      const t = setup({ steps: [reply(status, body)], otherwise: reply(200, okReport()) });
      const report = makeReport();
      await t.queue.enqueue(report);
      const outcomes = await t.queue.drain();
      assert.deepEqual([outcomes[0].kind, outcomes[0].status], ["dropped", status]);
      const entry = t.store.entry(report.report_id);
      assert.deepEqual([entry.state, entry.message, entry.status], ["dropped", message, status]);
      assert.equal(t.events.at(-1).type, "dropped");
      await t.clock.advance(WINDOW_MS);
      await t.queue.drain();
      assert.equal(t.fetchFn.calls.length, 1);
      assert.equal(t.store.deletes.length, 0);
    });
  }

  it("lets the reports behind it go ahead", async () => {
    const t = setup({ steps: [reply(422), reply(200, okReport())] });
    const first = makeReport();
    const second = makeReport();
    await t.queue.enqueue(first);
    await t.clock.advance(1);
    await t.queue.enqueue(second);
    assert.deepEqual((await t.queue.drain()).map((o) => o.kind), ["dropped", "sent"]);
  });
});

describe("order and no double submit", () => {
  async function three(t) {
    const reports = [makeReport(), makeReport(), makeReport()];
    for (const report of reports) {
      await t.queue.enqueue(report);
      await t.clock.advance(1);
    }
    return reports;
  }

  it("sends the oldest first", async () => {
    const t = setup({ otherwise: reply(200, okReport()) });
    const reports = await three(t);
    await t.queue.drain();
    assert.deepEqual(t.fetchFn.calls.map((c) => c.body), reports.map((r) => r.body));
  });

  it("does not let a newer report overtake an older one that is waiting", async () => {
    const t = setup({ steps: [reply(503)], otherwise: reply(200, okReport()) });
    const reports = await three(t);
    await t.queue.drain();
    assert.equal(t.fetchFn.calls.length, 1);
    assert.equal(t.store.entry(reports[1].report_id).attempts, 0);
    await t.clock.advance(BACKOFF_MS[0]);
    await t.queue.drain();
    assert.deepEqual(t.fetchFn.calls.map((c) => c.body), [reports[0].body, reports[0].body, reports[1].body, reports[2].body]);
  });

  it("puts reports made in the same millisecond in report_id order", async () => {
    const t = setup({ otherwise: reply(200, okReport()) });
    const b = makeReport();
    const a = makeReport();
    const [low, high] = [a, b].sort((x, y) => (x.report_id < y.report_id ? -1 : 1));
    await t.queue.enqueue(high);
    await t.queue.enqueue(low);
    await t.queue.drain();
    assert.deepEqual(t.fetchFn.calls.map((c) => c.body), [low.body, high.body]);
  });

  it("makes one request when drain is called twice at once", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const t = setup({ steps: [{ wait: gate, response: reply(200, okReport()) }] });
    await t.queue.enqueue(makeReport());
    const first = t.queue.drain();
    const second = t.queue.drain();
    await flush();
    release();
    const [a, b] = await Promise.all([first, second]);
    assert.equal(t.fetchFn.calls.length, 1);
    assert.deepEqual(a, b);
  });

  it("never has two requests in flight, even when a report is queued during a drain", async () => {
    let inFlight = 0;
    let peak = 0;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const t = setup();
    t.queue.fetchFn = async (url, init) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await gate;
      inFlight -= 1;
      return reply(200, okReport());
    };
    await t.queue.enqueue(makeReport());
    const drained = t.queue.drain();
    await flush();
    await t.clock.advance(1);
    await t.queue.enqueue(makeReport());
    const again = t.queue.drain();
    release();
    await Promise.all([drained, again]);
    assert.equal(peak, 1);
    assert.equal((await t.store.list()).length, 0);
  });

  it("sends a report that arrives just as a running drain finds nothing left", async () => {
    const t = setup({ otherwise: reply(200, okReport()) });
    const first = makeReport();
    const late = makeReport();
    await t.queue.enqueue(first);
    const original = t.store.list.bind(t.store);
    let lists = 0;
    let lateDrain = null;
    t.store.list = async () => {
      const result = await original();
      lists += 1;
      if (lists === 2) { // the second look finds nothing; the late report and its drain request arrive right after
        t.store.map.set(late.report_id, { report_id: late.report_id, sector_id: late.sector_id, body: late.body, created_at: t.clock.now(), attempts: 0,
          next_attempt_at: 0, state: "queued", status: null, message: null });
        lateDrain = t.queue.drain();
      }
      return result;
    };
    await t.queue.drain();
    await lateDrain;
    assert.deepEqual(t.fetchFn.calls.map((c) => c.body), [first.body, late.body]);
    assert.equal(t.store.map.size, 0);
  });

  it("runs inside the cross-tab lock", async () => {
    const order = [];
    const lock = async (fn) => {
      order.push("lock");
      try {
        return await fn();
      } finally {
        order.push("unlock");
      }
    };
    const t = setup({ steps: [reply(200, okReport())], lock });
    await t.queue.enqueue(makeReport());
    await t.queue.drain();
    assert.deepEqual(order, ["lock", "unlock"]);
  });

  it("sends once when two tabs share the store and the lock", async () => {
    let tail = Promise.resolve();
    const mutex = (fn) => {
      const run = tail.then(fn);
      tail = run.catch(() => {});
      return run;
    };
    const store = new FakeStore();
    const a = setup({ steps: [reply(200, okReport())], lock: mutex, store });
    const b = setup({ steps: [reply(200, okReport())], lock: mutex, store });
    await a.queue.enqueue(makeReport());
    await Promise.all([a.queue.drain(), b.queue.drain()]);
    assert.equal(a.fetchFn.calls.length + b.fetchFn.calls.length, 1);
  });
});

describe("dismissing and the snapshot", () => {
  it("lets the reporter dismiss a finished report but not one that is still waiting", async () => {
    const t = setup({ steps: [reply(422), reply(503)] });
    const dropped = makeReport();
    const waiting = makeReport();
    await t.queue.enqueue(dropped);
    await t.clock.advance(1);
    await t.queue.enqueue(waiting);
    await t.queue.drain();
    assert.equal(await t.queue.dismiss(waiting.report_id), false);
    assert.ok(t.store.entry(waiting.report_id));
    assert.equal(await t.queue.dismiss(dropped.report_id), true);
    assert.equal(t.store.entry(dropped.report_id), undefined);
    assert.equal(await t.queue.dismiss("no-such-report"), false);
  });

  it("lists waiting reports oldest first and finished ones apart, with the time to look again", async () => {
    const t = setup({ steps: [reply(503)] });
    const first = makeReport();
    const second = makeReport();
    await t.queue.enqueue(first);
    await t.clock.advance(1);
    await t.queue.enqueue(second);
    await t.queue.drain();
    const snapshot = await t.queue.snapshot();
    assert.deepEqual(snapshot.queued.map((e) => e.report_id), [first.report_id, second.report_id]);
    assert.deepEqual(snapshot.finished, []);
    assert.equal(snapshot.wakeAt, t.store.entry(first.report_id).next_attempt_at);
  });

  it("has nothing to wake for when nothing waits", async () => {
    const t = setup();
    assert.equal((await t.queue.snapshot()).wakeAt, null);
  });

  it("wakes at the 24 hour mark if that comes before the next attempt", async () => {
    const t = setup();
    const report = makeReport();
    await t.queue.enqueue(report);
    t.store.map.get(report.report_id).next_attempt_at = t.clock.now() + 3 * WINDOW_MS;
    assert.equal((await t.queue.snapshot()).wakeAt, t.store.entry(report.report_id).created_at + WINDOW_MS + 1);
  });
});
