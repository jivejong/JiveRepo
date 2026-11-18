// The built bundle's size and shape (doc 07 Phase 8, finding 22): no emitted JavaScript chunk reaches 500 kB (the chart panels are loaded
// on demand, so recharts is not in the entry chunk), and the built page has no inline script or style (the backend's Content-Security-Policy
// is script-src 'self', style-src 'self'). node --test on dist/, no browser.
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const DIST = join(dirname(fileURLToPath(import.meta.url)), "..", "dist");
const MAX_CHUNK_BYTES = 500000;

describe("built bundle", { skip: existsSync(join(DIST, "index.html")) ? false : "dist/ is missing: run npm run build" }, () => {
  it("emits no JavaScript chunk of 500,000 bytes or more", () => {
    const assets = join(DIST, "assets");
    const chunks = readdirSync(assets).filter((name) => name.endsWith(".js"));
    assert.ok(chunks.length >= 2, "expected the entry chunk and at least one lazy chunk");
    for (const name of chunks) {
      const size = statSync(join(assets, name)).size;
      assert.ok(size < MAX_CHUNK_BYTES, `${name} is ${size} bytes`);
    }
  });
  it("has no inline script and no inline style in index.html", () => {
    const html = readFileSync(join(DIST, "index.html"), "utf-8");
    const scripts = html.match(/<script\b[^>]*>[\s\S]*?<\/script>/gi) || [];
    assert.ok(scripts.length >= 1);
    for (const tag of scripts) {
      assert.match(tag, /^<script\b[^>]*\bsrc="[^"]+"/i, "a script without a src");
      assert.match(tag, /<\/script>$/i);
      assert.equal(tag.replace(/^<script\b[^>]*>/i, "").replace(/<\/script>$/i, "").trim(), "", "an inline script body");
    }
    assert.ok(!/<style\b/i.test(html), "an inline style element");
    assert.ok(!/\sstyle\s*=/i.test(html), "an inline style attribute");
  });
});
