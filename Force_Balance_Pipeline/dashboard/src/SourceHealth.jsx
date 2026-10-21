// Panel 4 (doc 07 Phase 7): the probe's mode, last seen, last scan complete, buffer depth (always n/a) and a strip of the last 96 scans.
import { formatAgo, formatUtc } from "./lib/format.js";
import { completenessStrip, summary } from "./lib/health.js";
import { usePoll } from "./usePoll.js";

const BUFFER_NOTE = "not reported by the probe (silver_source_health.buffer_depth is always NULL)";

export default function SourceHealth() {
  const { data, error } = usePoll("/api/source-health");
  const s = summary(data);
  const strip = completenessStrip(data ? data.scans : []);
  return (
    <section className="panel health" aria-labelledby="health-h">
      <h2 id="health-h">Source health</h2>
      {error ? <p className="error">Source health: {error}</p> : null}
      <dl className="facts">
        <dt>Mode</dt><dd>{s.mode || "-"}</dd>
        <dt>Last seen</dt><dd>{formatAgo(Date.now(), s.lastSeen)}</dd>
        <dt>Last scan complete</dt><dd>{s.lastScanComplete === null ? "-" : s.lastScanComplete ? "yes" : "no"}</dd>
        <dt>Buffer depth</dt><dd title={BUFFER_NOTE}>{s.bufferDepth}</dd>
      </dl>
      <p className="strip-title">Last {strip.length} scans</p>
      <ul className="strip" aria-label="Scan completeness, oldest first">
        {strip.map((x) => (
          <li
            key={x.scan_id}
            className={x.disconnected ? "scan disconnected" : x.complete ? "scan complete" : "scan incomplete"}
            title={`${formatUtc(x.t)}: ${x.complete ? "complete" : "incomplete"}${x.disconnected ? ", disconnected" : ""}`}
          />
        ))}
      </ul>
      <p className="legend-line">complete / incomplete / disconnected</p>
    </section>
  );
}
