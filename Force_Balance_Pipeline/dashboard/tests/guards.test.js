// Parity and scan tests of the dashboard (doc 07 Phase 7): the region order against agent/distance.py, the banned calls in src/, and the
// built page against the backend's Content-Security-Policy. node --test, no browser.
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import { REGION_ORDER } from "../src/lib/regions.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const DASH = join(HERE, "..");
const SRC = join(DASH, "src");
const DIST = join(DASH, "dist");

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

const stripComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'])\/\/.*$/gm, "$1");

describe("region order", () => {
  it("equals the ring order of agent/distance.py", () => {
    const text = readFileSync(join(DASH, "..", "agent", "distance.py"), "utf-8");
    const block = /RINGS\s*=\s*\{([^}]*)\}/.exec(text)[1];
    const entries = [...block.matchAll(/"([^"]+)"\s*:\s*(\d+)/g)].map((m, i) => ({ region: m[1], ring: Number(m[2]), i }));
    const ordered = entries.sort((a, b) => a.ring - b.ring || a.i - b.i).map((e) => e.region);
    assert.equal(ordered.length, 8);
    assert.deepEqual(REGION_ORDER, ordered);
  });
});

describe("source scan", () => {
  const files = walk(SRC).filter((f) => /\.(js|jsx)$/.test(f));
  it("finds the sources", () => assert.ok(files.length >= 12));
  it("uses no unsafe HTML or code evaluation", () => {
    for (const file of files) {
      const code = stripComments(readFileSync(file, "utf-8"));
      for (const banned of ["dangerouslySetInnerHTML", "innerHTML", "eval(", "new Function"]) {
        assert.ok(!code.includes(banned), `${relative(DASH, file)} contains ${banned}`);
      }
    }
  });
  it("calls fetch( only in api.js", () => {
    for (const file of files) {
      const has = stripComments(readFileSync(file, "utf-8")).includes("fetch(");
      assert.equal(has, file.endsWith("api.js"), relative(DASH, file));
    }
  });
});

describe("built page", { skip: existsSync(join(DIST, "index.html")) ? false : "dist/ is missing: run npm run build" }, () => {
  const html = existsSync(join(DIST, "index.html")) ? readFileSync(join(DIST, "index.html"), "utf-8") : "";
  it("has no inline script or style", () => {
    const scripts = html.match(/<script\b[^>]*>/gi) || [];
    assert.ok(scripts.length >= 1);
    for (const tag of scripts) assert.match(tag, /\bsrc=/, tag);
    assert.ok(!/<script\b[^>]*>\s*\S/i.test(html.replace(/<script\b[^>]*src=[^>]*>\s*<\/script>/gi, "")), "an inline script body");
    assert.ok(!/<style\b/i.test(html));
  });
  it("references only assets that exist", () => {
    const refs = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);
    assert.ok(refs.length >= 2);
    for (const ref of refs) assert.ok(existsSync(join(DIST, ref.slice(1))), ref);
  });
});
