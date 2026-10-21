// Panel 7 (doc 07 Phase 7): the incident feed with tabs. Every server string is rendered as text; a report description is cut to 200 characters.
import { useState } from "react";

import { DeployControl } from "./DeployForm.jsx";
import { formatAgo, formatNumber, formatUtc } from "./lib/format.js";
import { badgesFor, truncate } from "./lib/incidents.js";
import { usePoll } from "./usePoll.js";

const TABS = [
  ["All", null],
  ["Open", "open"],
  ["Decided", "decided"],
  ["Historical", "historical"],
];

export default function IncidentFeed({ meta }) {
  const [status, setStatus] = useState(null);
  const path = status ? `/api/incidents?status=${status}&limit=50` : "/api/incidents?limit=50";
  const { data, error } = usePoll(path);
  return (
    <section className="panel incidents" aria-labelledby="inc-h">
      <h2 id="inc-h">Incidents</h2>
      <div className="tabs" role="tablist" aria-label="Incident status">
        {TABS.map(([label, value]) => (
          <button type="button" role="tab" key={label} aria-selected={status === value} className={status === value ? "tab active" : "tab"} onClick={() => setStatus(value)}>
            {label}
          </button>
        ))}
      </div>
      {error ? <p className="error">Incidents: {error}</p> : null}
      {data && data.length === 0 ? <p>No incidents.</p> : null}
      <ul className="feed">
        {(data || []).map((i) => (
          <li key={i.disturbance_id}>
            <div className="row-head">
              <strong>{i.sector_name}</strong> ({i.region || "-"}) - {i.signature} - severity {formatNumber(i.severity, 2)}
            </div>
            <div className="row-sub">
              {formatUtc(i.detected_at)} ({formatAgo(Date.now(), i.detected_at)})
              {badgesFor(i).map((b) => (
                <span key={b} className={`badge badge-${b.replace(/ /g, "-")}`}>{b}</span>
              ))}
              {i.status === "open" ? <span className="note">awaiting decision</span> : null}
              {i.status === "open" ? <DeployControl disturbanceId={i.disturbance_id} meta={meta} /> : null}
            </div>
            {i.report_description ? <p className="quote">{truncate(i.report_description, 200)}</p> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
