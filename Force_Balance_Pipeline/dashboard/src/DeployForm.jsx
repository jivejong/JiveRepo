// The manual deploy form (doc 06 "Manual deployment path"). DeployControl is the "Deploy..." button on an open incident (or a note saying why
// deploying is off); DeployForm loads the options, validates with lib/deploy.js and shows the service's answer. Every string is rendered as text.
import { useState } from "react";

import { postJson } from "./api.js";
import { MAX_PARTY, MAX_RATIONALE, buildRequest, clockUtc, isSelectable, nextAgentDecision, reasonLabel, validate } from "./lib/deploy.js";
import { formatUtc } from "./lib/format.js";
import { refreshAll, usePoll } from "./usePoll.js";

/** The button for an open incident, or the reason deploying is off. `meta` is the /api/meta data (or null while loading). */
export function DeployControl({ disturbanceId, meta }) {
  const [open, setOpen] = useState(false);
  if (!meta) return null;
  if (!meta.deploy_enabled) {
    return <span className="note deploy-off">Deploy is off: {meta.deploy_disabled_why || "unknown"}</span>;
  }
  return (
    <span className="deploy-control">
      <button type="button" className="deploy-open" onClick={() => setOpen(!open)} aria-expanded={open}>
        Deploy...
      </button>
      {open ? <DeployForm disturbanceId={disturbanceId} onClose={() => setOpen(false)} /> : null}
    </span>
  );
}

function until(item) {
  return item.unavailable_until ? ` - deployed until ${formatUtc(item.unavailable_until)}` : "";
}

export default function DeployForm({ disturbanceId, onClose }) {
  const { data, error } = usePoll(`/api/deploy/options?disturbance_id=${encodeURIComponent(disturbanceId)}`);
  const [jedi, setJedi] = useState([]);
  const [starship, setStarship] = useState("");
  const [rationale, setRationale] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState(null);

  const form = { disturbanceId, jedi, starship, rationale };
  const errors = validate(form);

  const toggle = (id) => setJedi((list) => (list.includes(id) ? list.filter((j) => j !== id) : list.length < MAX_PARTY ? [...list, id] : list));

  const submit = async (event) => {
    event.preventDefault();
    if (pending || errors.length) return;
    setPending(true);
    setResult(null);
    try {
      const { status, body } = await postJson("/api/deploy", buildRequest(form));
      setResult({ status, body });
      if (status === 200 && body && body.ok) refreshAll();
    } catch (e) {
      setResult({ status: 0, body: { error: "network" } });
    } finally {
      setPending(false);
    }
  };

  const decideAt = data ? nextAgentDecision(data.agent_first_seen, Date.now()) : null;

  return (
    <form className="deploy-form" onSubmit={submit} aria-label="Deploy">
      <h3>Deploy to an open incident</h3>
      {error ? <p className="error">Options: {error}</p> : null}
      {data ? (
        <>
          <p>
            <strong>{data.incident.sector_name}</strong> ({data.incident.region || "-"}) - {data.incident.signature} - detected {formatUtc(data.incident.detected_at)}
          </p>
          <p className="note">
            {decideAt
              ? `Yoda decides at ${clockUtc(decideAt)} UTC`
              : "Yoda has not seen it yet: he records it at the next even-hour :11 cycle and decides it two hours later."}
          </p>
          <fieldset>
            <legend>Jedi (choose 1 to {MAX_PARTY})</legend>
            {data.jedi.map((j) => (
              <label key={j.jedi_id} className="choice">
                <input type="checkbox" disabled={!isSelectable(j)} checked={jedi.includes(j.jedi_id)} onChange={() => toggle(j.jedi_id)} />{" "}
                {j.jedi_name} ({j.rank}, {j.primary_specialty}
                {j.secondary_specialty ? `/${j.secondary_specialty}` : ""}){until(j)}
              </label>
            ))}
          </fieldset>
          <fieldset>
            <legend>Starship</legend>
            {data.starships.map((s) => (
              <label key={s.starship_id} className="choice">
                <input type="radio" name="starship" disabled={!isSelectable(s)} checked={starship === s.starship_id} onChange={() => setStarship(s.starship_id)} />{" "}
                {s.starship_name} (crew {s.crew}, passengers {s.passengers}, hyperdrive {s.hyperdrive_rating}){until(s)}
              </label>
            ))}
          </fieldset>
          <label className="choice">
            Rationale
            <textarea value={rationale} onChange={(e) => setRationale(e.target.value)} rows={4} />
          </label>
          <p className="footnote">
            {rationale.trim().length} / {MAX_RATIONALE}
          </p>
          {errors.length ? <p className="footnote">{errors.join(" ")}</p> : null}
          <div className="deploy-actions">
            <button type="submit" disabled={pending || errors.length > 0}>
              {pending ? "Sending..." : "Deploy"}
            </button>
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </>
      ) : error ? null : (
        <p>Loading</p>
      )}
      <DeployResult result={result} />
    </form>
  );
}

function DeployResult({ result }) {
  if (!result) return null;
  const { status, body } = result;
  if (status === 200 && body && body.ok) {
    return (
      <div className="deploy-result ok" role="status">
        <p>Deployed ({body.deployment_id})</p>
        {(body.warnings || []).map((w) => (
          <p key={w} className="note">
            {reasonLabel(w)}
          </p>
        ))}
      </div>
    );
  }
  const service = status === 200 && body && Array.isArray(body.reasons);
  const codes = service ? body.reasons : [body && body.why ? body.why : body && body.error ? body.error : `http_${status}`];
  return (
    <div className="deploy-result refused" role="alert">
      <p>{service && !codes.every(isServiceCode) ? "Rejected by the constraint layer:" : "Refused:"}</p>
      <ul>
        {codes.map((c) => (
          <li key={c}>{reasonLabel(c)}</li>
        ))}
      </ul>
    </div>
  );
}

const SERVICE_CODES = new Set([
  "invalid_request", "quota_hour", "not_open", "unknown_incident", "agent_cycle_in_progress", "job1_running", "jobs_api_error",
  "already_decided", "internal_error",
]);

function isServiceCode(code) {
  return SERVICE_CODES.has(code);
}
