// The small pure modules: ULIDs, the browser identity, the request builder and the client-side rules.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { buildReport, getJson, loadRecent, loadSectors, validateDescription } from "../api.js";
import { REPORTER_KEY, REPORTER_RE, createIdentity, newReporterId } from "../identity.js";
import { MAX_DESCRIPTION_CHARS } from "../messages.js";
import { ULID_RE, newUlid } from "../ulid.js";
import { reply, scriptedFetch } from "./helpers.js";

const bytes = (list) => () => Uint8Array.from(list);

describe("newUlid", () => {
  // These vectors come from the Python implementation the server uses (intake_core.new_ulid).
  const vectors = [
    [1788000000123, Array(10).fill(255), "01M16HNP3VZZZZZZZZZZZZZZZZ"],
    [0, Array(10).fill(0), "00000000000000000000000000"],
    [1788000000000, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], "01M16HNP00000G40R40M30E209"],
    [2 ** 48 - 1, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "7ZZZZZZZZZ041061050R3GG28A"],
    [1700000000000, [222, 173, 190, 239, 0, 17, 34, 51, 68, 85], "01HF7YAT00VTPVXVR024H36H2N"],
  ];
  for (const [ms, raw, expected] of vectors) {
    it(`matches the server's encoding for ${ms}`, () => {
      assert.equal(newUlid(ms, bytes(raw)), expected);
    });
  }

  it("is 26 characters in the shape the server accepts", () => {
    for (let i = 0; i < 200; i++) assert.match(newUlid(1_800_000_000_000 + i), ULID_RE);
  });

  it("sorts by time", () => {
    const a = newUlid(1_800_000_000_000, bytes(Array(10).fill(255)));
    const b = newUlid(1_800_000_000_001, bytes(Array(10).fill(0)));
    assert.ok(a < b);
  });

  it("uses fresh randomness each time", () => {
    assert.notEqual(newUlid(1_800_000_000_000), newUlid(1_800_000_000_000));
  });

  it("refuses a time it cannot encode", () => {
    for (const bad of [-1, 2 ** 48, 1.5, NaN, "1700000000000", null]) assert.throws(() => newUlid(bad), RangeError, String(bad));
  });

  it("refuses a short source of randomness", () => {
    assert.throws(() => newUlid(1, () => new Uint8Array(9)), RangeError);
    assert.throws(() => newUlid(1, () => new Uint8Array(11)), RangeError);
  });

  it("draws its randomness from the platform's cryptographic source", () => {
    const calls = [];
    const original = globalThis.crypto.getRandomValues.bind(globalThis.crypto);
    globalThis.crypto.getRandomValues = (bytes) => { calls.push(bytes.length); return original(bytes); };
    try {
      newUlid(1_800_000_000_000);
    } finally {
      globalThis.crypto.getRandomValues = original;
    }
    assert.deepEqual(calls, [10]);
  });
});

describe("ULID_RE", () => {
  // The same samples the Python side checks against intake_core.is_ulid (test_server_web.ParityTests).
  const samples = [
    ["01KX0000000000000000000001", true], ["0".repeat(26), true], ["7".repeat(26), true], ["01KX" + "Z".repeat(22), true],
    ["8".repeat(26), false], ["9".repeat(26), false], ["0".repeat(25), false], ["0".repeat(27), false], ["0".repeat(25) + "I", false],
    ["0".repeat(25) + "L", false], ["0".repeat(25) + "O", false], ["0".repeat(25) + "U", false], ["0".repeat(25) + "a", false],
    ["0".repeat(25) + "\n", false], ["\n" + "0".repeat(25), false], ["0".repeat(26) + "\n", false], ["", false],
  ];
  for (const [text, ok] of samples) {
    it(`${ok ? "accepts" : "refuses"} ${JSON.stringify(text)}`, () => {
      assert.equal(ULID_RE.test(text), ok);
    });
  }
});

describe("the reporter identity", () => {
  const memoryStorage = (initial = {}) => {
    const data = { ...initial };
    return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, data };
  };

  it("makes a random 4-16 character [a-z0-9] id and keeps it", () => {
    const storage = memoryStorage();
    const identity = createIdentity({ getStorage: () => storage });
    const id = identity.reporterId();
    assert.match(id, REPORTER_RE);
    assert.equal(storage.data[REPORTER_KEY], id);
    assert.equal(identity.persistent(), true);
    assert.equal(createIdentity({ getStorage: () => storage }).reporterId(), id);
  });

  it("keeps the id under the key the checklist and the docs name", () => {
    assert.equal(REPORTER_KEY, "fbp.reporter_id");
  });

  it("reuses what is stored", () => {
    assert.equal(createIdentity({ getStorage: () => memoryStorage({ [REPORTER_KEY]: "abcd1234" }) }).reporterId(), "abcd1234");
  });

  it("replaces a stored value that the server would refuse", () => {
    for (const bad of ["ABC", "abc", "a".repeat(17), "ab-cd", "abcd\n", "", "a b c d"]) {
      const storage = memoryStorage({ [REPORTER_KEY]: bad });
      const id = createIdentity({ getStorage: () => storage }).reporterId();
      assert.match(id, REPORTER_RE, bad);
      assert.notEqual(id, bad);
      assert.equal(storage.data[REPORTER_KEY], id);
    }
  });

  it("works, with an id that lasts the session, when storage throws", () => {
    for (const make of [() => { throw new Error("SecurityError"); },
      () => ({ getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } }),
      () => ({ getItem: () => null, setItem() { throw new Error("QuotaExceededError"); } }), () => null]) {
      const identity = createIdentity({ getStorage: make });
      let id;
      assert.doesNotThrow(() => { id = identity.reporterId(); });
      assert.match(id, REPORTER_RE);
      assert.equal(identity.reporterId(), id);
      assert.equal(identity.persistent(), false);
    }
  });

  it("draws every character with the same chance", () => {
    const counts = new Map();
    let state = 7; // mulberry32: a small, well-mixed generator, so the test is repeatable
    const next = () => {
      state = (state + 0x6d2b79f5) | 0;
      let t = Math.imul(state ^ (state >>> 15), 1 | state);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) & 255;
    };
    const source = (n) => Uint8Array.from({ length: n }, next);
    for (let i = 0; i < 4000; i++) for (const ch of newReporterId(source)) counts.set(ch, (counts.get(ch) || 0) + 1);
    assert.equal(counts.size, 36);
    const values = [...counts.values()];
    assert.ok(Math.max(...values) / Math.min(...values) < 1.25, `uneven: ${Math.min(...values)}..${Math.max(...values)}`);
  });

  it("does not use bytes that would favour some characters", () => {
    const id = newReporterId(() => Uint8Array.from([252, 253, 254, 255, ...Array(20).fill(0)]));
    assert.equal(id, "a".repeat(12));
  });
});

