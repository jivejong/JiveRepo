// The dashboard page (doc 07 Phase 7): the data-as-of header, the fixture banner, and the first four panels.
// The four panels that draw charts import recharts, the bulk of the bundle, so they load on demand (React.lazy) as their own chunks.
import { Suspense, lazy, useState } from "react";

import DeploymentLog from "./DeploymentLog.jsx";
import GalaxyView from "./GalaxyView.jsx";
import IncidentFeed from "./IncidentFeed.jsx";
import SourceHealth from "./SourceHealth.jsx";
import { formatAgo, formatUtc } from "./lib/format.js";
import { usePoll } from "./usePoll.js";

const IngestLag = lazy(() => import("./IngestLag.jsx"));
const PlanetDetail = lazy(() => import("./PlanetDetail.jsx"));
const RejectionRate = lazy(() => import("./RejectionRate.jsx"));
const SignatureBreakdown = lazy(() => import("./SignatureBreakdown.jsx"));

function Lazy({ children }) {
  return <Suspense fallback={<p className="note">Loading panel</p>}>{children}</Suspense>;
}

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
        <GalaxyView onSelect={setPlanet} selected={planet} meta={meta.data} />
        {planet ? (
          <Lazy>
            <PlanetDetail sectorId={planet} onClose={() => setPlanet(null)} />
          </Lazy>
        ) : null}
        <Lazy>
          <SignatureBreakdown />
        </Lazy>
        <SourceHealth />
        <Lazy>
          <IngestLag />
        </Lazy>
        <Lazy>
          <RejectionRate />
        </Lazy>
        <IncidentFeed meta={meta.data} />
        <DeploymentLog />
      </main>
    </div>
  );
}
