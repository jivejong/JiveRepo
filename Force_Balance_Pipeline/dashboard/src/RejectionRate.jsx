// Panel 6 (doc 07 Phase 7): the rejection rate per hour and the rejects by reason. Shows a plain message when there are none.
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatNumber, formatUtc } from "./lib/format.js";
import { pivotByReason, reasons } from "./lib/rejections.js";
import { usePoll } from "./usePoll.js";

const hourLabel = (h) => formatUtc(h).slice(5, 16);

export default function RejectionRate() {
  const { data, error } = usePoll("/api/rejections");
  const rate = data ? data.rate : [];
  const none = !data || rate.length === 0 || rate.every((r) => r.rejects === 0);
  const names = data ? reasons(data.by_reason) : [];
  return (
    <section className="panel rejections" aria-labelledby="rej-h">
      <h2 id="rej-h">Rejections</h2>
      {error ? <p className="error">Rejections: {error}</p> : null}
      {data && none ? <p>No rejects in the last 7 days</p> : null}
      {data && !none ? (
        <>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={rate} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="grid" />
              <XAxis dataKey="hour" tickFormatter={hourLabel} minTickGap={48} />
              <YAxis width={56} tickFormatter={(v) => formatNumber(v, 3)} />
              <Tooltip formatter={(v) => formatNumber(v, 4)} labelFormatter={formatUtc} />
              <Line type="monotone" dataKey="rate" name="rejection rate" dot={false} isAnimationActive={false} className="series-line" />
            </LineChart>
          </ResponsiveContainer>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={pivotByReason(data.by_reason)} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="grid" />
              <XAxis dataKey="hour" tickFormatter={hourLabel} minTickGap={48} />
              <YAxis allowDecimals={false} width={56} />
              <Tooltip labelFormatter={formatUtc} />
              <Legend />
              {names.map((name, i) => (
                <Bar key={name} dataKey={name} stackId="r" className={`reason-${i % 6}`} isAnimationActive={false} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </>
      ) : null}
    </section>
  );
}
