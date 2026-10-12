// Scans of the page's own files. The server's Content-Security-Policy would stop most of what these look for; the scans make sure the
// code never relies on that. Comments are stripped first (a comment may name a banned call to say it is banned).
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import { MAX_DESCRIPTION_CHARS } from "../messages.js";
import { PAGE_IDS } from "./helpers.js";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..");
const SERVED_NAME = /^[a-z0-9_-]+\.(js|css|html)$/; // what the server will serve (intake_server.STATIC_NAME_RE)
const read = (name) => readFileSync(join(WEB, name), "utf8");
const files = readdirSync(WEB, { withFileTypes: true }).filter((e) => e.isFile()).map((e) => e.name);
const served = files.filter((n) => SERVED_NAME.test(n));
const scripts = served.filter((n) => n.endsWith(".js"));

function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").split("\n").map((line) => line.replace(/(^|\s)\/\/.*$/, "$1")).join("\n");
}

describe("which files the page is made of", () => {
  it("is index.html, style.css and the modules; nothing else is meant to be served", () => {
    assert.deepEqual(served.sort(), ["api.js", "app.js", "identity.js", "index.html", "main.js", "messages.js", "queue.js", "render.js", "store.js", "style.css",
      "ulid.js"]);
    assert.deepEqual(files.filter((n) => !SERVED_NAME.test(n)).sort(), ["package.json"]);
  });

  it("is plain ASCII (anything else is written as an escape)", () => {
    for (const name of served) assert.doesNotMatch(read(name), /[^\x00-\x7f]/, name);
  });

  it("is ES modules without a build step", () => {
    assert.equal(JSON.parse(read("package.json")).type, "module");
  });
});

describe("the scripts", () => {
  const banned = [
    [/\.innerHTML\b/, "innerHTML"], [/\.outerHTML\b/, "outerHTML"], [/insertAdjacentHTML/, "insertAdjacentHTML"], [/document\s*\.\s*write/, "document.write"],
    [/\beval\s*\(/, "eval"], [/\bnew\s+Function\b/, "new Function"], [/\bFunction\s*\(/, "Function()"], [/set(Timeout|Interval)\s*\(\s*["'`]/, "a string timer"],
    [/javascript:/i, "a javascript: URL"], [/https?:\/\//i, "an absolute URL"], [/\bimport\s*\(/, "a dynamic import"], [/\bMath\.random\b/, "Math.random"],
    [/\bXMLHttpRequest\b/, "XMLHttpRequest"], [/\bWebSocket\b/, "WebSocket"], [/\bsendBeacon\b/, "sendBeacon"], [/\bimportScripts\b/, "importScripts"],
    [/\bdocument\s*\.\s*cookie\b/, "document.cookie"], [/\bconsole\s*\./, "console"],
  ];
  for (const name of scripts) {
    it(`${name} uses none of the banned calls`, () => {
      const code = stripComments(read(name));
      for (const [pattern, what] of banned) assert.doesNotMatch(code, pattern, `${name} uses ${what}`);
    });
  }

  it("only main.js touches the real browser objects", () => {
    for (const name of scripts.filter((n) => n !== "main.js")) {
      const code = stripComments(read(name));
      for (const pattern of [/\bwindow\b/, /\bnavigator\b/, /\blocalStorage\b/, /\bsessionStorage\b/, /\bDate\.now\b/, /(^|[^.\w])fetch\s*\(/, /(^|[^.\w])document\./,
        /(^|[^.\w])setTimeout\s*\(/, /(^|[^.\w])clearTimeout\s*\(/]) {
        assert.doesNotMatch(code, pattern, `${name} reaches for ${pattern}`);
      }
    }
  });

  it("imports only its own files, by relative path, and each exists", () => {
    for (const name of scripts) {
      const code = stripComments(read(name));
      for (const match of code.matchAll(/\bfrom\s+(["'])(.*?)\1/g)) {
        const target = match[2];
        assert.match(target, /^\.\/[a-z0-9_-]+\.js$/, `${name} imports ${target}`);
        assert.ok(served.includes(target.slice(2)), `${name} imports ${target}, which does not exist or is not served`);
      }
      assert.doesNotMatch(code, /\brequire\s*\(/, name);
    }
  });

  it("makes ids from the browser's random source only", () => {
    assert.match(stripComments(read("ulid.js")), /crypto\.getRandomValues/);
  });
});

describe("index.html", () => {
  const html = read("index.html");

  it("has one script, a module loaded from the server's own /static path, and no inline code", () => {
    const tags = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
    assert.equal(tags.length, 1);
    assert.match(tags[0][1], /^\s*type="module"\s+src="\/static\/main\.js"\s*$/);
    assert.equal(tags[0][2], "");
    assert.doesNotMatch(html, /<script\b(?![^>]*\bsrc=)/i);
  });

  it("has no inline style, event handler attribute, or embedded content", () => {
    assert.doesNotMatch(html, /<style\b/i);
    assert.doesNotMatch(html, /\sstyle\s*=/i);
    assert.doesNotMatch(html, /\son[a-z]+\s*=/i);
    assert.doesNotMatch(html, /<(iframe|object|embed|base|frame|applet)\b/i);
    assert.doesNotMatch(html, /javascript:/i);
    assert.doesNotMatch(html, /https?:\/\//i);
    assert.doesNotMatch(html, /<form\b[^>]*\saction\s*=/i);
  });

  it("takes everything it loads from /static/ and each file exists", () => {
    const refs = [...html.matchAll(/\b(?:src|href)="([^"]*)"/g)].map((m) => m[1]);
    assert.ok(refs.length >= 2);
    for (const ref of refs) {
      assert.match(ref, /^\/static\/[a-z0-9_-]+\.(js|css)$/, ref);
      assert.ok(served.includes(ref.slice("/static/".length)), ref);
    }
  });

  it("has every element the page's code looks for, once", () => {
    for (const id of PAGE_IDS) assert.equal([...html.matchAll(new RegExp(`\\bid="${id}"`, "g"))].length, 1, id);
    const wanted = new Set(PAGE_IDS);
    for (const m of html.matchAll(/\bid="([^"]+)"/g)) assert.ok(wanted.has(m[1]), `unexpected id ${m[1]}`);
  });

  it("limits the box to the same length the server does", () => {
    assert.match(html, new RegExp(`<textarea\\b[^>]*\\bmaxlength="${MAX_DESCRIPTION_CHARS}"`));
    assert.match(html, new RegExp(`>0 / ${MAX_DESCRIPTION_CHARS}<`));
  });

  it("declares its language, character set and viewport", () => {
    assert.match(html, /^<!doctype html>/i);
    assert.match(html, /<html lang="en">/);
    assert.match(html, /<meta charset="utf-8">/);
    assert.match(html, /<meta name="viewport"/);
  });
});

describe("style.css", () => {
  it("pulls in nothing from elsewhere", () => {
    const css = stripComments(read("style.css"));
    assert.doesNotMatch(css, /@import/i);
    assert.doesNotMatch(css, /url\s*\(/i);
    assert.doesNotMatch(css, /expression\s*\(/i);
    assert.doesNotMatch(css, /https?:\/\//i);
  });
});
