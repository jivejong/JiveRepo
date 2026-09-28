import { useRef, useState } from 'react';
import { api } from './lib/api.js';
import titleImage from '../images/Springfield.png';
import RequisitionForm from './components/RequisitionForm.jsx';
import MatchList from './components/MatchList.jsx';
import FitScore from './components/FitScore.jsx';
import Interview from './components/Interview.jsx';
import PipelineActions from './components/PipelineActions.jsx';
import CandidateSearch from './components/CandidateSearch.jsx';
import PageNav from './components/PageNav.jsx';
import { Status } from './components/Status.jsx';

/**
 * The five-step flow across four pages: requisition + applications (steps 1-2), fit score (3),
 * mock interview (4) and decision (5). Nothing is routed - `page` is plain state.
 *
 * Every page that has data stays mounted and the inactive ones are only hidden, never unmounted.
 * Unmounting would throw away the fit score, the interview transcript and the stage the decision
 * page has walked to whenever someone pressed Back, and remounting FitScore would fire a second
 * POST /ai-profile for an application that already has its one row.
 */
export default function App() {
  const [page, setPage] = useState('applications');
  const [requisition, setRequisition] = useState(null);
  const [candidate, setCandidate] = useState(null);
  const [application, setApplication] = useState(null);
  const [applyState, setApplyState] = useState('idle');
  const [applyError, setApplyError] = useState(null);
  const [showSearch, setShowSearch] = useState(false);

  // The interview lives here rather than in <Interview> because the fit score page's "Generate
  // interview" button starts it before the interview page is shown.
  const [interview, setInterview] = useState(null);
  const [interviewState, setInterviewState] = useState('idle');
  const [interviewError, setInterviewError] = useState(null);
  // Bumped on restart so a generation still in flight for the old application can't land on the
  // next one.
  const session = useRef(0);

  function go(next) {
    setPage(next);
    window.scrollTo(0, 0);
  }

  async function apply(match) {
    setApplyState('loading');
    setApplyError(null);
    setCandidate(match);
    try {
      const created = await api.createApplication(match.candidateId, requisition.id);
      setApplication({ ...created, requisitionTitle: requisition.title });
      setApplyState('idle');
      go('fit');
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

  async function generateInterview() {
    const mine = session.current;
    setInterviewState('loading');
    setInterviewError(null);
    try {
      const result = await api.mockInterview(application.id);
      if (mine !== session.current) return;
      setInterview(result);
      setInterviewState('ready');
    } catch (cause) {
      if (mine !== session.current) return;
      setInterviewError(cause.message);
      setInterviewState('error');
    }
  }

  /** Step 3's button. An existing transcript is shown as-is rather than paying for another. */
  function openInterview() {
    if (!interview && interviewState !== 'loading') generateInterview();
    go('interview');
  }

  function restart() {
    session.current += 1;
    setRequisition(null);
    setCandidate(null);
    setApplication(null);
    setApplyState('idle');
    setApplyError(null);
    setInterview(null);
    setInterviewState('idle');
    setInterviewError(null);
    go('applications');
  }

  const nav = {
    applications: {
      forward: application && { label: 'Continue to fit score →', onClick: () => go('fit') },
    },
    fit: {
      back: { label: '← Back to applications', onClick: () => go('applications') },
      forward: { label: 'Generate interview', onClick: openInterview },
    },
    interview: {
      back: { label: '← Back to fit score', onClick: () => go('fit') },
      forward: { label: 'Go to decision →', onClick: () => go('decision') },
    },
    decision: {
      back: { label: '← Back to interview', onClick: () => go('interview') },
    },
  }[page];

  return (
    <div className="app">
      <header>
        <h1 className="title">
          <img src={titleImage} alt="Springfield Talent Pipeline" />
        </h1>
        <p className="subtitle">
          Open a requisition, rank the pool against it, apply a candidate, score them, interview
          them, decide.
        </p>
      </header>

      <div hidden={page !== 'applications'} data-testid="page-applications">
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
      </div>

      {application && candidate && (
        <>
          <div hidden={page !== 'fit'} data-testid="page-fit">
            <FitScore application={application} candidate={candidate} />
          </div>
          <div hidden={page !== 'interview'} data-testid="page-interview">
            <Interview
              candidate={candidate}
              interview={interview}
              state={interviewState}
              error={interviewError}
              onGenerate={generateInterview}
            />
          </div>
          <div hidden={page !== 'decision'} data-testid="page-decision">
            <PipelineActions application={application} candidate={candidate} />
          </div>
        </>
      )}

      {(page !== 'applications' || requisition) && (
        <PageNav back={nav.back} forward={nav.forward} onRestart={restart} />
      )}

      <section className="panel secondary">
        <button
          type="button"
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
