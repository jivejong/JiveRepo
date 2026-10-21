// Badges and text of the incident feed (doc 07 Phase 7, panel 7; doc 06 "Re-escalation" flag). Pure.

/** The badge labels of an incident, in this order: "report", the status, "severity rose", "cooldown conflict". */
export function badgesFor(incident) {
  const out = [];
  if (incident.is_report_sourced) out.push("report");
  if (incident.status) out.push(incident.status);
  if (incident.severity_rose_after_decision) out.push("severity rose");
  if (incident.cooldown_conflict) out.push("cooldown conflict");
  return out;
}

/** The text cut to `max` characters with "..." appended when it was longer; null or empty gives "". */
export function truncate(text, max = 200) {
  if (typeof text !== "string") return "";
  return text.length > max ? `${text.slice(0, max)}...` : text;
}
