// Panel 5 (doc 07 Phase 7): the ingest lag histogram. Normal and replayed rows are stacked; buffered rows are a separate bar.
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { bars } from "./lib/histogram.js";
import { usePoll } from "./usePoll.js";

export default function IngestLag() {
  const { data, error } = usePoll("/api/ingest-lag");
  let rows = [];
  let problem = null;
  try {
    rows = data ? bars(data) : [];
  } catch (e) {
    problem = e.message;
  }
  return (
    <section className="panel lag" aria-labelledby="lag-h">
      <h2 id="lag-h">Ingest lag (7 days to the newest arrival)</h2>
      {error || problem ? <p className="error">Ingest lag: {error || problem}</p> : null}
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="grid" />
          <XAxis dataKey="label" interval={0} angle={-30} textAnchor="end" height={54} />
          <YAxis allowDecimals={false} width={56} />
          <Tooltip />
          <Legend />
          <Bar dataKey="normal" stackId="a" name="on time" className="bar-decided" isAnimationActive={false} />
          <Bar dataKey="replayed" stackId="a" name="replayed" className="bar-open" isAnimationActive={false} />
          <Bar dataKey="buffered" name="buffered" className="bar-other" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
      <p className="footnote">Lags down to -2 s are clock jitter (doc 03); rows with an event time in the future are excluded.</p>
    </section>
  );
}