describe("validateDescription", () => {
  it("accepts ordinary text, newlines and tabs, and does not change it", () => {
    for (const ok of ["It rained today", "line one\nline two\r\n\tindented", "  padded  ", "caf\u00e9 \u2014 \u{1F311}", "<script>alert(1)</script>"]) {
      assert.equal(validateDescription(ok), null, JSON.stringify(ok));
    }
  });

  it("refuses empty and blank text", () => {
    for (const bad of ["", "   ", "\n\t \r\n", null, undefined, 5]) assert.equal(validateDescription(bad)?.code, "empty", String(bad));
  });

  it("counts characters the way the server does, not UTF-16 units", () => {
    assert.equal(MAX_DESCRIPTION_CHARS, 8000);
    assert.equal(validateDescription("a".repeat(8000)), null);
    assert.equal(validateDescription("a".repeat(8001))?.code, "too_long");
    assert.equal(validateDescription("\u{1F311}".repeat(8000)), null);
    assert.equal(validateDescription("\u{1F311}".repeat(8001))?.code, "too_long");
  });

  it("refuses control characters but not tab, newline or return", () => {
    for (const code of [0x00, 0x07, 0x1b, 0x7f, 0x85, 0x9f]) {
      assert.equal(validateDescription(`a${String.fromCharCode(code)}b`)?.code, "invalid_characters", code.toString(16));
    }
    for (const code of [0x09, 0x0a, 0x0d, 0xa0, 0xa1]) assert.equal(validateDescription(`a${String.fromCharCode(code)}b`), null, code.toString(16));
  });
});

describe("buildReport", () => {
  const input = { sectorId: "coruscant", description: "Saw a figure.\nCold \u2014 \u{1F311}", reporterId: "a3f2b7c9d1e4", nowMs: 1_800_000_000_123,
    newUlid: (ms) => newUlid(ms, bytes(Array(10).fill(1))) };

  it("fixes the whole request body at submit time", () => {
    const report = buildReport(input);
    assert.equal(typeof report.body, "string");
    assert.deepEqual(JSON.parse(report.body), {
      sector_id: "coruscant", description: input.description, report_id: report.report_id, reporter_id: "a3f2b7c9d1e4",
      observed_at: "2027-01-15T08:00:00.123Z" });
    assert.equal(report.sector_id, "coruscant");
  });

  it("makes the report id from the submit time", () => {
    const report = buildReport(input);
    assert.equal(report.report_id, newUlid(1_800_000_000_123, bytes(Array(10).fill(1))));
    assert.match(report.report_id, ULID_RE);
  });

  it("puts the description in verbatim, whatever it contains", () => {
    for (const text of ['quote " and \\ backslash', "<img src=x onerror=alert(1)>", "  padded  ", "\u202e\u2028 odd", "\u{1F311}"]) {
      assert.equal(JSON.parse(buildReport({ ...input, description: text }).body).description, text);
    }
  });

  it("uses an observed_at the server's window accepts (ISO, UTC, milliseconds)", () => {
    assert.match(JSON.parse(buildReport(input).body).observed_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  });
});

describe("getJson, loadSectors, loadRecent", () => {
  it("returns the document on a 200", async () => {
    const fetchFn = scriptedFetch([reply(200, [{ sector_id: "a" }])]);
    assert.deepEqual(await loadSectors(fetchFn), { ok: true, status: 200, body: [{ sector_id: "a" }] });
    assert.equal(fetchFn.calls[0].url, "/api/sectors");
    assert.equal(fetchFn.calls[0].init.cache, "no-store");
  });

  it("is not ok on any other status, on a network error, or on a body that is not JSON", async () => {
    assert.equal((await getJson(scriptedFetch([reply(404, { error: "x" })]), "/x")).ok, false);
    let offline;
    await assert.doesNotReject(async () => { offline = await getJson(scriptedFetch([{ throws: new Error("offline") }]), "/x"); });
    assert.deepEqual(offline, { ok: false, status: 0, body: null });
    assert.equal((await getJson(scriptedFetch([reply(200, "BAD")]), "/x")).ok, false);
  });

  it("asks for one planet's reports with the id encoded", async () => {
    const fetchFn = scriptedFetch([reply(200, [])]);
    await loadRecent(fetchFn, "a b&c=d");
    assert.equal(fetchFn.calls[0].url, "/api/reports?sector_id=a%20b%26c%3Dd");
  });
});
