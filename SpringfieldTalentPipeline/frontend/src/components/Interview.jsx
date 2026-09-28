import { Status } from './Status.jsx';

/**
 * Step 4. On demand rather than automatic - this is the most expensive call in the app (a whole
 * interview in one generation, ~1,500-1,850 tokens on the 120b model) and firing it without being
 * asked would make every application cost one.
 *
 * Presentational: the generation state lives in App, because the Fit score page's "Generate
 * interview" button starts it before this page is even shown.
 */
export default function Interview({ candidate, interview, state, error, onGenerate }) {
  return (
    <section className="panel">
      <h2>4 · Mock interview</h2>
      <p className="hint">
        One structured generation produces the whole transcript — questions, in-character answers and
        a closing assessment.
      </p>

      <button type="button" onClick={onGenerate} disabled={state === 'loading'} data-testid="interview-run">
        {state === 'loading' ? 'Generating…' : interview ? 'Generate another' : 'Run mock interview'}
      </button>

      <Status
        state={state}
        error={error}
        generating={`Interviewing ${candidate.name} with gemini-3.1-flash-lite — this takes several seconds…`}
        testId="interview"
      />

      {state === 'ready' && interview && (
        <div data-testid="interview-result">
          <ol className="turns" data-testid="interview-turns">
            {interview.turns.map((turn) => (
              <li key={turn.questionNumber} data-testid="interview-turn">
                <p className="question">{turn.question}</p>
                <p className="answer">{turn.answer}</p>
              </li>
            ))}
          </ol>
          <div className="assessment">
            <h3>
              Assessment{' '}
              {interview.overallRating != null && (
                <span className="badge" data-testid="interview-rating">
                  {interview.overallRating}/5
                </span>
              )}
            </h3>
            <p data-testid="interview-assessment">{interview.overallAssessment}</p>
            <p className="meta">
              This is the model's read on the transcript it just wrote — not recruiter judgement.
              {` ${interview.modelUsed}`}
              {interview.tokens?.totalTokens ? ` · ${interview.tokens.totalTokens} tokens` : ''}
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
