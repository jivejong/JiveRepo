// Panel 1 (doc 07 Phase 7): the galaxy by region, each planet a tile coloured by the server's band.
import { bandClass, bandLabel } from "./lib/bands.js";
import { formatNumber } from "./lib/format.js";
import { groupByRegion } from "./lib/regions.js";
import { usePoll } from "./usePoll.js";

const LEGEND = [
  ["normal", "normal"],
  ["anomaly", "anomaly (above 4.0)"],
  ["emergency", "emergency (above 5.75)"],
  ["no_data", "no data"],
];

export default function GalaxyView({ onSelect, selected }) {
  const { data, error } = usePoll("/api/galaxy");
  return (
    <section className="panel galaxy" aria-labelledby="galaxy-h">
      <h2 id="galaxy-h">Galaxy</h2>
      {error ? <p className="error">Galaxy: {error}</p> : null}
      <ul className="legend">
        {LEGEND.map(([band, text]) => (
          <li key={band}>
            <span className={`swatch ${bandClass(band)}`} /> {text}
          </li>
        ))}
      </ul>
      {groupByRegion(data || []).map(({ region, planets }) => (
        <div className="region" key={region}>
          <h3>{region}</h3>
          <div className="tiles">
            {planets.map((p) => (
              <button
                type="button"
                key={p.sector_id}
                className={`tile ${bandClass(p.band)}${selected === p.sector_id ? " selected" : ""}`}
                aria-label={`${p.sector_name}, ${bandLabel(p.band)}, score ${formatNumber(p.imbalance_score, 2)}`}
                onClick={() => onSelect(p.sector_id)}
              >
                <span className="name">{p.sector_name}</span>
                <span className="score">{formatNumber(p.imbalance_score, 2)}</span>
                {p.open_disturbance_id ? <span className="marker" title="open incident">open incident</span> : null}
              </button>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
