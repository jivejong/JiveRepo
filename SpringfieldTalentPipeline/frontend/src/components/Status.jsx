/**
 * The four render states, in one place so every panel behaves the same way.
 *
 * `empty` is deliberately separate from `error`: the API answers a no-match search with 200 and an
 * empty array, so "found nothing" is a valid outcome and must never render as a failure.
 *
 * `generating` exists because the AI calls take seconds against Groq. A spinner identical to a
 * 20ms fetch reads as a hang, so those two panels say what is happening and roughly how long it
 * takes.
 */
export function Status({ state, error, empty, generating, testId }) {
  if (state === 'loading') {
    return (
      <p className="status" data-testid={testId ? `${testId}-loading` : 'loading'}>
        {generating ? (
          <>
            <span className="pulse" aria-hidden="true" />
            {generating}
          </>
        ) : (
          'Loading…'
        )}
      </p>
    );
  }

  if (state === 'error') {
    return (
      <p className="status error" data-testid={testId ? `${testId}-error` : 'error'}>
        {error}
      </p>
    );
  }

  if (state === 'empty') {
    return (
      <p className="status" data-testid={testId ? `${testId}-empty` : 'empty'}>
        {empty}
      </p>
    );
  }

  return null;
}
