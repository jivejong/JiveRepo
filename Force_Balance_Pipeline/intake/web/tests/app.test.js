// The page's behaviour end to end, with a fake document, fetch, clock and IndexedDB.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { validateDescription } from "../api.js";
import { startApp } from "../app.js";
import { REPORTER_RE } from "../identity.js";
import { ULID_RE } from "../ulid.js";
import { IndexedDbStore } from "../store.js";
import { FakeClock, FakeDocument, FakeIDB, PAGE_IDS, flush, okReport, reply } from "./helpers.js";

const SECTORS = [{ sector_id: "coruscant", sector_name: "Coruscant" }, { sector_id: "tatooine", sector_name: "Tatooine" }];
const HOSTILE = "<img src=x onerror=alert(1)>";

const memoryStorage = () => {
  const data = {};
  return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, data };
};

/** A fetch that answers by URL. A handler is a reply, {throws}, or a function of (url, init) returning either (or a promise of one). */
function routerFetch(handlers) {
  const calls = [];
  const fn = async (url, init = {}) => {
    calls.push({ url, init, body: init.body });
    if (calls.length > 80) assert.fail("fetch was called more than 80 times: a runaway loop");
    const key = Object.keys(handlers).find((k) => k === url) ?? Object.keys(handlers).find((k) => k.endsWith("*") && url.startsWith(k.slice(0, -1)));
    if (!key) throw new Error(`no handler for ${url}`);
    const handler = handlers[key];
    const result = typeof handler === "function" ? await handler(url, init) : handler;
    if (result && result.throws) throw result.throws;
    return result;
  };
  fn.calls = calls;
  fn.to = (prefix) => calls.filter((c) => c.url.startsWith(prefix));
  return fn;
}

async function boot({ handlers = {}, idb = new FakeIDB(), storage = memoryStorage(), clock = new FakeClock(), lock = null } = {}) {
  const table = { "/api/sectors": reply(200, SECTORS), "/api/reports*": reply(200, []), "/api/report": reply(200, okReport()), ...handlers };
  const doc = new FakeDocument(PAGE_IDS);
  const fetchFn = routerFetch(table);
  const online = [];
  const visible = [];
  const app = await startApp({ document: doc, fetchFn, indexedDB: idb, getStorage: () => storage, now: clock.now, setTimeoutFn: clock.setTimeoutFn,
    clearTimeoutFn: clock.clearTimeoutFn, lock, listenOnline: (fn) => online.push(fn), listenVisible: (fn) => visible.push(fn) });
  return { app, doc, clock, fetchFn, table, online, visible, storage, idb, ui: app.ui };
}

async function choose(page, sectorId) {
  page.ui.planet.value = sectorId;
  await page.ui.planet.fire("change");
}

async function submit(page, sectorId, text) {
  await choose(page, sectorId);
  page.ui.description.value = text;
  await page.ui.form.fire("submit");
}

const queueTexts = (page) => page.ui.queueList.children.map((li) => li.textContent);

describe("starting up", () => {
  it("fills the planet picker from /api/sectors and sends no report", async () => {
    const page = await boot();
    assert.deepEqual(page.ui.planet.children.map((o) => [o.value, o.textContent]),
      [["", "Choose a planet"], ["coruscant", "Coruscant"], ["tatooine", "Tatooine"]]);
    assert.equal(page.fetchFn.calls[0].url, "/api/sectors");
    assert.equal(page.fetchFn.to("/api/report").filter((c) => c.url === "/api/report").length, 0);
    assert.equal(page.ui.confirmation.hidden, true);
    assert.equal(page.ui.counter.textContent, "0 / 8000");
    assert.equal(page.ui.recentNote.textContent, "Choose a planet to see its recent reports.");
    assert.equal(page.ui.queueStatus.textContent, "Nothing is waiting to be sent.");
  });

  it("says so when the planets cannot be loaded, and does not leave the page half-built", async () => {
    const page = await boot({ handlers: { "/api/sectors": reply(503) } });
    assert.match(page.ui.message.textContent, /Could not load the list of planets/);
    assert.equal(page.ui.planet.children.length, 0);
  });

  it("says so when the browser cannot keep reports", async () => {
    const page = await boot({ idb: null });
    assert.match(page.ui.queueStatus.textContent, /cannot keep reports between visits/);
    assert.equal((await boot()).ui.queueStatus.textContent.includes("cannot keep"), false);
  });

  it("counts characters as the server does", async () => {
    const page = await boot();
    page.ui.description.value = "h\u00e9llo \u{1F311}";
    await page.ui.description.fire("input");
    assert.equal(page.ui.counter.textContent, "7 / 8000");
  });
});

