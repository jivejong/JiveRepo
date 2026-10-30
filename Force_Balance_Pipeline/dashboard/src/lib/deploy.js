// The deploy form's rules (doc 06 "Manual deployment path"). Pure: no DOM, no fetch, no clock of its own. validate mirrors serving/deploy.py
// validate; the service stays the authority, this only keeps the submit button honest.

export const MAX_PARTY = 4;
export const MAX_RATIONALE = 2000;
const DISTURBANCE_ID = /^[0-9A-Za-z_]{1,64}$/;
const SLUG = /^[a-z0-9_]{1,64}$/;
const HOUR_MS = 3600000;
const AGENT_MINUTE = 11;
const CYCLE_MS = 2 * HOUR_MS;                      // the agent polls only after a Job 1 run, and Job 1 runs at :03 on even UTC hours
const MIN_FIRST_SEEN_AGE_MS = 30 * 60000;          // agent/config.py MIN_FIRST_SEEN_AGE_MINUTES

/** A list of problems (empty when the form may be sent). form: {disturbanceId, jedi: [ids], starship: id or "", rationale}. */
export function validate(form) {
  const errors = [];
  const jedi = Array.isArray(form.jedi) ? form.jedi : [];
  if (typeof form.disturbanceId !== "string" || !DISTURBANCE_ID.test(form.disturbanceId)) errors.push("Incident is not valid.");
  if (jedi.length < 1) errors.push("Choose at least one Jedi.");
  if (jedi.length > MAX_PARTY) errors.push(`Choose at most ${MAX_PARTY} Jedi.`);
  if (new Set(jedi).size !== jedi.length) errors.push("A Jedi is chosen twice.");
  if (jedi.some((j) => typeof j !== "string" || !SLUG.test(j))) errors.push("A Jedi id is not valid.");
  if (typeof form.starship !== "string" || !SLUG.test(form.starship)) errors.push("Choose a starship.");
  const rationale = typeof form.rationale === "string" ? form.rationale.trim() : "";
  if (rationale.length < 1) errors.push("Write a rationale.");
  if (rationale.length > MAX_RATIONALE) errors.push(`The rationale is over ${MAX_RATIONALE} characters.`);
  return errors;
}

/** The request body: exactly the four keys the endpoint accepts. */
export function buildRequest(form) {
  return {
    disturbance_id: form.disturbanceId,
    jedi_ids: [...form.jedi],
    starship_id: form.starship,
    rationale: form.rationale.trim(),
  };
}

const LABELS = {
  // agent/constraints.py REASONS
  severity_below_threshold: "The incident's severity is below the deployment threshold.",
  signature_missing: "The incident has no signature.",
  no_jedi: "No Jedi was chosen.",
  duplicate_jedi: "A Jedi was chosen twice.",
  party_too_large: "The party is larger than four Jedi.",
  jedi_unavailable: "A chosen Jedi is already deployed.",
  starship_unavailable: "The chosen starship is already deployed.",
  deployment_capacity_reached: "Three deployments are already open.",
  insufficient_rank: "No chosen Jedi has the rank of Master or higher.",
  specialty_mismatch: "No chosen Jedi has a specialty that matches the incident.",
  insufficient_capacity: "The starship cannot carry the party.",
  agent_exhausted_retries: "The agent ran out of retries.",
  agent_no_decision: "The agent made no decision.",
  invalid_arguments: "The request arguments were not valid.",
  unknown_tool: "An unknown tool was requested.",
  blocked_by_model: "The model blocked the deployment.",
  one_decision_per_turn: "Only one decision is allowed per turn.",
  // serving/deploy.py refusals
  invalid_request: "The request was not valid.",
  quota_hour: "Deployments are paused in the 03:xx UTC hour.",
  not_open: "The incident is not open.",
  unknown_incident: "The incident is not known.",
  agent_cycle_in_progress: "The agent is running a cycle; try again in a minute.",
  job1_running: "Job 1 is running; try again when it has finished.",
  jobs_api_error: "Job 1 could not be checked; try again.",
  already_decided: "The incident was already decided.",
  internal_error: "The service failed before writing anything.",
  // serving/deploy.py warnings
  mark_failed: "The incident could not be marked; the agent's next repair marks it.",
  refresh_failed: "The deployment was written but the local copy is not refreshed yet; the next pull will update it.",
  merge_result_unknown: "The write's result could not be read, so it is not known whether the deployment was recorded; check the deployment log.",
  // endpoint errors
  deploy_in_progress: "Another deployment is being sent; try again.",
  deploy_disabled: "Deploying is disabled on this dashboard.",
  bad_origin: "The request came from the wrong origin.",
  bad_token: "The page's deploy token was refused; reload the page.",
  bad_body: "The request body was not valid.",
  body_too_large: "The request was too large.",
  unsupported_media_type: "The request was not JSON.",
  internal: "The dashboard backend failed.",
};

/** Whether a Jedi or starship can be ticked: always. A deployed one stays selectable, labelled with its release time, because the list is a hint
 *  and the constraint layer decides at submit. */
export function isSelectable(item) {
  return Boolean(item);
}

/** The label for a reason or warning code; an unknown code shows itself. */
export function reasonLabel(code) {
  return Object.prototype.hasOwnProperty.call(LABELS, code) ? LABELS[code] : String(code);
}

/** The label table's codes (for the parity test). */
export const KNOWN_CODES = Object.keys(LABELS);

/** When the agent decides an incident first seen at `firstSeenIso` (doc 06 "Human-first window"): the agent polls only after a new Job 1 run, so
 *  its cycles that count are the :11 of even UTC hours, and it decides an incident only when it was first seen at least 30 minutes earlier. That is
 *  the first HH:11:00Z with HH even strictly after firstSeen + 30 minutes. If that is not after `nowMs` (the agent missed a cycle), it steps
 *  forward 2 hours at a time until it is. Returns an ISO string, or null when the incident has not been seen or the input is invalid. */
export function nextAgentDecision(firstSeenIso, nowMs) {
  if (typeof firstSeenIso !== "string" || typeof nowMs !== "number") return null;
  const seen = new Date(firstSeenIso).getTime();
  if (Number.isNaN(seen)) return null;
  const offset = AGENT_MINUTE * 60000;
  const earliest = seen + MIN_FIRST_SEEN_AGE_MS;
  let slot = Math.floor((earliest - offset) / CYCLE_MS) * CYCLE_MS + offset + CYCLE_MS;
  while (slot <= nowMs) slot += CYCLE_MS;
  return new Date(slot).toISOString();
}

/** "HH:MM" of an ISO time in UTC, or "-". */
export function clockUtc(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}
