// Panel 2 (doc 07 Phase 7): one planet. Three channel charts with the 90-day baseline bands (the mean, 1 sigma darker, 2 sigma lighter),
// replayed points marked, the planet's description and force history as plain text, and its incidents.
import { CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatNumber, formatUtc } from "./lib/format.js";
import { channelSeries } from "./lib/planet.js";
import { usePoll } from "./usePoll.js";

const TITLES = { midichlorian: "Midichlorian (ppm)", kyber: "Kyber resonance", dark_side: "Dark side activity" };

function ReplayDot(props) {
  const { cx, cy, payload } = props;
  if (!payload || !payload.replayed || cx === undefined) return <g />;
  return <circle cx={cx} cy={cy} r={3} className="replay-dot" />;
}

function ChannelChart({ series }) {
  const { channel, points, bands } = series;
  const low = bands.lower2 !== null ? bands.lower2 : undefined;
  const high = bands.upper2 !== null ? bands.upper2 : undefined;
  return (
    <figure className="chart">
      <figcaption>{TITLES[channel]}</figcaption>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="grid" />
          <XAxis dataKey="t" tickFormatter={(t) => formatUtc(t).slice(5, 16)} minTickGap={48} />
          <YAxis
            width={56}
            domain={[
              (min) => (low !== undefined ? Math.min(min, low) : min),
              (max) => (high !== undefined ? Math.max(max, high) : max),
            ]}
            tickFormatter={(v) => formatNumber(v, 1)}
          />
          {bands.lower2 !== null ? <ReferenceArea y1={bands.lower2} y2={bands.upper2} className="band-2sigma" ifOverflow="extendDomain" /> : null}
          {bands.lower1 !== null ? <ReferenceArea y1={bands.lower1} y2={bands.upper1} className="band-1sigma" ifOverflow="extendDomain" /> : null}
          {bands.mean !== null ? <ReferenceLine y={bands.mean} className="band-mean" /> : null}
          <Tooltip formatter={(v) => formatNumber(v, 2)} labelFormatter={(t) => formatUtc(t)} />
          <Line type="monotone" dataKey="value" dot={<ReplayDot />} isAnimationActive={false} className="series-line" />
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}

export default function PlanetDetail({ sectorId, onClose }) {
  const { data, error } = usePoll(`/api/planet/${encodeURIComponent(sectorId)}`);
  return (
    <section className="panel planet" aria-labelledby="planet-h">
      <div className="panel-head">
        <h2 id="planet-h">{data ? data.sector.sector_name : sectorId}</h2>
        <button type="button" onClick={onClose}>Close</button>
      </div>
      {error ? <p className="error">Planet: {error}</p> : null}
      {data ? (
        <>
          <p className="meta-line">
            {data.sector.system_name} - {data.sector.region}
          </p>
          <p>{data.sector.description}</p>
          <p className="history">{data.sector.force_history}</p>
          {channelSeries(data.readings, data.baseline).map((s) => (
            <ChannelChart key={s.channel} series={s} />
          ))}
          <h3>Incidents</h3>
          {data.incidents.length === 0 ? <p>No incidents.</p> : (
            <table>
              <thead>
                <tr><th>Detected</th><th>Signature</th><th>Severity</th><th>Status</th><th /></tr>
              </thead>
              <tbody>
                {data.incidents.map((i) => (
                  <tr key={i.disturbance_id}>
                    <td>{formatUtc(i.detected_at)}</td>
                    <td>{i.signature}</td>
                    <td>{formatNumber(i.severity, 2)}</td>
                    <td>{i.status}</td>
                    <td>{i.is_report_sourced ? <span className="badge">report</span> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : null}
    </section>
  );
}
