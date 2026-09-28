# Frontend

Vite + React front end for the Springfield Talent Pipeline API. Four pages walk the whole recruiting
story, with Back / forward buttons and a New requisition button on each:

1. **Requisition + applications** (steps 1–2): open a requisition, rank the candidate pool against it,
   apply someone.
2. **Fit score** (step 3): **Generate interview** starts the mock interview and moves on.
3. **Mock interview** (step 4): **Go to decision**.
4. **Decision** (step 5): **Extend offer** (walks the application to `OFFER`, then submits the
   salary) or **Reject**.

There is no router; the page is plain state in `App.jsx`. Inactive pages stay mounted and are only
hidden, so going back keeps the fit score, the interview transcript and the stage, and never fires a
second Gemini call. The "HIRED is terminal" section (which attempted a second transition and showed
the 409) is commented out in `PipelineActions.jsx` and can be restored by uncommenting it.

## Running

The Spring Boot API must be running on port 8080 first (`../gradlew bootRun`).

```bash
npm install
npm run dev          # http://localhost:5173
```

## Verifying

```bash
npm run verify         # Stage A: candidate search
npm run verify:flow    # Stage B: the full paged flow, including live Gemini calls
npm run verify:offer   # Offer stage: accepted, declined and fallback cases, live Gemini calls
```

This is the point of the stage: `vite build` succeeding proves nothing about whether the app renders
or can reach the API. `scripts/verify.mjs` launches headless Chromium, reads assertions out of the
live DOM after React commits, and captures screenshots to `screenshots/`. It also fails on any
browser console error or failed request.

`scripts/verify-error.mjs` covers the paths that need the backend to misbehave: connection refused
and a 500: by intercepting at the browser rather than by actually stopping Postgres.

Requires Node `^20.19 || >=22.12` (Vite 8). Run `npx playwright install chromium` once.

## What the verification covers

`verify.mjs`: search (opened via the "Show candidate search" button): the 50-row cap, a real query, a
no-match query, recovery, browser health.
`verify-error.mjs`: the error paths, by intercepting at the browser: connection refused and a 500.
`verify-stage-b.mjs`: the full flow against the live backend and **real Gemini generations**: create
requisition, ranked matches, apply, fit score, interview, back/forward keeping results with no
second Gemini call, extend offer, and New requisition. The 409-refusal step is commented out along
with the UI section it drove.

## Things learned the hard way

- **`toStage` must be the upper-case enum constant.** `"HIRED"`, not `"Hired"`: Jackson matches
  enum names exactly and rejects the title-case form with a 400 before the state machine ever runs.
- **Hiring is not one call.** `HIRED` is reachable only from `OFFER`, so **Extend offer** first walks
  `SOURCED → SCREENING → INTERVIEWING → OFFER` as three sequential transitions, then submits the
  offer, and an accepted offer lands on `HIRED`. Sending `HIRED` straight from `SOURCED` is
  correctly refused with a 409.
- **Match rows key on `candidateId`, not `id`.** `MatchResponse` and `CandidateResponse` are
  different shapes.
- **Wait on committed renders.** A `waitForSelector` can match the *previous* render's list and let
  assertions read stale DOM. Every step waits for the API response it triggered and then for that
  panel's loading indicator to detach.
- **Chromium logs any non-2xx fetch as a console error.** The deliberate 409 trips a naive
  "no console errors" check, so it is filtered explicitly and asserted to occur exactly once;
  the same treatment as StrictMode's `ERR_ABORTED`.

- **Auto-triggered POSTs need a ref guard, not just an AbortController.** StrictMode double-invokes
  effects in dev. Aborting cancels the browser's interest in the response, but the request has
  already gone to the server, which processes it anyway. For a GET that is harmless; for a POST that
  creates a row it means two writes racing for one unique constraint. `FitScore` keeps a
  `requestedFor` ref so the second request is never sent: and clears it on failure so a genuine
  error can still be retried.
- **Assert on requests made, not only on what rendered.** The double-fire bug still showed a correct
  score, because one of the two requests succeeded. `verify-stage-b.mjs` counts POSTs to
  `/ai-profile` and asserts exactly one.

## Conventions

- **The proxy is the CORS answer.** `vite.config.js` forwards `/api` to `localhost:8080`, so the
  browser only ever makes same-origin requests. No `@CrossOrigin` on the backend, no preflight.
  Keep new calls on relative paths (`/api/...`) and they inherit this for free.
- **Four render states, not three.** loading / error / empty / results. The API returns `200` with
  `[]` for a no-match search, so "found nothing" must not route through the error branch: error is
  only for a rejected fetch or a non-OK status.
- **An empty query returns all 1,182 candidates (~293KB).** The list renders at most
  `RENDER_LIMIT` (50) rows and reports the true total separately. Any Stage B list over a large
  collection needs the same treatment.
- **Requests are abortable.** The effect cleanup cancels in-flight requests, so a fast retype does
  not race. `AbortError` is swallowed deliberately; in dev, React StrictMode's double-invoked effect
  makes one `ERR_ABORTED` per mount, which is expected and filtered out of the verification.
- **Assert on committed renders, not on selectors alone.** The first verification run failed
  spuriously because the previous result list was still in the DOM at click time. `searchFor()`
  waits for the API response *and* for the loading indicator to detach.
