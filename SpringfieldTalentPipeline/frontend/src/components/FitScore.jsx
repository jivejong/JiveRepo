import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api.js';
import { Status } from './Status.jsx';

/**
 * Steps 3-4. The application exists by the time this mounts; this fires the fit score immediately.
 *
 * This is a real Groq call taking seconds, so the loading copy says "generating" and names the
 * model. A generic spinner here is indistinguishable from a hung request.
 */
export default function FitScore({ application, candidate }) {
  const [profile, setProfile] = useState(null);
  const [state, setState] = useState('loading');
  const [error, setError] = useState(null);

  /**
   * Which applicationId this component has already requested a profile for.
   *
   * React StrictMode double-invokes effects in development, and an AbortController is not enough
   * here: aborting cancels the browser's interest in the response, but the request has already been
   * sent and the server still processes it. Two generations then race to insert the one row allowed
   * per application, and the loser used to surface as a 500. This ref stops the second request from
   * ever being sent. It is a ref rather than state precisely because updating it must not trigger
   * another render.
   */
  const requestedFor = useRef(null);

  useEffect(() => {
    if (requestedFor.current === application.id) return undefined;
    requestedFor.current = application.id;

    const controller = new AbortController();
    setState('loading');
    setError(null);
    api
      .aiProfile(application.id, { refresh: false }, controller.signal)
      .then((data) => {
        setProfile(data);
        setState('ready');
      })
      .catch((cause) => {
        if (cause.name === 'AbortError') return;
        // Allow a retry if this genuinely failed, rather than latching the guard forever.
        requestedFor.current = null;
        setError(cause.message);
        setState('error');
      });
    // No abort on cleanup: the request is in flight server-side regardless, and the guard above
    // means this effect body runs once per application, so there is nothing stale to cancel.
    return undefined;
  }, [application.id]);

  return (
    <section className="panel">
      <h2>3 · Fit score</h2>
      <p className="hint">
        {candidate.name} against {application.requisitionTitle ?? 'this requisition'}
      </p>

      <Status
        state={state}
        error={error}
        generating="Generating profile with openai/gpt-oss-20b — a few seconds…"
        testId="fit"
      />

      {state === 'ready' && profile && (
        <div data-testid="fit-result">
          <div className="score-row">
            <span className="score" data-testid="fit-score">
              {profile.fitScore}
            </span>
            <span className="score-of">/ 100</span>
            <span className="badge" data-testid="fit-origin">
              {profile.cached ? 'from cache' : 'generated'}
            </span>
          </div>
          <p className="rationale" data-testid="fit-rationale">
            {profile.fitRationale}
          </p>
          {profile.bio && <p className="bio">{profile.bio}</p>}
          <p className="meta">
            {profile.modelUsed}
            {profile.tokens?.totalTokens ? ` · ${profile.tokens.totalTokens} tokens` : ''}
            {` · ${profile.origin}`}
          </p>
        </div>
      )}
    </section>
  );
}
