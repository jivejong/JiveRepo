// The page's behaviour, with every browser facility passed in (`env`), so it runs the same in a test with fakes. main.js hands it the
// real ones.

import { buildReport, loadRecent, loadSectors, validateDescription } from "./api.js";
import { createIdentity } from "./identity.js";
import { MAX_DESCRIPTION_CHARS } from "./messages.js";
import { ReportQueue } from "./queue.js";
import { renderConfirmation, renderQueue, renderRecent, renderSectors } from "./render.js";
import { openStore } from "./store.js";
import { newUlid } from "./ulid.js";

const WAKE_SLACK_MS = 50;

export async function startApp(env) {
  const { document: doc, fetchFn, indexedDB, getStorage, now, randomBytes, setTimeoutFn, clearTimeoutFn, lock = null,
    listenOnline = () => {}, listenVisible = () => {} } = env;
  const get = (id) => doc.getElementById(id);
  const ui = {
    form: get("report-form"), planet: get("planet"), description: get("description"), counter: get("counter"), submit: get("submit"),
    message: get("message"), confirmation: get("confirmation"), recentList: get("recent-list"), recentNote: get("recent-note"),
    queueStatus: get("queue-status"), queueList: get("queue-list"),
  };
  const identity = createIdentity({ getStorage, randomBytes });
  const store = await openStore(indexedDB);
  let wakeTimer = null;
  let shownSector = "";
  let recentToken = 0;

  const say = (text) => {
    ui.message.textContent = text;
  };

  async function refreshQueue() {
    const snapshot = await queue.snapshot();
    renderQueue(doc, ui.queueStatus, ui.queueList, snapshot, { onDismiss: dismiss, nowMs: now(), persistent: store.persistent });
    if (wakeTimer !== null) clearTimeoutFn(wakeTimer);
    wakeTimer = null;
    if (snapshot.wakeAt !== null) wakeTimer = setTimeoutFn(() => drain(), Math.max(0, snapshot.wakeAt - now()) + WAKE_SLACK_MS);
  }

  async function showRecent() {
    const token = ++recentToken; // an older answer must not overwrite a newer one (the reporter changed planet meanwhile)
    const sectorId = ui.planet.value;
    shownSector = sectorId;
    if (!sectorId) {
      renderRecent(doc, ui.recentList, ui.recentNote, []);
      ui.recentNote.textContent = "Choose a planet to see its recent reports.";
      return;
    }
    const answer = await loadRecent(fetchFn, sectorId);
    if (token !== recentToken) return;
    if (!answer.ok || !Array.isArray(answer.body)) {
      ui.recentList.replaceChildren();
      ui.recentNote.textContent = "Could not load the recent reports.";
      return;
    }
    renderRecent(doc, ui.recentList, ui.recentNote, answer.body);
  }

  function onEvent(event) {
    const entry = event.entry;
    if (event.type === "sent") {
      if (event.response && typeof event.response === "object") renderConfirmation(doc, ui.confirmation, event.response);
      say("Report filed.");
      if (entry.sector_id === shownSector) showRecent();
    } else if (event.type === "queued") {
      say("Saved on this device. Sending...");
    } else if (["retry", "rate_limited", "dropped", "failed"].includes(event.type)) {
      say(entry.message || "");
    }
  }

  const queue = new ReportQueue({ store, fetchFn, now, setTimeoutFn, clearTimeoutFn, lock, onEvent });

  async function drain() {
    try {
      await queue.drain();
    } catch (e) {
      say("Something went wrong while sending. Waiting reports are still saved on this device.");
    }
    await refreshQueue();
  }

  async function dismiss(reportId) {
    await queue.dismiss(reportId);
    await refreshQueue();
  }

  function updateCounter() {
    ui.counter.textContent = `${Array.from(ui.description.value).length} / ${MAX_DESCRIPTION_CHARS}`;
  }

  let submitting = false; // a second submit while the first is being saved must not make a second report

  async function onSubmit(event) {
    if (event && event.preventDefault) event.preventDefault();
    if (submitting) return;
    const sectorId = ui.planet.value;
    if (!sectorId) return say("Choose a planet first.");
    const description = ui.description.value;
    const problem = validateDescription(description);
    if (problem) return say(problem.message);
    submitting = true;
    ui.submit.disabled = true;
    try {
      const report = buildReport({ sectorId, description, reporterId: identity.reporterId(), nowMs: now(),
        newUlid: (ms) => newUlid(ms, randomBytes) });
      await queue.enqueue(report);
      ui.description.value = ""; // the report is safe in the store now
      updateCounter();
    } catch (e) {
      return say("This browser could not save the report, so nothing was sent. Your text is still in the box.");
    } finally {
      submitting = false;
      ui.submit.disabled = false;
    }
    await drain();
  }

  ui.form.addEventListener("submit", onSubmit);
  ui.planet.addEventListener("change", showRecent);
  ui.description.addEventListener("input", updateCounter);
  listenOnline(() => drain());
  listenVisible(() => drain());
  updateCounter();
  ui.confirmation.hidden = true;

  const sectors = await loadSectors(fetchFn);
  if (sectors.ok && Array.isArray(sectors.body)) {
    renderSectors(doc, ui.planet, sectors.body);
  } else {
    say("Could not load the list of planets. Reload the page when the connection is back.");
  }
  await showRecent();
  await drain(); // anything saved on an earlier visit
  return { queue, store, ui, identity, onSubmit, drain, showRecent };
}
