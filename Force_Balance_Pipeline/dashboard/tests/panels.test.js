// Tests of the pure modules behind the last four panels (doc 07 Phase 7, panels 5 to 8 and the display rules). node --test, no browser.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { countdown } from "../src/lib/countdown.js";
import { bars } from "../src/lib/histogram.js";
import { badgesFor, truncate } from "../src/lib/incidents.js";
import { summarizeOverrides } from "../src/lib/overrides.js";
import { pivotByReason, reasons } from "../src/lib/rejections.js";

describe("countdown", () => {
  const now = Date.parse("2026-10-19T12:00:00Z");
  const at = (seconds) => new Date(now + seconds * 1000).toISOString();
  it("is done at exactly now and in the past", () => {
    assert.equal(countdown(now, at(0)), "done");
    assert.equal(countdown(now, at(-1)), "done");
    assert.equal(countdown(now, at(-86400)), "done");
  });
  it("counts minutes under an hour, rounding up", () => {
    assert.equal(countdown(now, at(1)), "in 1 min");
    assert.equal(countdown(now, at(59)), "in 1 min");
    assert.equal(countdown(now, at(60)), "in 1 min");
    assert.equal(countdown(now, at(61)), "in 2 min");
    assert.equal(countdown(now, at(59 * 60)), "in 59 min");
  });
  it("counts hours and minutes under 48 h, and days beyond", () => {
    assert.equal(countdown(now, at(61 * 60)), "in 1 h 1 min");
    assert.equal(countdown(now, at(60 * 60)), "in 1 h 0 min");
    assert.equal(countdown(now, at(47 * 3600)), "in 47 h 0 min");
    assert.equal(countdown(now, at(49 * 3600)), "in 2 d");
    assert.equal(countdown(now, at(48 * 3600)), "in 2 d");
  });
  it("gives - for null or invalid input", () => {
    for (const bad of [null, undefined, "", "nope", 5]) assert.equal(countdown(now, bad), "-");
  });
});

describe("overrides", () => {
  it("reads the agent's shape", () => {
    const value = [{ proposal: { jedi_ids: ["obi_wan_kenobi", "yoda"], starship_id: "naboo_fighter", rationale: "r" }, reasons: ["deployment_capacity_reached", "jedi_unavailable"] }];
    assert.deepEqual(summarizeOverrides(value), [{ reasons: ["deployment_capacity_reached", "jedi_unavailable"], proposalJedi: ["obi_wan_kenobi", "yoda"], proposalShip: "naboo_fighter" }]);
  });
  it("reads a forced stand-down (no proposal)", () => {
    assert.deepEqual(summarizeOverrides([{ proposal: null, reasons: ["severity_below_threshold"] }]),
      [{ reasons: ["severity_below_threshold"], proposalJedi: [], proposalShip: null }]);
  });
  it("never throws on odd input", () => {
    for (const odd of [null, undefined, "text", 5, {}, [null], [[]]]) assert.doesNotThrow(() => summarizeOverrides(odd), String(odd));
    assert.deepEqual(summarizeOverrides(null), []);
    assert.deepEqual(summarizeOverrides("text"), []);
    assert.deepEqual(summarizeOverrides({ reasons: ["x"] }), []);
    assert.deepEqual(summarizeOverrides([null, 5, {}, { reasons: "x", proposal: "y" }, { proposal: { jedi_ids: "a", starship_id: 3 } }]),
      Array(5).fill({ reasons: [], proposalJedi: [], proposalShip: null }));
  });
});

describe("incident badges", () => {
  it("lists them in the fixed order", () => {
    const all = { is_report_sourced: true, status: "decided", severity_rose_after_decision: true, cooldown_conflict: true };
    assert.deepEqual(badgesFor(all), ["report", "decided", "severity rose", "cooldown conflict"]);
  });
  it("shows each flag only when set", () => {
    assert.deepEqual(badgesFor({ status: "open" }), ["open"]);
    assert.deepEqual(badgesFor({ is_report_sourced: true, status: "historical" }), ["report", "historical"]);
    assert.deepEqual(badgesFor({ status: "decided", severity_rose_after_decision: true }), ["decided", "severity rose"]);
    assert.deepEqual(badgesFor({ status: "decided", cooldown_conflict: true }), ["decided", "cooldown conflict"]);
    assert.deepEqual(badgesFor({}), []);
  });
  it("truncates long text to 200 characters with an ellipsis, leaves the rest", () => {
    assert.equal(truncate("a".repeat(200), 200), "a".repeat(200));
    assert.equal(truncate("a".repeat(201), 200), "a".repeat(200) + "...");
    assert.equal(truncate(null), "");
    assert.equal(truncate("<b>x</b>"), "<b>x</b>");
  });
});

describe("histogram bars", () => {
  const rows = Array.from({ length: 10 }, (_, i) => ({ bin_order: 10 - i, label: `b${10 - i}`, rows: (10 - i) * 10, replayed_rows: 10 - i, buffered_rows: 1 }));
  it("returns the ten bins in order with normal = rows - replayed", () => {
    const out = bars(rows);
    assert.deepEqual(out.map((b) => b.label), ["b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9", "b10"]);
    assert.deepEqual(out[2], { label: "b3", normal: 27, replayed: 3, buffered: 1 });
    assert.equal(out[9].normal, 90);
  });
  it("throws if a bin is missing", () => {
    assert.throws(() => bars(rows.filter((r) => r.bin_order !== 4)), /bin 4/);
    assert.throws(() => bars([]), /missing/);
    assert.throws(() => bars(null), /missing/);
  });
});

describe("rejections", () => {
  const byReason = [
    { hour: "2026-10-19T02:00:00Z", reject_reason: "out_of_range", rejects: 3 },
    { hour: "2026-10-19T01:00:00Z", reject_reason: "unknown_sector", rejects: 1 },
    { hour: "2026-10-19T02:00:00Z", reject_reason: "null_required_field", rejects: 2 },
  ];
  it("sorts the distinct reasons", () => {
    assert.deepEqual(reasons(byReason), ["null_required_field", "out_of_range", "unknown_sector"]);
    assert.deepEqual(reasons([]), []);
  });
  it("pivots to one row per hour, ascending, with 0 where a reason is absent", () => {
    assert.deepEqual(pivotByReason(byReason), [
      { hour: "2026-10-19T01:00:00Z", null_required_field: 0, out_of_range: 0, unknown_sector: 1 },
      { hour: "2026-10-19T02:00:00Z", null_required_field: 2, out_of_range: 3, unknown_sector: 0 },
    ]);
    assert.deepEqual(pivotByReason([]), []);
  });
});
