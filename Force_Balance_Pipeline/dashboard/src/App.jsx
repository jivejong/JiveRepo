// The dashboard page (doc 07 Phase 7): the data-as-of header, the fixture banner, and the first four panels.
import { useState } from "react";

import GalaxyView from "./GalaxyView.jsx";
import PlanetDetail from "./PlanetDetail.jsx";
import SignatureBreakdown from "./SignatureBreakdown.jsx";
import SourceHealth from "./SourceHealth.jsx";
import { formatAgo, formatUtc } from "./lib/format.js";
import { usePoll } from "./usePoll.js";

export default function App() {
  const [planet, setPlanet] = useState(null);
  const meta = usePoll("/api/meta");
  const publish = meta.data ? meta.data.publish : null;

  return (
    <div className="page">
      <header className="top">
        <h1>Jedi Council</h1>
        <p className="asof">
          {publish
            ? `Data as of ${formatUtc(publish.finished_at)} (Job 1 run ended ${formatAgo(Date.now(), publish.job1_run_end)})`
            : meta.error
              ? "No data yet"
              : "Loading"}
        </p>
        {meta.error ? <p className="error" role="alert">The backend did not answer ({meta.error}); showing the last data.</p> : null}
      </header>
      {publish && publish.kind === "fixture" ? <div className="banner" role="status">FIXTURE DATA</div> : null}
      <main className="panels">
        <GalaxyView onSelect={setPlanet} selected={planet} />
        {planet ? <PlanetDetail sectorId={planet} onClose={() => setPlanet(null)} /> : null}
        <SignatureBreakdown />
        <SourceHealth />
      </main>
    </div>
  );
}
