// Tests of the pure modules in dashboard/src/lib (doc 07 Phase 7, panels and display rules). node --test, no browser.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { BAND_CLASS, bandClass, bandLabel } from "../src/lib/bands.js";
import { formatAgo, formatNumber, formatUtc } from "../src/lib/format.js";
import { completenessStrip, summary } from "../src/lib/health.js";
import { channelSeries } from "../src/lib/planet.js";
import { MAX_DELAY_MS, POLL_MS, nextDelay } from "../src/lib/poll.js";
import { REGION_ORDER, groupByRegion } from "../src/lib/regions.js";

describe("format", () => {
  it("formatUtc", () => {
    assert.equal(formatUtc("2026-10-19T04:05:59.000Z"), "2026-10-19 04:05 UTC");
    assert.equal(formatUtc("2026-10-19T04:05:00+02:00"), "2026-10-19 02:05 UTC");
    for (const bad of [null, undefined, "", "nope", 5]) assert.equal(formatUtc(bad), "-");
  });
  it("formatAgo boundaries", () => {
    const now = Date.parse("2026-10-19T12:00:00Z");
    const ago = (s) => formatAgo(now, new Date(now - s * 1000).toISOString());
    assert.equal(ago(0), "just now");
    assert.equal(ago(59), "just now");
    assert.equal(ago(60), "1 min ago");
    assert.equal(ago(59 * 60 + 59), "59 min ago");
    assert.equal(ago(3600), "1 h ago");
    assert.equal(ago(47 * 3600 + 3599), "47 h ago");
    assert.equal(ago(48 * 3600), "2 d ago");
    assert.equal(ago(-30), "just now");
    assert.equal(formatAgo(now, null), "-");
    assert.equal(formatAgo(now, "bad"), "-");
  });
  it("formatNumber", () => {
    assert.equal(formatNumber(1.236, 2), "1.24");
    assert.equal(formatNumber(2, 0), "2");
    for (const bad of [null, undefined, NaN, "x"]) assert.equal(formatNumber(bad, 2), "-");
  });
});

describe("regions", () => {
  it("the order is the ring order", () => {
    assert.deepEqual(REGION_ORDER.slice(0, 3), ["Core Worlds", "Colonies", "Inner Rim"]);
  });
  it("groups in order, keeps the server order and puts unknown regions last under Other", () => {
    const rows = [
      { sector_id: "z", region: "Outer Rim" }, { sector_id: "a", region: "Core Worlds" }, { sector_id: "m", region: "Outer Rim" },
      { sector_id: "q", region: "Atlantis" }, { sector_id: "n", region: null },
    ];
    const groups = groupByRegion(rows);
    assert.deepEqual(groups.map((g) => g.region), ["Core Worlds", "Outer Rim", "Other"]);
    assert.deepEqual(groups[1].planets.map((p) => p.sector_id), ["z", "m"]);
    assert.deepEqual(groups[2].planets.map((p) => p.sector_id), ["q", "n"]);
    assert.deepEqual(groupByRegion([]), []);
  });
});

describe("bands", () => {
  it("every band has a class and a label, and an unknown band is no data", () => {
    for (const band of ["normal", "anomaly", "emergency", "no_data"]) {
      assert.ok(BAND_CLASS[band]);
      assert.equal(bandClass(band), BAND_CLASS[band]);
      assert.ok(bandLabel(band));
    }
    assert.equal(new Set(Object.values(BAND_CLASS)).size, 4);
    assert.equal(bandClass("mystery"), BAND_CLASS.no_data);
  });
});

describe("planet", () => {
  const readings = [
    { event_time: "t1", midichlorian_ppm: 100, kyber_resonance: 10, dark_side_activity: 1, z_midi: 0.1, z_kyber: 0.2, z_dark: 0.3, is_replayed: false },
    { event_time: "t2", midichlorian_ppm: 110, kyber_resonance: 11, dark_side_activity: 2, z_midi: 1.1, z_kyber: 1.2, z_dark: 1.3, is_replayed: true },
  ];
  const bands = [
    { channel: "kyber", mean_90d: 10, lower_1: 9, upper_1: 11, lower_2: 8, upper_2: 12 },
    { channel: "dark_side", mean_90d: 1, lower_1: 0, upper_1: 2, lower_2: -1, upper_2: 3 },
    { channel: "midichlorian", mean_90d: 100, lower_1: 90, upper_1: 110, lower_2: 80, upper_2: 120 },
  ];
  it("maps each reading column to its channel and band row", () => {
    const [midi, kyber, dark] = channelSeries(readings, bands);
    assert.deepEqual([midi.channel, kyber.channel, dark.channel], ["midichlorian", "kyber", "dark_side"]);
    assert.deepEqual(midi.points, [{ t: "t1", value: 100, z: 0.1, replayed: false }, { t: "t2", value: 110, z: 1.1, replayed: true }]);
    assert.deepEqual(kyber.points.map((p) => p.value), [10, 11]);
    assert.deepEqual(dark.points.map((p) => p.z), [0.3, 1.3]);
    assert.deepEqual(midi.bands, { mean: 100, lower1: 90, upper1: 110, lower2: 80, upper2: 120 });
    assert.deepEqual(kyber.bands, { mean: 10, lower1: 9, upper1: 11, lower2: 8, upper2: 12 });
    assert.equal(dark.bands.upper2, 3);
  });
  it("a missing bands row gives nulls", () => {
    const [midi] = channelSeries(readings, [bands[0]]);
    assert.deepEqual(midi.bands, { mean: null, lower1: null, upper1: null, lower2: null, upper2: null });
    assert.equal(channelSeries([], null)[0].points.length, 0);
  });
});

describe("health", () => {
  it("the strip is ascending and flags incomplete and disconnected scans", () => {
    const strip = completenessStrip([
      { scan_id: "b", scan_event_time: "2026-10-19T00:15:00Z", complete: false, any_disconnected: true },
      { scan_id: "a", scan_event_time: "2026-10-19T00:00:00Z", complete: true, any_disconnected: false },
    ]);
    assert.deepEqual(strip.map((s) => s.scan_id), ["a", "b"]);
    assert.deepEqual(strip[1], { scan_id: "b", t: "2026-10-19T00:15:00Z", complete: false, disconnected: true });
  });
  it("the summary always says n/a for the buffer depth", () => {
    const s = summary({ sources: [{ current_mode: "CONNECTED", last_seen_at: "x", last_scan_complete: true }] });
    assert.deepEqual(s, { mode: "CONNECTED", lastSeen: "x", lastScanComplete: true, bufferDepth: "n/a" });
    assert.equal(summary(null).bufferDepth, "n/a");
    assert.equal(summary({ sources: [] }).mode, null);
  });
});

describe("poll", () => {
  it("backs off and caps", () => {
    assert.equal(POLL_MS, 60000);
    assert.deepEqual([0, 1, 2, 3].map(nextDelay), [60000, 120000, 240000, 480000]);
    assert.equal(nextDelay(4), MAX_DELAY_MS);
    assert.equal(nextDelay(50), 600000);
    assert.equal(nextDelay(-1), 60000);
    assert.equal(nextDelay("x"), 60000);
  });
});
