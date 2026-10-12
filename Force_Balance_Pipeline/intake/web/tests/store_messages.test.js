// The stores (memory and IndexedDB) and the reporter-facing texts.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { COULD_NOT_BE_ASSESSED, formatWait, messageForDropped, messageForRateLimited, messageForRetry } from "../messages.js";
import { DB_NAME, DB_VERSION, IndexedDbStore, MemoryStore, STORE_NAME, openStore } from "../store.js";
import { FakeIDB, flush, settles } from "./helpers.js";

const entry = (id, extra = {}) => ({ report_id: id, sector_id: "coruscant", state: "queued", attempts: 0, ...extra });

describe("MemoryStore", () => {
  it("keeps entries by report_id, replacing on a second put", async () => {
    const store = new MemoryStore();
    await store.put(entry("a"));
    await store.put(entry("a", { attempts: 2 }));
    await store.put(entry("b"));
    const listed = await store.list();
    assert.equal(listed.length, 2);
    assert.equal(listed.find((e) => e.report_id === "a").attempts, 2);
  });

  it("hands out copies, so a caller cannot change what is stored", async () => {
    const store = new MemoryStore();
    const original = entry("a");
    await store.put(original);
    original.attempts = 99;
    (await store.list())[0].attempts = 77;
    assert.equal((await store.list())[0].attempts, 0);
  });

  it("deletes, and says it is not persistent", async () => {
    const store = new MemoryStore();
    await store.put(entry("a"));
    await store.delete("a");
    await store.delete("never-there");
    assert.deepEqual(await store.list(), []);
    assert.equal(store.persistent, false);
  });
});

describe("IndexedDbStore", () => {
  it("creates its object store keyed by report_id on first open, and says it is persistent", async () => {
    const idb = new FakeIDB();
    const store = new IndexedDbStore(idb);
    assert.deepEqual(await store.list(), []);
    assert.deepEqual(idb.created, [{ name: STORE_NAME, opts: { keyPath: "report_id" } }]);
    assert.equal(store.persistent, true);
    assert.equal(DB_NAME, "fbp-intake");
    assert.equal(DB_VERSION, 1);
  });

  it("round-trips entries and deletes", async () => {
    const store = new IndexedDbStore(new FakeIDB());
    await store.put(entry("a"));
    await store.put(entry("b"));
    await store.put(entry("a", { attempts: 3 }));
    const listed = await store.list();
    assert.deepEqual(listed.map((e) => [e.report_id, e.attempts]).sort(), [["a", 3], ["b", 0]]);
    await store.delete("a");
    assert.deepEqual((await store.list()).map((e) => e.report_id), ["b"]);
  });

  it("survives a reload: a new store on the same database sees what the old one saved", async () => {
    const idb = new FakeIDB();
    await new IndexedDbStore(idb).put(entry("a"));
    assert.deepEqual((await new IndexedDbStore(idb).list()).map((e) => e.report_id), ["a"]);
  });

  it("opens the database once for any number of operations", async () => {
    const idb = new FakeIDB();
    const store = new IndexedDbStore(idb);
    await Promise.all([store.put(entry("a")), store.list(), store.delete("a")]);
    assert.equal(idb.opens, 1);
  });

  it("resolves a write only when its transaction completes, not when the request succeeds", async () => {
    const idb = new FakeIDB({ deferComplete: true });
    const store = new IndexedDbStore(idb);
    let done = false;
    store.put(entry("a")).then(() => { done = true; });
    await flush();
    assert.equal(done, false);
    assert.equal(idb.pendingCompletes.length, 1);
    idb.completeAll();
    await flush();
    assert.equal(done, true);
  });

  it("rejects, and never hangs, when the transaction aborts or errors, or the database cannot be opened or is blocked", async () => {
    for (const failTx of [true, "error"]) {
      await assert.rejects(settles(new IndexedDbStore(new FakeIDB({ failTx })).put(entry("a"))), /transaction failed/, `failTx ${failTx}`);
    }
    await assert.rejects(settles(new IndexedDbStore(new FakeIDB({ failOpen: true })).list()), /open failed/);
    await assert.rejects(settles(new IndexedDbStore(new FakeIDB({ blocked: true })).list()), /blocked/);
  });

  it("tries to open again after a failed open", async () => {
    const idb = new FakeIDB({ failOpen: true });
    const store = new IndexedDbStore(idb);
    await assert.rejects(settles(store.list()), /open failed/);
    idb.failOpen = false;
    let rows;
    await assert.doesNotReject(async () => { rows = await settles(store.list()); });
    assert.deepEqual(rows, []);
    assert.equal(idb.opens, 2);
  });
});

describe("openStore", () => {
  it("gives IndexedDB when it opens", async () => {
    const store = await openStore(new FakeIDB());
    assert.ok(store instanceof IndexedDbStore);
    assert.equal(store.persistent, true);
  });

  it("falls back to memory, which says it is not persistent, when IndexedDB is missing, fails, is blocked or throws", async () => {
    const throwing = { open() { throw new Error("SecurityError"); } };
    for (const idb of [null, undefined, new FakeIDB({ failOpen: true }), new FakeIDB({ blocked: true }), throwing]) {
      let store;
      await assert.doesNotReject(async () => { store = await openStore(idb); });
      assert.ok(store instanceof MemoryStore);
      assert.equal(store.persistent, false);
    }
  });
});

describe("the reporter-facing texts", () => {
  it("has the one sentence for a report that gave up", () => {
    assert.equal(COULD_NOT_BE_ASSESSED, "This report could not be assessed.");
  });

  it("says what a dropped report was refused for, preferring the server's words only where they help", () => {
    assert.equal(messageForDropped(409, { message: "server" }), "That report number was already used for a different report, so this one was not sent.");
    assert.equal(messageForDropped(413, { message: "server" }), "That report is too long to send.");
    assert.equal(messageForDropped(422, { message: "  Try other words.  " }), "Try other words.");
    assert.equal(messageForDropped(422, { message: "   " }), "That report could not be accepted.");
    assert.equal(messageForDropped(422, { message: 5 }), "That report could not be accepted.");
    assert.equal(messageForDropped(422, null), "That report could not be accepted.");
    assert.equal(messageForDropped(403, { message: "Wrong address." }), "Wrong address.");
    assert.equal(messageForDropped(400, {}), "The report was refused (error 400).");
  });

  it("explains a 429: the server's reason, then what the page will do", () => {
    assert.equal(messageForRateLimited({ message: "Too many today." }, 90_000), "Too many today. It stays on this device and will be sent in 2 minutes.");
    assert.equal(messageForRateLimited(null, 30_000), "A daily limit has been reached. It stays on this device and will be sent in 30 seconds.");
    assert.equal(messageForRateLimited({ message: "" }, 30_000), "A daily limit has been reached. It stays on this device and will be sent in 30 seconds.");
  });

  it("explains a retry with the attempt number", () => {
    assert.equal(messageForRetry(2, 5, 30_000),
      "The report could not be sent yet. It is saved on this device and will be tried again in 30 seconds (attempt 2 of 5).");
  });

  it("formats waits", () => {
    const cases = [[0, "in 1 second"], [1000, "in 1 second"], [1499, "in 1 second"], [5000, "in 5 seconds"], [89_000, "in 89 seconds"],
      [90_000, "in 2 minutes"], [300_000, "in 5 minutes"], [89 * 60_000, "in 89 minutes"], [90 * 60_000, "in about 2 hours"],
      [24 * 3600_000, "in about 24 hours"]];
    for (const [ms, text] of cases) assert.equal(formatWait(ms), text, String(ms));
  });
});
