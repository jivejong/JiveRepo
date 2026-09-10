import { useState } from 'react';
import { api } from './lib/api.js';
import RequisitionForm from './components/RequisitionForm.jsx';
import MatchList from './components/MatchList.jsx';
import FitScore from './components/FitScore.jsx';
import Interview from './components/Interview.jsx';
import PipelineActions from './components/PipelineActions.jsx';
import CandidateSearch from './components/CandidateSearch.jsx';
import { Status } from './components/Status.jsx';

/**
 * The seven-step flow, revealed progressively: each panel appears once the step before it has
 * produced the thing it needs. Nothing is routed - the whole story is one scrollable column, which
 * is what makes it demonstrable in a single screen recording.
 */
export default function App() {
  const [requisition, setRequisition] = useState(null);
  const [candidate, setCandidate] = useState(null);
  const [application, setApplication] = useState(null);
  const [applyState, setApplyState] = useState('idle');
  const [applyError, setApplyError] = useState(null);
  const [showSearch, setShowSearch] = useState(false);

  async function apply(match) {
    setApplyState('loading');
    setApplyError(null);
    setCandidate(match);
    try {
      const created = await api.createApplication(match.candidateId, requisition.id);
      setApplication({ ...created, requisitionTitle: requisition.title });
      setApplyState('idle');
    } catch (cause) {
      // A 409 here means this candidate already has an active application on this requisition,
      // which is a legitimate answer rather than a fault - say so plainly.
      setApplyError(
        cause.status === 409
          ? `${match.name} already has an active application for this requisition.`
          : cause.message,
      );
      setApplyState('error');
      setCandidate(null);
    }
  }

  function restart() {
    setRequisition(null);
    setCandidate(null);
    setApplication(null);
    setApplyState('idle');
    setApplyError(null);
  }

  return (
    <div className="app">
      <header>
        <h1>Springfield Talent Pipeline</h1>
        <p className="subtitle">
          Open a requisition, rank the pool against it, apply a candidate, score them, interview
          them, decide.
        </p>
      </header>

      {!requisition && <RequisitionForm onCreated={setRequisition} />}

      {requisition && (
        <section className="panel requisition-summary" data-testid="requisition-summary">
          <h2>1 · Requisition</h2>
          <p>
            <strong data-testid="requisition-title">{requisition.title}</strong>
            {requisition.department ? ` · ${requisition.department}` : ''}
            {requisition.hiringManager ? ` · ${requisition.hiringManager}` : ''}
          </p>
          <p className="hint">
            Target keywords: <code>{requisition.targetKeywords}</code> · status {requisition.status}
          </p>
          <button type="button" onClick={restart} className="link" data-testid="restart">
            Start over with a different requisition
          </button>
        </section>
      )}

      {requisition && (
        <MatchList
          requisition={requisition}
          onSelect={apply}
          selectedCandidateId={candidate?.candidateId}
          disabled={applyState === 'loading' || Boolean(application)}
        />
      )}

      {applyState === 'loading' && (
        <section className="panel">
          <Status state="loading" testId="apply" />
        </section>
      )}
      {applyState === 'error' && (
        <section className="panel">
          <Status state="error" error={applyError} testId="apply" />
        </section>
      )}

      {application && candidate && <FitScore application={application} candidate={candidate} />}
      {application && candidate && <Interview application={application} candidate={candidate} />}
      {application && candidate && (
        <PipelineActions application={application} candidate={candidate} />
      )}

      <section className="panel secondary">
        <button
          type="button"
          className="link"
          onClick={() => setShowSearch((open) => !open)}
          data-testid="toggle-search"
        >
          {showSearch ? 'Hide' : 'Show'} candidate search
        </button>
        {showSearch && <CandidateSearch />}
      </section>
    </div>
  );
}
