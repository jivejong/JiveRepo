// Drawing the page: hostile text stays text. FakeElement throws on innerHTML/outerHTML/insertAdjacentHTML, so any use fails loudly.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { renderConfirmation, renderQueue, renderRecent, renderSectors } from "../render.js";
import { FakeDocument } from "./helpers.js";

const HOSTILE = ['<img src=x onerror=alert(1)>', '<script>alert(1)</script>', '"><svg onload=alert(1)>', "&lt;b&gt;", "javascript:alert(1)"];
const tags = (root) => root.find(() => true).map((e) => e.tagName);

function fresh() {
  const doc = new FakeDocument();
  return { doc, el: (tag) => doc.createElement(tag) };
}

describe("renderSectors", () => {
  it("offers a blank first choice, then one option per planet with its id as the value", () => {
    const { doc, el } = fresh();
    const select = el("select");
    renderSectors(doc, select, [{ sector_id: "coruscant", sector_name: "Coruscant" }, { sector_id: "tatooine", sector_name: "Tatooine" }]);
    assert.deepEqual(select.children.map((o) => [o.value, o.textContent]),
      [["", "Choose a planet"], ["coruscant", "Coruscant"], ["tatooine", "Tatooine"]]);
  });

  it("replaces what was there, and keeps hostile names as text", () => {
    const { doc, el } = fresh();
    const select = el("select");
    renderSectors(doc, select, [{ sector_id: "a", sector_name: "old" }]);
    renderSectors(doc, select, HOSTILE.map((h, i) => ({ sector_id: `s${i}`, sector_name: h })));
    assert.equal(select.children.length, 1 + HOSTILE.length);
    assert.deepEqual(select.children.slice(1).map((o) => o.textContent), HOSTILE);
    assert.deepEqual(tags(select).filter((t) => !["SELECT", "OPTION"].includes(t)), []);
  });
});

describe("renderConfirmation", () => {
  const response = { relevance_score: 0.95, midichlorian_ppm: 26400, kyber_resonance: 30, dark_side_activity: 85.04, inference_rationale: "A named figure." };

  it("shows the inferred values, the relevance and the reason, and reveals the section", () => {
    const { doc, el } = fresh();
    const box = el("section");
    box.hidden = true;
    renderConfirmation(doc, box, response);
    assert.equal(box.hidden, false);
    const text = box.textContent;
    for (const expected of ["Report filed", "0.95", "26400 ppm", "30.0", "85.0", "Why: A named figure.", "next hourly update"]) {
      assert.ok(text.includes(expected), `missing ${expected} in ${text}`);
    }
  });

  it("shows ? for a value that is not a number, and nothing for a missing reason", () => {
    const { doc, el } = fresh();
    const box = el("section");
    renderConfirmation(doc, box, { relevance_score: "0.9", midichlorian_ppm: null, kyber_resonance: NaN, dark_side_activity: Infinity, inference_rationale: 5 });
    const values = box.find((e) => e.tagName === "DD").map((e) => e.textContent);
    assert.ok(values.every((v) => v.startsWith("?")), values.join("|"));
    assert.equal(box.find((e) => e.className === "rationale")[0].textContent, "");
  });

  it("keeps a hostile rationale as text", () => {
    for (const hostile of HOSTILE) {
      const { doc, el } = fresh();
      const box = el("section");
      renderConfirmation(doc, box, { ...response, inference_rationale: hostile });
      assert.equal(box.find((e) => e.className === "rationale")[0].textContent, `Why: ${hostile}`);
      assert.deepEqual(tags(box).filter((t) => !["SECTION", "H2", "DL", "DT", "DD", "P"].includes(t)), []);
    }
  });

  it("replaces an earlier confirmation", () => {
    const { doc, el } = fresh();
    const box = el("section");
    renderConfirmation(doc, box, response);
    renderConfirmation(doc, box, { ...response, inference_rationale: "Second." });
    assert.equal(box.find((e) => e.tagName === "H2").length, 1);
    assert.ok(!box.textContent.includes("A named figure."));
  });
});