describe("checking before sending", () => {
  it("asks for a planet and sends nothing", async () => {
    const page = await boot();
    page.ui.description.value = "Saw a figure";
    await page.ui.form.fire("submit");
    assert.equal(page.ui.message.textContent, "Choose a planet first.");
    assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 0);
    assert.equal(page.ui.description.value, "Saw a figure");
    assert.deepEqual(await page.app.store.list(), []);
  });

  it("refuses empty and too-long text with the client rule's message, keeps the text, sends nothing", async () => {
    for (const text of ["   ", "a".repeat(8001), "bad\u0007text"]) {
      const page = await boot();
      await submit(page, "coruscant", text);
      assert.equal(page.ui.message.textContent, validateDescription(text).message);
      assert.match(page.ui.message.textContent, /\S/);
      assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 0);
      assert.equal(page.ui.description.value, text);
      assert.deepEqual(await page.app.store.list(), []);
    }
  });
});

describe("filing a report", () => {
  it("saves the report first, sends the whole fixed body once, shows what was inferred, and refreshes the recent list", async () => {
    let storedWhenSent = null;
    const page = await boot();
    page.table["/api/report"] = async () => {
      storedWhenSent = (await page.app.store.list()).length;
      return reply(200, okReport({ inference_rationale: "A named figure.", relevance_score: 0.95 }));
    };
    await submit(page, "coruscant", "Saw Darth Vader on the lower levels");
    assert.equal(storedWhenSent, 1);
    const posts = page.fetchFn.calls.filter((c) => c.url === "/api/report");
    assert.equal(posts.length, 1);
    const body = JSON.parse(posts[0].body);
    assert.deepEqual(Object.keys(body).sort(), ["description", "observed_at", "report_id", "reporter_id", "sector_id"]);
    assert.equal(body.sector_id, "coruscant");
    assert.equal(body.description, "Saw Darth Vader on the lower levels");
    assert.match(body.report_id, ULID_RE);
    assert.match(body.reporter_id, REPORTER_RE);
    assert.equal(body.observed_at, new Date(page.clock.now()).toISOString());
    assert.equal(page.ui.message.textContent, "Report filed.");
    assert.equal(page.ui.confirmation.hidden, false);
    assert.ok(page.ui.confirmation.textContent.includes("Why: A named figure."));
    assert.equal(page.ui.description.value, "");
    assert.equal(page.ui.counter.textContent, "0 / 8000");
    assert.equal(page.ui.submit.disabled, false);
    assert.deepEqual(await page.app.store.list(), []);
    assert.equal(page.ui.queueStatus.textContent, "Nothing is waiting to be sent.");
    assert.equal(page.fetchFn.to("/api/reports?sector_id=coruscant").length, 2); // on choosing the planet, and again once filed
  });

  it("files a delivered report even when the answer cannot be read, without a confirmation", async () => {
    const page = await boot({ handlers: { "/api/report": reply(200, "BAD") } });
    await submit(page, "coruscant", "Saw a figure");
    assert.equal(page.ui.message.textContent, "Report filed.");
    assert.equal(page.ui.confirmation.hidden, true);
    assert.deepEqual(await page.app.store.list(), []);
  });

  it("makes one report when submit is pressed twice at once", async () => {
    const page = await boot();
    await choose(page, "coruscant");
    page.ui.description.value = "Saw a figure";
    await Promise.all([page.app.onSubmit(), page.app.onSubmit()]);
    assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 1);
  });

  it("keeps the reporter's text and sends nothing if the browser cannot save the report", async () => {
    const page = await boot({ idb: null });
    page.app.store.put = async () => { throw new Error("quota"); };
    await submit(page, "coruscant", "Saw a figure");
    assert.match(page.ui.message.textContent, /could not save the report, so nothing was sent/);
    assert.equal(page.ui.description.value, "Saw a figure");
    assert.equal(page.ui.submit.disabled, false);
    assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 0);
    // and the page still works afterwards
    page.app.store.put = async function put(entry) { this.entries.set(entry.report_id, { ...entry }); };
    await page.ui.form.fire("submit");
    assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 1);
  });

  it("uses the same reporter id for every report and every visit", async () => {
    const storage = memoryStorage();
    const idb = new FakeIDB();
    const first = await boot({ storage, idb });
    await submit(first, "coruscant", "one");
    await submit(first, "coruscant", "two");
    const second = await boot({ storage, idb });
    await submit(second, "tatooine", "three");
    const ids = [first, first, second].map((p, i) => JSON.parse(p.fetchFn.calls.filter((c) => c.url === "/api/report")[i === 1 ? 1 : 0].body).reporter_id);
    assert.equal(new Set(ids).size, 1);
    assert.match(ids[0], REPORTER_RE);
  });
});

