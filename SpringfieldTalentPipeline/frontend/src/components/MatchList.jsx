import { useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import { Status } from './Status.jsx';

/**
 * Step 2. Ranked candidates for a requisition.
 *
 * The order is the API's, untouched. `ts_rank` scores on match count and document length and reads
 * no corpus statistics, so a shorter occupation string can legitimately rank above a longer one
 * matching the same term. Re-sorting here would hide the behaviour this screen exists to show, so
 * the rank is printed alongside each row and nothing is reordered.
 */
export default function MatchList({ requisition, onSelect, selectedCandidateId, disabled }) {
  const [matches, setMatches] = useState([]);
  const [state, setState] = useState('loading');
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    setState('loading');
    setError(null);
    api
      .matches(requisition.id, 20, controller.signal)
      .then((data) => {
        setMatches(data);
        setState(data.length === 0 ? 'empty' : 'ready');
      })
      .catch((cause) => {
        if (cause.name === 'AbortError') return;
        setError(cause.message);
        setState('error');
      });
    return () => controller.abort();
  }, [requisition.id]);

  return (
    <section className="panel">
      <h2>2 · Ranked matches</h2>
      <p className="hint">
        “{requisition.targetKeywords}” · full-text search over candidate occupations, ordered by{' '}
        <code>ts_rank</code> exactly as the API returned it
      </p>

      <Status
        state={state}
        error={error}
        empty={`No candidate occupation matches “${requisition.targetKeywords}”.`}
        testId="matches"
      />

      {state === 'ready' && (
        <>
          <p className="count" data-testid="matches-count">
            {matches.length} ranked {matches.length === 1 ? 'match' : 'matches'}
          </p>
          <ol className="matches" data-testid="matches-list">
            {matches.map((match) => (
              <li key={match.candidateId} data-testid="match-row">
                <div className="match-main">
                  <span className="name">{match.name}</span>
                  <span className="occupation">{match.occupation ?? 'Occupation not on record'}</span>
                </div>
                <span className="rank" title="ts_rank score">
                  {match.rank.toFixed(5)}
                </span>
                <button
                  type="button"
                  onClick={() => onSelect(match)}
                  disabled={disabled || selectedCandidateId === match.candidateId}
                  data-testid={`apply-${match.externalId}`}
                >
                  {selectedCandidateId === match.candidateId ? 'Applied' : 'Apply'}
                </button>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
