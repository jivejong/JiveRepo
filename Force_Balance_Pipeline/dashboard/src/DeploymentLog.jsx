// Panel 8 (doc 07 Phase 7): the deployment log, agent and user decisions side by side. Rationales and every other server string are
// rendered as text: a tag in a rationale shows literally. Countdowns target released_at (display rules).
import { countdown } from "./lib/countdown.js";
import { formatUtc } from "./lib/format.js";
import { summarizeOverrides } from "./lib/overrides.js";
import { usePoll } from "./usePoll.js";

const WHO = { yoda_agent: "Yoda", user: "User" };

function Overrides({ value }) {
  const items = summarizeOverrides(value);
  return (
    <details>
      <summary>{items.length} guardrail override{items.length === 1 ? "" : "s"}</summary>
      <ul>
        {items.map((o, n) => (
          <li key={n}>
            reasons: {o.reasons.join(", ") || "-"}
            {o.proposalJedi.length > 0 || o.proposalShip ? `; proposed: ${o.proposalJedi.join(", ") || "-"} on ${o.proposalShip || "-"}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

export default function DeploymentLog() {
  const { data, error } = usePoll("/api/deployments?limit=50");
  const now = Date.now();
  return (
    <section className="panel deployments" aria-labelledby="dep-h">
      <h2 id="dep-h">Deployments</h2>
      {error ? <p className="error">Deployments: {error}</p> : null}
      {data && data.length === 0 ? <p>No deployments.</p> : null}
      <ul className="feed">
        {(data || []).map((d) => (
          <li key={d.deployment_id}>
            <div className="row-head">
              <span className={`badge badge-${d.decided_by}`}>{WHO[d.decided_by] || d.decided_by}</span> <strong>{d.decision}</strong>
              {" "}- {d.sector_name || "unknown planet"}
              {d.decision === "deploy" ? ` - ${(d.jedi_ids || []).join(", ") || "-"} on ${d.starship_id || "-"}` : ""}
            </div>
            <div className="row-sub">
              decided {formatUtc(d.decided_at)}; arrives {countdown(now, d.arrival_at)}; released {countdown(now, d.released_at)}
              {d.orphaned ? <span className="badge badge-orphaned">orphaned</span> : null}
              {d.capacity_reached ? <span className="badge badge-capacity">fleet at capacity</span> : null}
            </div>
            <p className="quote">{d.rationale}</p>
            {d.decided_by === "yoda_agent" ? (
              <div className="row-sub">model {d.model || "-"}; decision took {d.decision_latency_ms === null || d.decision_latency_ms === undefined ? "-" : `${d.decision_latency_ms} ms`}</div>
            ) : null}
            {d.override_count > 0 ? <Overrides value={d.guardrail_overrides} /> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