describe("when the report cannot be sent", () => {
  it("keeps it on the device, says so, retries on its own timer with the same body, and clears the queue when it lands", async () => {
    let calls = 0;
    const page = await boot({ handlers: { "/api/report": () => (++calls === 1 ? { throws: new TypeError("Failed to fetch") } : reply(200, okReport())) } });
    await submit(page, "coruscant", "Saw a figure");
    assert.match(page.ui.message.textContent, /could not be sent yet.*attempt 1 of 5/);
    assert.equal((await page.app.store.list()).length, 1);
    assert.match(page.ui.queueStatus.textContent, /^1 report is waiting to be sent\. Next attempt in 5 seconds\./);
    assert.equal(page.ui.description.value, "");
    await page.clock.advance(5050);
    await flush();
    const posts = page.fetchFn.calls.filter((c) => c.url === "/api/report");
    assert.equal(posts.length, 2);
    assert.equal(posts[0].body, posts[1].body);
    assert.equal(page.ui.message.textContent, "Report filed.");
    assert.deepEqual(await page.app.store.list(), []);
    assert.equal(page.clock.pendingTimers(), 0);
  });

  it("tries again when the browser comes back online or the tab becomes visible, once the wait is over", async () => {
    for (const which of ["online", "visible"]) {
      let calls = 0;
      const page = await boot({ handlers: { "/api/report": () => (++calls === 1 ? reply(503) : reply(200, okReport())) } });
      await submit(page, "coruscant", "Saw a figure");
      page.clock.timers.clear();
      page.clock.t += 6000;
      await Promise.all(page[which].map((fn) => fn()));
      assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 2, which);
      assert.deepEqual(await page.app.store.list(), [], which);
    }
  });

  it("does not skip the wait just because the browser says it is online", async () => {
    const page = await boot({ handlers: { "/api/report": reply(503) } });
    await submit(page, "coruscant", "Saw a figure");
    await Promise.all(page.online.map((fn) => fn()));
    assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 1);
  });

  it("sends a report that was waiting when the tab was closed, unchanged, on the next visit", async () => {
    const idb = new FakeIDB();
    const storage = memoryStorage();
    const first = await boot({ idb, storage, handlers: { "/api/report": reply(503) } });
    await submit(first, "coruscant", "Saw a figure");
    assert.equal((await first.app.store.list()).length, 1);
    const sentBefore = first.fetchFn.calls.find((c) => c.url === "/api/report").body;
    const second = await boot({ idb, storage, clock: new FakeClock(first.clock.now() + 6000) });
    const posts = second.fetchFn.calls.filter((c) => c.url === "/api/report");
    assert.equal(posts.length, 1);
    assert.equal(posts[0].body, sentBefore);
    assert.deepEqual(await second.app.store.list(), []);
  });

  it("shows a refused report once, with the server's words, and lets the reporter dismiss it", async () => {
    const message = "This report could not be assessed. Try describing what you saw in different words.";
    const page = await boot({ handlers: { "/api/report": reply(422, { error: "blocked_by_model", message }) } });
    await submit(page, "coruscant", "Saw a figure");
    assert.equal(page.ui.message.textContent, message);
    assert.equal(page.ui.confirmation.hidden, true);
    assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 1);
    assert.equal(page.ui.queueList.children.length, 1);
    assert.equal(page.ui.queueList.children[0].className, "queue-dropped");
    assert.ok(queueTexts(page)[0].includes(message));
    await page.clock.advance(60 * 60 * 1000);
    assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 1);
    await page.ui.queueList.find((e) => e.tagName === "BUTTON")[0].fire("click");
    assert.equal(page.ui.queueList.children.length, 0);
    assert.deepEqual(await page.app.store.list(), []);
  });

  it("gives the text back after the model refuses it, when the box is empty", async () => {
    const page = await boot({ handlers: { "/api/report": reply(422, { error: "blocked_by_model", message: "Reword it." }) } });
    await submit(page, "coruscant", "Saw something odd near the docks");
    assert.equal(page.ui.message.textContent, "Reword it.");
    assert.equal(page.ui.description.value, "Saw something odd near the docks");
    assert.equal(page.ui.counter.textContent, `${Array.from("Saw something odd near the docks").length} / 8000`);
    assert.equal(page.ui.queueList.children[0].className, "queue-dropped");
  });

  it("copes with a stored report whose text cannot be read back", async () => {
    for (const body of ["not json", JSON.stringify({ description: 5 }), JSON.stringify({ description: "" }), "null"]) {
      const idb = new FakeIDB();
      const clock = new FakeClock();
      await new IndexedDbStore(idb).put({ report_id: "01KX0000000000000000000001", sector_id: "coruscant", body, created_at: clock.now(), attempts: 0,
        next_attempt_at: 0, state: "queued", status: null, message: null });
      const page = await boot({ idb, clock, handlers: { "/api/report": reply(422, { error: "blocked_by_model", message: "Reword it." }) } });
      assert.equal(page.ui.message.textContent, "Reword it.", body);
      assert.equal(page.ui.description.value, "", body);
    }
  });

  it("leaves what the reporter has started typing in the meantime alone", async () => {
    const page = await boot();
    page.table["/api/report"] = async () => {
      page.ui.description.value = "a new draft";
      return reply(422, { error: "blocked_by_model", message: "Reword it." });
    };
    await submit(page, "coruscant", "the refused text");
    assert.equal(page.ui.description.value, "a new draft");
  });

  it("gives the text back only for a refusal by the model", async () => {
    const answers = [reply(422, { error: "empty_description", message: "empty" }), reply(409, { error: "report_id_reused" }), reply(413, {}), reply(403, {}),
      reply(422, {}), reply(503), reply(429, {}, { "Retry-After": "60" })];
    for (const answer of answers) {
      const page = await boot({ handlers: { "/api/report": answer } });
      await submit(page, "coruscant", "some text");
      assert.equal(page.ui.description.value, "", `status ${answer.status}`);
    }
  });

  it("lets the reporter dismiss a report that is waiting on a 429, and no other waiting report", async () => {
    const held = await boot({ handlers: { "/api/report": reply(429, { message: "Daily limit." }, { "Retry-After": "3600" }) } });
    await submit(held, "coruscant", "one too many");
    assert.match(held.ui.queueStatus.textContent, /^1 report is waiting to be sent/);
    const buttons = held.ui.queueList.find((e) => e.tagName === "BUTTON");
    assert.equal(buttons.length, 1);
    await buttons[0].fire("click");
    assert.equal(held.ui.queueList.children.length, 0);
    assert.equal(held.ui.queueStatus.textContent, "Nothing is waiting to be sent.");
    assert.deepEqual(await held.app.store.list(), []);

    const retrying = await boot({ handlers: { "/api/report": reply(503) } });
    await submit(retrying, "coruscant", "model down");
    assert.equal(retrying.ui.queueList.find((e) => e.tagName === "BUTTON").length, 0);
  });

  it("shows 'could not be assessed' after the fifth failed attempt", async () => {
    const page = await boot({ handlers: { "/api/report": reply(503) } });
    await submit(page, "coruscant", "Saw a figure");
    for (const wait of [5000, 30_000, 120_000, 600_000]) await page.clock.advance(wait + 100);
    assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 5);
    assert.equal(page.ui.message.textContent, "This report could not be assessed.");
    assert.equal(page.ui.queueList.children[0].className, "queue-failed");
    await page.clock.advance(3 * 60 * 60 * 1000);
    assert.equal(page.fetchFn.calls.filter((c) => c.url === "/api/report").length, 5);
  });

  it("keeps a rate-limited report and says when it will be sent", async () => {
    const page = await boot({ handlers: { "/api/report": reply(429, { message: "Daily limit." }, { "Retry-After": "3600" }) } });
    await submit(page, "coruscant", "Saw a figure");
    assert.equal(page.ui.message.textContent, "Daily limit. It stays on this device and will be sent in 60 minutes.");
    assert.equal((await page.app.store.list()).length, 1);
    assert.match(page.ui.queueStatus.textContent, /Next attempt in 60 minutes/);
  });
});

