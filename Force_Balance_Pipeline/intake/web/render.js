// Drawing the page. Everything a reporter typed, or the server or the model said, reaches the page ONLY through `textContent`
// (and attributes the code itself builds). Nothing here uses innerHTML, outerHTML, insertAdjacentHTML or document.write; a test
// scans for them, and the Content-Security-Policy would stop an injected script anyway.

import { formatWait } from "./messages.js";
import { canDismiss } from "./queue.js";

function element(doc, tag, { text = null, className = null } = {}) {
  const el = doc.createElement(tag);
  if (className) el.className = className;
  if (text !== null) el.textContent = text;
  return el;
}

const number = (value, digits) => (typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "?");

export function renderSectors(doc, select, sectors) {
  const options = [element(doc, "option", { text: "Choose a planet" })];
  options[0].value = "";
  for (const sector of sectors) {
    const option = element(doc, "option", { text: String(sector.sector_name) });
    option.value = String(sector.sector_id);
    options.push(option);
  }
  select.replaceChildren(...options);
}

/** What the system made of the report: the values it inferred, the relevance and the model's reason. */
export function renderConfirmation(doc, container, response) {
  const rows = [
    ["Relevance", number(response.relevance_score, 2) + " (0 = nothing to do with the Force, 1 = a named dark side figure)"],
    ["Midichlorian density", number(response.midichlorian_ppm, 0) + " ppm"],
    ["Kyber resonance", number(response.kyber_resonance, 1)],
    ["Dark side activity", number(response.dark_side_activity, 1)],
  ];
  const list = element(doc, "dl");
  for (const [label, value] of rows) {
    list.appendChild(element(doc, "dt", { text: label }));
    list.appendChild(element(doc, "dd", { text: value }));
  }
  const reason = element(doc, "p", { className: "rationale" });
  reason.textContent = typeof response.inference_rationale === "string" ? `Why: ${response.inference_rationale}` : "";
  const heading = element(doc, "h2", { text: "Report filed" });
  const note = element(doc, "p", { text: "An incident, if one is warranted, appears after the next hourly update." });
  container.replaceChildren(heading, list, reason, note);
  container.hidden = false;
}

export function renderRecent(doc, list, note, reports) {
  if (!Array.isArray(reports) || reports.length === 0) {
    list.replaceChildren();
    note.textContent = "No reports for this planet yet.";
    return;
  }
  note.textContent = "";
  list.replaceChildren(
    ...reports.map((report) => {
      const item = element(doc, "li");
      item.appendChild(element(doc, "p", { className: "description", text: String(report.description) }));
      item.appendChild(
        element(doc, "p", { className: "meta",
          text: `${report.event_time} | relevance ${number(report.relevance_score, 2)} | dark side ${number(report.dark_side_activity, 1)}` }),
      );
      return item;
    }),
  );
}

/** The queue on this device: reports waiting, and the ones the reporter can dismiss (finished, or waiting on a 429 with nothing in flight). */
export function renderQueue(doc, statusEl, listEl, snapshot, { onDismiss, nowMs, persistent = true }) {
  const waiting = snapshot.queued.length;
  const parts = [];
  parts.push(waiting === 0 ? "Nothing is waiting to be sent." : `${waiting} report${waiting === 1 ? " is" : "s are"} waiting to be sent.`);
  if (waiting > 0 && snapshot.wakeAt !== null) {
    parts.push(`Next attempt ${snapshot.wakeAt <= nowMs ? "now" : formatWait(snapshot.wakeAt - nowMs)}.`);
  }
  if (!persistent) parts.push("This browser cannot keep reports between visits: closing this tab would lose a waiting report.");
  statusEl.textContent = parts.join(" ");
  const items = [];
  for (const entry of [...snapshot.queued, ...snapshot.finished]) {
    const item = element(doc, "li", { className: `queue-${entry.state}` });
    item.appendChild(element(doc, "p", { className: "state", text: `${entry.sector_id}: ${entry.state}` }));
    if (entry.message) item.appendChild(element(doc, "p", { className: "message", text: entry.message }));
    if (canDismiss(entry, nowMs)) {
      const button = element(doc, "button", { text: "Dismiss" });
      button.setAttribute("type", "button");
      button.addEventListener("click", () => onDismiss(entry.report_id));
      item.appendChild(button);
    }
    items.push(item);
  }
  listEl.replaceChildren(...items);
}
