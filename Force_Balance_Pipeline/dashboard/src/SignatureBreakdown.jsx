// Panel 3 (doc 07 Phase 7): incidents by signature, open, decided and the rest, with a table of report-sourced counts.
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatUtc } from "./lib/format.js";
import { usePoll } from "./usePoll.js";

export default function SignatureBreakdown() {
  const { data, error } = usePoll("/api/signatures");
  const rows = (data || []).map((r) => ({
    signature: r.signature,
    open: r.incidents_open,
    decided: r.incidents_decided,
    other: r.incidents_total - r.incidents_open - r.incidents_decided,
  }));
  return (
    <section className="panel signatures" aria-labelledby="sig-h">
      <h2 id="sig-h">Signatures</h2>
      {error ? <p className="error">Signatures: {error}</p> : null}
      <ResponsiveContainer width="100%" height={Math.max(120, rows.length * 34 + 60)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 8, bottom: 0, left: 16 }}>
          <CartesianGrid strokeDasharray="3 3" className="grid" />
          <XAxis type="number" allowDecimals={false} />
          <YAxis type="category" dataKey="signature" width={120} />
          <Tooltip />
          <Legend />
          <Bar dataKey="open" stackId="a" name="open" className="bar-open" isAnimationActive={false} />
          <Bar dataKey="decided" stackId="a" name="decided" className="bar-decided" isAnimationActive={false} />
          <Bar dataKey="other" stackId="a" name="historical" className="bar-other" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
      <table>
        <thead>
          <tr><th>Signature</th><th>Total</th><th>Report-sourced</th><th>Last detected</th></tr>
        </thead>
        <tbody>
          {(data || []).map((r) => (
            <tr key={r.signature}>
              <td>{r.signature}</td>
              <td>{r.incidents_total}</td>
              <td>{r.incidents_report_sourced}</td>
              <td>{formatUtc(r.last_detected_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