describe("recent reports", () => {
  it("shows the chosen planet's reports as text", async () => {
    const report = { description: HOSTILE, event_time: "2026-10-14T14:59:00.000Z", relevance_score: 0.5, dark_side_activity: 40 };
    const page = await boot({ handlers: { "/api/reports*": reply(200, [report]) } });
    await choose(page, "tatooine");
    assert.equal(page.ui.recentList.children[0].children[0].textContent, HOSTILE);
    assert.deepEqual(page.ui.recentList.find((e) => e.tagName === "IMG"), []);
    assert.equal(page.fetchFn.to("/api/reports").at(-1).url, "/api/reports?sector_id=tatooine");
  });

  it("ignores an older answer that arrives after a newer one", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const item = (text) => ({ description: text, event_time: "t", relevance_score: 0.5, dark_side_activity: 1 });
    const page = await boot({ handlers: {
      "/api/reports?sector_id=coruscant": async () => { await gate; return reply(200, [item("from coruscant")]); },
      "/api/reports?sector_id=tatooine": reply(200, [item("from tatooine")]),
    } });
    page.ui.planet.value = "coruscant";
    const slow = page.app.showRecent();
    await flush();
    await choose(page, "tatooine");
    release();
    await slow;
    assert.deepEqual(page.ui.recentList.children.map((li) => li.children[0].textContent), ["from tatooine"]);
  });

  it("says so when they cannot be loaded, and when there are none", async () => {
    const page = await boot({ handlers: { "/api/reports*": reply(503) } });
    await choose(page, "coruscant");
    assert.equal(page.ui.recentNote.textContent, "Could not load the recent reports.");
    page.table["/api/reports*"] = reply(200, []);
    await choose(page, "coruscant");
    assert.equal(page.ui.recentNote.textContent, "No reports for this planet yet.");
    await choose(page, "");
    assert.equal(page.ui.recentNote.textContent, "Choose a planet to see its recent reports.");
  });
});
