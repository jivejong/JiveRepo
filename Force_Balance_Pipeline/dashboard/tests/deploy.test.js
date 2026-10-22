// Tests of the deploy form's pure rules (doc 06 "Manual deployment path"): validation, the request shape, the reason labels against the
// Python sources, and the agent's decision time. node --test, no browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import { KNOWN_CODES, buildRequest, isSelectable, nextAgentDecision, reasonLabel, validate } from "../src/lib/deploy.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const good = { disturbanceId: "FIXD4", jedi: ["windu"], starship: "arc_170", rationale: "Because." };

describe("validate", () => {
  it("accepts a good form", () => assert.deepEqual(validate(good), []));
  it("needs 1 to 4 distinct Jedi", () => {
    assert.ok(validate({ ...good, jedi: [] }).length);
    assert.ok(validate({ ...good, jedi: ["a", "b", "c", "d", "e"] }).length);
    assert.ok(validate({ ...good, jedi: ["a", "a"] }).length);
    assert.deepEqual(validate({ ...good, jedi: ["a", "b", "c", "d"] }), []);
  });
  it("needs a starship and a slug-shaped id", () => {
    assert.ok(validate({ ...good, starship: "" }).length);
    assert.ok(validate({ ...good, starship: "Arc 170" }).length);
    assert.ok(validate({ ...good, jedi: ["Windu"] }).length);
  });
  it("needs a valid incident id", () => {
    assert.ok(validate({ ...good, disturbanceId: "" }).length);
    assert.ok(validate({ ...good, disturbanceId: "a b" }).length);
  });
  it("needs a rationale of 1 to 2000 characters after trimming", () => {
    assert.ok(validate({ ...good, rationale: "   " }).length);
    assert.deepEqual(validate({ ...good, rationale: " ".concat("x".repeat(2000), " ") }), []);
    assert.ok(validate({ ...good, rationale: "x".repeat(2001) }).length);
  });
});

describe("buildRequest", () => {
  it("has exactly the four keys, with the rationale trimmed", () => {
    const body = buildRequest({ ...good, rationale: "  Because.  " });
    assert.deepEqual(Object.keys(body).sort(), ["disturbance_id", "jedi_ids", "rationale", "starship_id"]);
    assert.deepEqual(body, { disturbance_id: "FIXD4", jedi_ids: ["windu"], starship_id: "arc_170", rationale: "Because." });
  });
});

describe("reasonLabel", () => {
  const constraints = readFileSync(join(ROOT, "agent", "constraints.py"), "utf-8");
  const deploySrc = readFileSync(join(ROOT, "serving", "deploy.py"), "utf-8");
  const fromReasons = [.../REASONS\s*=\s*\(([^)]*)\)/.exec(constraints)[1].matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
  const fromDeploy = [
    ...[...deploySrc.matchAll(/\["reasons"\] = \["([a-z0-9_]+)"\]/g)].map((m) => m[1]),
    ...[...deploySrc.matchAll(/reasons=\["([a-z0-9_]+)"\]/g)].map((m) => m[1]),
    ...[...deploySrc.matchAll(/append\("([a-z0-9_]+)"\)/g)].map((m) => m[1]),
    ...[...deploySrc.matchAll(/return None, "([a-z0-9_]+)"/g)].map((m) => m[1]),
  ];
  it("finds the codes in the sources", () => {
    assert.equal(fromReasons.length, 17);
    for (const c of ["quota_hour", "not_open", "unknown_incident", "agent_cycle_in_progress", "already_decided", "internal_error", "invalid_request",
      "mark_failed", "refresh_failed", "merge_result_unverified"]) assert.ok(fromDeploy.includes(c), c);
  });
  it("has a label, not the code, for every constraint and service code", () => {
    for (const code of [...fromReasons, ...fromDeploy]) {
      assert.notEqual(reasonLabel(code), code, code);
      assert.ok(KNOWN_CODES.includes(code), code);
    }
  });
  it("labels the Job 1 reasons of agent/jobs.py", () => {
    const jobs = readFileSync(join(ROOT, "agent", "jobs.py"), "utf-8");
    for (const code of [...jobs.matchAll(/"(job1_running|jobs_api_error)"/g)].map((m) => m[1])) assert.notEqual(reasonLabel(code), code);
    assert.notEqual(reasonLabel("jedi_unavailable"), "jedi_unavailable");
  });
  it("shows an unknown code as itself", () => {
    assert.equal(reasonLabel("brand_new_code"), "brand_new_code");
    assert.equal(reasonLabel("constructor"), "constructor");
  });
});

describe("nextAgentDecision", () => {
  const at = (s) => Date.parse(s);
  const early = at("2026-10-20T00:00:00Z");                   // a clock before every first-seen time below: no stepping
  it("is the first :11 strictly after the first-seen time", () => {
    assert.equal(nextAgentDecision("2026-10-21T02:10:00Z", early), "2026-10-21T02:11:00.000Z");
    assert.equal(nextAgentDecision("2026-10-21T02:11:00Z", early), "2026-10-21T03:11:00.000Z");
    assert.equal(nextAgentDecision("2026-10-21T02:11:01Z", early), "2026-10-21T03:11:00.000Z");
    assert.equal(nextAgentDecision("2026-10-21T23:30:00Z", early), "2026-10-22T00:11:00.000Z");
  });
  it("steps forward an hour at a time when that time has passed", () => {
    assert.equal(nextAgentDecision("2026-10-21T02:10:00Z", at("2026-10-21T05:30:00Z")), "2026-10-21T06:11:00.000Z");
    assert.equal(nextAgentDecision("2026-10-21T02:10:00Z", at("2026-10-21T04:11:00Z")), "2026-10-21T05:11:00.000Z");
    assert.equal(nextAgentDecision("2026-10-21T02:10:00Z", at("2026-10-21T02:10:59Z")), "2026-10-21T02:11:00.000Z");
  });
  it("is null when the incident has not been seen or the input is bad", () => {
    assert.equal(nextAgentDecision(null, 0), null);
    assert.equal(nextAgentDecision("nonsense", 0), null);
  });
});

describe("selectable", () => {
  it("keeps an unavailable Jedi selectable", () => {
    assert.equal(isSelectable({ jedi_id: "windu", unavailable_until: "2026-10-22T00:00:00Z" }), true);
    assert.equal(isSelectable({ jedi_id: "yoda", unavailable_until: null }), true);
  });
});
