import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import { Status } from './Status.jsx';

/**
 * Stage A's search, kept as a secondary way into the pool now that ranked matches are the primary
 * route. An empty query legitimately returns all 1,182 candidates (~293KB), so the list renders at
 * most RENDER_LIMIT rows and reports the true total.
 */
const RENDER_LIMIT = 50;

export default function CandidateSearch() {
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [results, setResults] = useState([]);
  const [state, setState] = useState('loading');
  const [error, setError] = useState(null);

  const search = useCallback((term, signal) => {
    setState('loading');
    setError(null);
    return api
      .searchCandidates(term, signal)
      .then((data) => {
        setResults(data);
        setState(data.length === 0 ? 'empty' : 'ready');
      })
      .catch((cause) => {
        if (cause.name === 'AbortError') return;
        setError(cause.message);
        setResults([]);
        setState('error');
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    search(submitted, controller.signal);
    return () => controller.abort();
  }, [submitted, search]);

  const shown = results.slice(0, RENDER_LIMIT);

  return (
    <section className="panel">
      <h2>Candidate search</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setSubmitted(query.trim());
        }}
      >
        <div className="row">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search name or occupation, e.g. bartender"
            aria-label="Search candidates"
            data-testid="search-input"
          />
          <button type="submit" disabled={state === 'loading'} data-testid="search-button">
            Search
          </button>
        </div>
      </form>

      <Status state={state} error={error} empty={`No candidates match “${submitted}”.`} />

      {state === 'ready' && (
        <>
          <p className="count" data-testid="count">
            {results.length.toLocaleString()} {results.length === 1 ? 'candidate' : 'candidates'}
            {submitted ? ` matching “${submitted}”` : ' (all)'}
            {results.length > RENDER_LIMIT && ` — showing the first ${RENDER_LIMIT}`}
          </p>
          <ul data-testid="results">
            {shown.map((candidate) => (
              <li key={candidate.id} data-testid="result-row">
                <div className="name">{candidate.name}</div>
                <div className="occupation">{candidate.occupation ?? 'Occupation not on record'}</div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
