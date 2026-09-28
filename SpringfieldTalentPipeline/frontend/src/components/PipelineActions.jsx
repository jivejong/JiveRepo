import { useState } from 'react';
import { ApiError, api, STAGE } from '../lib/api.js';
import { Status } from './Status.jsx';
import OfferForm, { OfferOutcome } from './OfferForm.jsx';

/**
 * Step 5. Extend an offer (walking the application to OFFER first) or reject.
 *
 * The second-transition probe below the outcome is commented out for now. Both HIRED and REJECTED
 * are terminal, so the state machine refuses anything after them and answers 409 with its own
 * account of the current stage and what remains reachable - an empty list. Rendering that body
 * verbatim was the proof; a caught error with a friendly message would throw away the evidence.
 */
export default function PipelineActions({ application, candidate, onStageChange }) {
  const [stage, setStage] = useState(application.currentStage);
  const [allowed, setAllowed] = useState(application.allowedNextStages ?? []);
  const [state, setState] = useState('idle');
  const [error, setError] = useState(null);
  const [rejection, setRejection] = useState(null);
  const [probing, setProbing] = useState(false);

  const [walked, setWalked] = useState([]);
  const [offer, setOffer] = useState(null);
  const terminal = stage === STAGE.HIRED || stage === STAGE.REJECTED;

  /**
   * The happy path, in order. HIRED is reachable only from OFFER, so "hire this candidate" is not
   * one call from SOURCED - it is a walk through every intermediate stage. Sending HIRED directly
   * is refused with a 409, correctly, which is why Extend offer advances one stage at a time
   * rather than pretending the pipeline can be skipped.
   */
  const ADVANCE_PATH = [STAGE.SOURCED, STAGE.SCREENING, STAGE.INTERVIEWING, STAGE.OFFER, STAGE.HIRED];

  /**
   * The walk deliberately stops at OFFER. The three hops before it are mechanical pipeline
   * movement; the last one needs a salary from a person, so it happens through the offer form
   * rather than being auto-completed. The form's submit runs the walk and then the offer.
   */
  const WALK_TARGET = STAGE.OFFER;

  async function move(toStage) {
    setState('loading');
    setError(null);
    try {
      const updated = await api.transition(application.id, toStage, `Set to ${toStage} from the demo UI`);
      setStage(updated.currentStage);
      setAllowed(updated.allowedNextStages ?? []);
      setState('idle');
      onStageChange?.(updated);
      return updated;
    } catch (cause) {
      setError(cause.message);
      setState('error');
      throw cause;
    }
  }

  /**
   * Advances through every remaining stage up to OFFER, one legal transition at a time. Resolves
   * true once the application is at OFFER (immediately, if it already is) and false if a hop failed
   * - the failure has already been put on screen by move().
   */
  async function advanceToOffer() {
    const from = ADVANCE_PATH.indexOf(stage);
    if (from === -1) {
      setError(`${stage} is not on the path to OFFER.`);
      setState('error');
      return false;
    }
    const remaining = ADVANCE_PATH.slice(from + 1, ADVANCE_PATH.indexOf(WALK_TARGET) + 1);
    setWalked([]);
    for (const next of remaining) {
      try {
        // Sequential on purpose: each transition's legality depends on the one before it landing.
        await move(next);
        setWalked((done) => [...done, next]);
      } catch {
        return false;
      }
    }
    return true;
  }

  /** Deliberately illegal: HIRED and REJECTED are terminal, so this must be refused. */
  async function probeSecondTransition() {
    setProbing(true);
    setRejection(null);
    const attempt = stage === STAGE.HIRED ? STAGE.REJECTED : STAGE.HIRED;
    try {
      await api.transition(application.id, attempt, 'Second transition — expected to be refused');
      setRejection({ unexpected: `The API allowed ${stage} → ${attempt}, which should be impossible.` });
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setRejection({ attempt, ...cause.body });
      } else {
        setRejection({ unexpected: cause.message });
      }
    } finally {
      setProbing(false);
    }
  }

  return (
    <section className="panel">
      <h2>5 · Decision</h2>
      <p className="hint">
        Current stage: <strong data-testid="current-stage">{stage}</strong>
        {allowed.length > 0 && ` · reachable: ${allowed.join(', ')}`}
        {allowed.length === 0 && ' · terminal, nothing reachable'}
      </p>

      {!terminal && !offer && (
        <OfferForm
          application={application}
          candidate={candidate}
          onBeforeSubmit={advanceToOffer}
          disabled={state === 'loading'}
          onDecided={(result) => {
            setOffer(result);
            setStage(result.currentStage);
            setAllowed(result.allowedNextStages ?? []);
          }}
        />
      )}

      {!terminal && !offer && (
        <div className="actions">
          <button
            type="button"
            onClick={() => move(STAGE.REJECTED).catch(() => {})}
            disabled={state === 'loading'}
            data-testid="reject"
          >
            Reject
          </button>
        </div>
      )}

      <Status state={state} error={error} testId="transition" />

      {walked.length > 0 && (
        <p className="hint" data-testid="walked-path">
          Walked: {[application.currentStage, ...walked].join(' → ')}
          {' · '}each hop is a separate legal transition; the walk stops at OFFER, where the offer
          itself is submitted.
        </p>
      )}

      {offer && <OfferOutcome offer={offer} />}

      {/*
        Hidden for now, and may come back: the "HIRED is terminal" section. It attempts a second
        transition from a terminal stage and renders the state machine's 409 body verbatim.
        probeSecondTransition, `probing` and `rejection` above are still live, so restoring this
        block is a single uncomment.

      {terminal && (
        <div className="probe">
          <p className="hint">
            {stage} is terminal. Try moving again and the state machine should refuse with a 409.
          </p>
          <button type="button" onClick={probeSecondTransition} disabled={probing} data-testid="probe-409">
            {probing ? 'Attempting…' : `Attempt ${stage} → ${stage === STAGE.HIRED ? 'REJECTED' : 'HIRED'}`}
          </button>

          {rejection && !rejection.unexpected && (
            <div className="rejected" data-testid="rejection-409">
              <p className="rejected-code">409 Conflict — refused by the state machine</p>
              <p data-testid="rejection-error">{rejection.error}</p>
              <dl>
                <dt>currentStage</dt>
                <dd data-testid="rejection-current">{rejection.currentStage}</dd>
                <dt>requestedStage</dt>
                <dd data-testid="rejection-requested">{rejection.requestedStage}</dd>
                <dt>allowedNextStages</dt>
                <dd data-testid="rejection-allowed">
                  {rejection.allowedNextStages?.length ? rejection.allowedNextStages.join(', ') : '(none)'}
                </dd>
              </dl>
            </div>
          )}

          {rejection?.unexpected && (
            <p className="status error" data-testid="rejection-unexpected">
              {rejection.unexpected}
            </p>
          )}
        </div>
      )}
      */}
    </section>
  );
}