describe("renderRecent", () => {
  const report = (description) => ({ description, event_time: "2026-10-14T14:59:00.000Z", relevance_score: 0.5, dark_side_activity: 60 });

  it("says so when there are no reports, or the answer is not a list", () => {
    for (const value of [[], null, undefined, "x", {}]) {
      const { doc, el } = fresh();
      const list = el("ul");
      const note = el("p");
      list.appendChild(el("li"));
      renderRecent(doc, list, note, value);
      assert.equal(list.children.length, 0);
      assert.equal(note.textContent, "No reports for this planet yet.");
    }
  });

  it("lists each report with its text, time, relevance and dark side value, and clears the note", () => {
    const { doc, el } = fresh();
    const list = el("ul");
    const note = el("p");
    note.textContent = "old";
    renderRecent(doc, list, note, [report("first"), report("second")]);
    assert.equal(note.textContent, "");
    assert.deepEqual(list.children.map((li) => li.children[0].textContent), ["first", "second"]);
    assert.equal(list.children[0].children[1].textContent, "2026-10-14T14:59:00.000Z | relevance 0.50 | dark side 60.0");
  });

  it("keeps hostile report text as text, in a plain paragraph", () => {
    for (const hostile of HOSTILE) {
      const { doc, el } = fresh();
      const list = el("ul");
      renderRecent(doc, list, el("p"), [report(hostile)]);
      assert.equal(list.children[0].children[0].textContent, hostile);
      assert.deepEqual(tags(list).filter((t) => !["UL", "LI", "P"].includes(t)), []);
    }
  });

  it("shows any value the server sent as text, even a non-string description", () => {
    const { doc, el } = fresh();
    const list = el("ul");
    renderRecent(doc, list, el("p"), [report(null), report({ a: 1 })]);
    assert.deepEqual(list.children.map((li) => li.children[0].textContent), ["null", "[object Object]"]);
  });
});

describe("renderQueue", () => {
  const snapshot = (queued = [], finished = [], wakeAt = null) => ({ queued, finished, wakeAt });
  const queued = (id, extra = {}) => ({ report_id: id, sector_id: "coruscant", state: "queued", message: "waiting", ...extra });
  const options = (extra = {}) => ({ onDismiss: () => {}, nowMs: 1000, ...extra });

  function run(snap, opts = options()) {
    const { doc, el } = fresh();
    const status = el("p");
    const list = el("ul");
    renderQueue(doc, status, list, snap, opts);
    return { status, list };
  }

  it("says nothing is waiting", () => {
    const { status, list } = run(snapshot());
    assert.equal(status.textContent, "Nothing is waiting to be sent.");
    assert.equal(list.children.length, 0);
  });

  it("counts waiting reports and says when the next attempt is", () => {
    assert.equal(run(snapshot([queued("a")], [], 1000 + 5000)).status.textContent, "1 report is waiting to be sent. Next attempt in 5 seconds.");
    assert.equal(run(snapshot([queued("a"), queued("b")], [], 500)).status.textContent, "2 reports are waiting to be sent. Next attempt now.");
    assert.equal(run(snapshot([queued("a")], [], 1000)).status.textContent, "1 report is waiting to be sent. Next attempt now.");
    assert.equal(run(snapshot([queued("a")], [], null)).status.textContent, "1 report is waiting to be sent.");
  });

  it("warns when the browser cannot keep reports", () => {
    const warning = "This browser cannot keep reports between visits: closing this tab would lose a waiting report.";
    assert.ok(run(snapshot([queued("a")]), options({ persistent: false })).status.textContent.includes(warning));
    assert.ok(!run(snapshot([queued("a")]), options({ persistent: true })).status.textContent.includes(warning));
    assert.ok(!run(snapshot([queued("a")])).status.textContent.includes(warning));
  });

  it("gives waiting reports no dismiss button, and finished ones a button that dismisses that report", async () => {
    const dismissed = [];
    const dropped = { report_id: "d1", sector_id: "tatooine", state: "dropped", message: "refused" };
    const failed = { report_id: "f1", sector_id: "naboo", state: "failed", message: "This report could not be assessed." };
    const { list } = run(snapshot([queued("q1")], [dropped, failed]), options({ onDismiss: (id) => dismissed.push(id) }));
    assert.deepEqual(list.children.map((li) => li.className), ["queue-queued", "queue-dropped", "queue-failed"]);
    assert.equal(list.children[0].find((e) => e.tagName === "BUTTON").length, 0);
    const buttons = list.find((e) => e.tagName === "BUTTON");
    assert.equal(buttons.length, 2);
    assert.equal(buttons[0].attributes.type, "button");
    for (const button of buttons) await button.fire("click");
    assert.deepEqual(dismissed, ["d1", "f1"]);
    assert.equal(list.children[1].children[0].textContent, "tatooine: dropped");
    assert.equal(list.children[2].children[1].textContent, "This report could not be assessed.");
  });

  it("keeps hostile messages and planet ids as text", () => {
    for (const hostile of HOSTILE) {
      const { list } = run(snapshot([queued("q", { message: hostile, sector_id: hostile })]));
      assert.ok(list.children[0].textContent.includes(hostile));
      assert.deepEqual(tags(list).filter((t) => !["UL", "LI", "P"].includes(t)), []);
    }
  });

  it("leaves out the message paragraph when there is none", () => {
    const { list } = run(snapshot([queued("q", { message: "" })]));
    assert.equal(list.children[0].children.length, 1);
  });
});
