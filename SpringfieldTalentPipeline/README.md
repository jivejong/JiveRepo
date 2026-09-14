# Springfield Talent Pipeline

A Spring Boot REST API modeling a full recruiting pipeline (ATS) — sourcing, stage tracking, and interview feedback — backed by Simpsons character data instead of real candidate PII. AI features generate a per-role candidate profile/fit score and run a structured mock interview in character, grounded in each candidate's actual API-sourced data.

It demonstrates enterprise Java idioms end to end: domain modeling with a state machine that actually enforces its transition table, Postgres full-text search, and structured LLM generation parsed against a JSON schema rather than scraped out of prose. Every feature was verified against real systems — the live Simpsons API, a real Postgres instance, real Groq calls — and several of them behaved in ways that are easy to get wrong. Those findings are written up inline, next to the code they explain.

**Contents:** [Quick start](#quick-start) · [Stack](#stack) · [Prerequisites](#prerequisites) ·
[Configuration](#configuration) · [Running](#running) · [API reference](#api-reference) ·
[Data model](#data-model) · [Pipeline stages](#pipeline-stages) ·
[Syncing](#syncing-the-candidate-pool) · [Finding candidates](#finding-candidates) ·
[Requisitions & matching](#requisitions-matching-and-the-pipeline) ·
[AI profile](#ai-candidate-profile) · [Mock interviews](#mock-interviews-and-recruiter-feedback) ·
[Offer stage](#offer-stage) · [Demo frontend](#demo-frontend) · [Tests](#tests) · [Attribution](#attribution-and-licensing)

## Quick start

With JDK 21 installed, Postgres running, and `config/local.yml` filled in (all three below):

```bash
./gradlew bootRun                                     # macOS / Linux
.\gradlew.bat bootRun                                 # Windows

curl http://localhost:8080/actuator/health            # expect "status":"UP" with db also UP
curl -X POST http://localhost:8080/api/candidates/sync  # populate the pool, ~20s, first run only
curl "http://localhost:8080/api/candidates?q=szyslak"
```

## Stack

- Java 21, Spring Boot 3.5.16, built with Gradle 9.7.1 via the wrapper (no local Gradle install needed)
- Spring Web, Spring Data JPA, PostgreSQL
- Spring Statemachine 4.0.2 for the pipeline stage transitions (this is what pins Boot to the 3.5 line — see the note at the top of `build.gradle`)
- Groq API for both AI features
- BLS OEWS national wage data, seeded locally, for the offer decision — reference data, not a runtime dependency
- React 19 + Vite 8 demo front end in `frontend/`, verified in real Chromium with Playwright

## Prerequisites

1. **JDK 21** on the `PATH` (or a `JAVA_HOME` pointing at one). Gradle's toolchain support will
   resolve a 21 toolchain if one is available; installing a JDK 21 outright is simpler.
2. **PostgreSQL** running locally with a `springfield` database. Via Docker:
   ```bash
   docker run -d --name springfield-postgres -p 5432:5432 -e POSTGRES_DB=springfield -e POSTGRES_PASSWORD=<your-password> postgres:16
   ```
   Or against an existing server: `CREATE DATABASE springfield;`
3. **A Groq API key** — free tier, from console.groq.com. Only the two AI features need it;
   candidate sync, matching and the pipeline all run without one.

## Configuration

No secret or machine-specific value is committed. There are two ways to supply them, and the precedence between them is **environment variable > `config/local.yml` > defaults in [`application.yml`](src/main/resources/application.yml)** (verified, not assumed).

### Local development: `config/local.yml`

Copy the template once and you never have to set a shell variable again:

```bash
cp config/local.yml.example config/local.yml
```

Then fill in your Postgres password and, if you want the AI features, your Groq key. `config/local.yml` is gitignored; `config/local.yml.example` is the committed template. `application.yml` pulls it in
with `spring.config.import: optional:file:./config/local.yml` — the `optional:` prefix means a fresh clone or a CI runner without that file still starts normally.

### CI / one-off overrides: environment variables

| Variable       | Default                                        | Purpose                              |
| -------------- | ---------------------------------------------- | ------------------------------------ |
| `DB_URL`       | `jdbc:postgresql://localhost:5432/springfield` | JDBC URL                             |
| `DB_USERNAME`  | `postgres`                                     | database user                        |
| `DB_PASSWORD`  | `postgres`                                     | database password                    |
| `GROQ_API_KEY` | _(empty)_                                      | Groq API key — **never commit this** |

These override `config/local.yml`, so CI can inject credentials without a file on disk.

The Groq key is bound into `GroqProperties`; an unset key leaves the app running normally with `isConfigured()` false. Everything except the two AI endpoints works without one.

## Running

```bash
./gradlew bootRun          # macOS / Linux
.\gradlew.bat bootRun      # Windows
```

Then check the app is up and actually talking to Postgres:

```bash
curl http://localhost:8080/actuator/health
```

A healthy response reports `"status":"UP"` with a `db` component also `UP`:

```json
{"status":"UP","components":{"db":{"status":"UP","details":{"database":"PostgreSQL",
"validationQuery":"isValid()"}},"diskSpace":{"status":"UP", ...},"ping":{"status":"UP"}}}
```

If Postgres is not reachable the overall status is `DOWN` with the `db` component naming the failure — which is the point of exposing details in local development.

`GET /` returns 404 — there is no root resource, by design.

## API reference

### Candidates

| Method | Path                   | Purpose                                                                                        |
| ------ | ---------------------- | ---------------------------------------------------------------------------------------------- |
| `POST` | `/api/candidates/sync` | Full sync from The Simpsons API; upserts on `externalId`, ~20s                                 |
| `GET`  | `/api/candidates?q=`   | Search/list. Case-insensitive substring on name and occupation; whole pool when `q` is omitted |

### Requisitions and matching

| Method  | Path                                    | Purpose                                                                                                 |
| ------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `POST`  | `/api/requisitions`                     | Create a job opening                                                                                    |
| `GET`   | `/api/requisitions/{id}`                | Read one                                                                                                |
| `PATCH` | `/api/requisitions/{id}`                | Partial update; omitted fields are left alone. Changing `targetKeywords` marks cached AI profiles stale |
| `GET`   | `/api/requisitions/{id}/matches?limit=` | Full-text-search-ranked candidate matches                                                               |

### Applications and the pipeline

| Method | Path                                | Purpose                                            |
| ------ | ----------------------------------- | -------------------------------------------------- |
| `POST` | `/api/applications`                 | Create an application (candidate ↔ requisition)    |
| `GET`  | `/api/applications/{id}`            | Read one, including the stages currently reachable |
| `POST` | `/api/applications/{id}/transition` | Move to a new stage                                |
| `GET`  | `/api/applications/{id}/history`    | Audit trail, oldest first                          |

### AI features

| Method | Path                                         | Purpose                                             |
| ------ | -------------------------------------------- | --------------------------------------------------- |
| `POST` | `/api/applications/{id}/ai-profile?refresh=` | Generate or return the cached profile + fit score   |
| `GET`  | `/api/applications/{id}/ai-profile`          | Cached profile only; never calls the model          |
| `POST` | `/api/applications/{id}/mock-interview`      | Generate an interview; each call **adds** a session |
| `GET`  | `/api/applications/{id}/mock-interviews`     | Every attempt for this application, newest first    |
| `GET`  | `/api/mock-interviews/{sessionId}`           | One full transcript                                 |

### Offer

| Method | Path                           | Purpose                                                                                            |
| ------ | ------------------------------ | -------------------------------------------------------------------------------------------------- |
| `POST` | `/api/applications/{id}/offer` | Extend a salary offer. Legal only at `OFFER`; accepting lands on `HIRED`, declining on `WITHDRAWN` |
| `GET`  | `/api/applications/{id}/offer` | The offer decision for this application, if one was made                                           |

### Recruiter feedback

| Method | Path                              | Purpose                                     |
| ------ | --------------------------------- | ------------------------------------------- |
| `POST` | `/api/applications/{id}/feedback` | The human's own rating and comments         |
| `GET`  | `/api/applications/{id}/feedback` | Feedback for this application, newest first |

There is no `GET /api/requisitions` list endpoint — requisitions are read by id.

### Status codes

| Code  | When                                                                                                                                                                                             |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `409` | An invalid stage transition (the body carries the reachable stages), an offer on an application that is not at `OFFER`, or a second _active_ application for the same candidate/requisition pair |
| `502` | The Simpsons API or Groq is unreachable — the fault is upstream, not the caller's                                                                                                                |
| `400` | Missing `targetKeywords` or `toStage`, or a feedback rating outside 1–5                                                                                                                          |
| `404` | Unknown id, or a cached AI profile that has never been generated                                                                                                                                 |

## Data model

Eight entities. UUID primary keys throughout.

**`Candidate`** — synced from The Simpsons API, never hand-entered. `externalId` (the API's own character id) carries a unique constraint and is what makes a re-sync an update rather than a duplicate. `occupation` is free text and does the heavy lifting for matching; it is populated for all 1,182 characters. `age` is null for 94% of them and is never defaulted to 0. `phrases` is an ordered element collection, empty for 77%, and JPA maps it to a separate candidate_phrase`table rather than a column — so`SELECT phrases FROM candidate`will tell you no such column exists; join`candidate_phrase`on`candidate_id`and order by`phrase_order`instead.`characterStatus`is deliberately not called`status`, so it can never be confused with a pipeline stage.

**`Requisition`** — a job opening. `targetKeywords` is the full-text query used for matching.
`keywordsUpdatedAt` exists so a cached AI profile can tell whether it was scored against a roledescription that has since changed. It advances only when the text genuinely differs, so re-saving
an unchanged requisition does not invalidate every profile hanging off it.

**`JobApplication`** — the candidate ↔ requisition join, and the actual pipeline record. Named `JobApplication` rather than `Application` to avoid colliding with SpringfieldTalentPipelineApplication`and Spring's own`ApplicationContext`vocabulary.`currentStage` is only ever changed through the state machine.

**`StageTransition`** — the audit log, one row per move, written from a state machine listener. `fromStage` is null only on the synthetic row recording an application's creation.

**`AiCandidateProfile`** — one per application, because fit is requisition-specific rather than a property of a candidate alone. Overwritten in place on regeneration.

**`MockInterviewSession`** / **`MockInterviewTurn`** — one session per generation, each holding 5–8 Q&A turns. Unlike the profile, regenerating **adds** a session so attempts can be compared. A failed
generation is recorded with status `FAILED` rather than discarded, so a run of failures is visible.

**`OccupationWage`** / **`OfferDecision`** — national wage reference data keyed by SOC code, and the record of one salary offer. The decision stores the matched occupation and the wage band _as they
were at the time_, so a past decision can still explain itself after the reference data is re-imported. Its rationale is templated rather than generated, because it describes arithmetic that
already happened.

**`RecruiterFeedback`** — the human's verdict. Lives in the `pipeline` package rather than `ai`, which is the point: it is the one judgement in the system that no model produced.

## Pipeline stages

```
Sourced → Screening → Interviewing → Offer → Hired
                                   ↘
                 (any active stage) → Rejected / Withdrawn
```

| From                               | Allowed to                              |
| ---------------------------------- | --------------------------------------- |
| `SOURCED`                          | `SCREENING`, `REJECTED`, `WITHDRAWN`    |
| `SCREENING`                        | `INTERVIEWING`, `REJECTED`, `WITHDRAWN` |
| `INTERVIEWING`                     | `OFFER`, `REJECTED`, `WITHDRAWN`        |
| `OFFER`                            | `HIRED`, `REJECTED`, `WITHDRAWN`        |
| `HIRED` / `REJECTED` / `WITHDRAWN` | _(terminal)_                            |

`HIRED` is reachable only from `OFFER`. There is no backward path — a mis-transition is corrected by an admin action, not by allowing moves that would make the audit trail meaningless.

The last hop is not a button. At `OFFER` the pipeline waits for a salary, and the candidate's response decides between `HIRED` and `WITHDRAWN` — see [Offer stage](#offer-stage).

## Syncing the candidate pool

```bash
curl -X POST http://localhost:8080/api/candidates/sync
```

Pulls all ~1,182 characters and upserts them on `externalId`, so re-running updates rather than duplicates. It walks 60 pages serially with a 200ms pause between them — the API publishes no rate limit, but
firing ~60 requests concurrently at a free service is not being a good citizen — which puts a full run at roughly 20 seconds.

The response is a summary rather than a bare 200 — bad records are isolated and reported, never allowed to fail the batch:

```json
{
  "startedAt": "...",
  "durationMs": 19275,
  "pagesExpected": 60,
  "pagesFetched": 60,
  "pagesFailed": 0,
  "recordsSeen": 1182,
  "created": 1182,
  "updated": 0,
  "failed": 0,
  "failures": []
}
```

If The Simpsons API is unreachable the endpoint returns **502**, not 500 — the fault is upstream.

### Two things about the upstream API worth knowing

Both were established against the live API rather than taken from its published examples:

1. **`status` has seven values, and `Dead` is not one of them.** The API returns `Alive` (855),
   `Deceased` (136), `Unknown` (126), `Fictional` (50), `Noncanon` (9), `Noncanon Deceased` (4) and
   `Destroyed Icon` (2). Modelling only the three obvious ones would misfile 201 records — 17% of
   the pool. `CharacterStatus` covers all seven and falls back to `UNKNOWN` with a logged warning,
   so a value added upstream costs one record's fidelity instead of failing its import.
2. **The portrait CDN base is `https://cdn.thesimpsonsapi.com/{width}`**, with widths 200, 500 and
   1280 (others return 403). The API documents that images are "served via CDN" without publishing
   the base URL, so it was confirmed against the live CDN rather than guessed. It is configurable as
   `simpsons.cdn-base-url`, defaulting to the 500px variant, and the raw `portrait_path` is what
   gets stored — so a CDN change is a config edit, not a re-sync.

## Finding candidates

```bash
curl "http://localhost:8080/api/candidates"                 # whole pool, alphabetical (1,182)
curl "http://localhost:8080/api/candidates?q=burns"         # name or occupation, case-insensitive
curl "http://localhost:8080/api/candidates?q=bartender"
```

Plain substring matching (`ILIKE`), so `szys` finds the Szyslaks and partial words work. Each record carries a resolved `portraitUrl`. No matches is an empty array, not a 404.

This is **not** the same as `/api/requisitions/{id}/matches`, and deliberately so - that endpoint ranks by relevance with full-text search, this one just finds people. A multi-word `q` here is one
literal substring, so `q=bartender tavern` matches nothing.

## Requisitions, matching and the pipeline

```bash
# Create a job opening. targetKeywords is the full-text search query used for matching.
curl -X POST http://localhost:8080/api/requisitions \
  -H 'Content-Type: application/json' -d '{"title":"Bartender","department":"Food & Beverage","targetKeywords":"bartender tavern bar drinks","hiringManager":"Marge Simpson"}'

# Ranked candidate matches
curl "http://localhost:8080/api/requisitions/{id}/matches?limit=10"

# Edit a requisition. Omitted fields are left alone. Changing targetKeywords re-ranks matching
# AND marks every cached AI profile for this requisition stale.
curl -X PATCH http://localhost:8080/api/requisitions/{id} \
  -H 'Content-Type: application/json' -d '{"targetKeywords":"mixologist cocktails craft spirits"}'

# Create an application, then move it through the pipeline
curl -X POST http://localhost:8080/api/applications \
  -H 'Content-Type: application/json' -d '{"candidateId":"...","requisitionId":"..."}'
curl -X POST http://localhost:8080/api/applications/{id}/transition \
  -H 'Content-Type: application/json' -d '{"toStage":"SCREENING","note":"Phone screen booked"}'

curl http://localhost:8080/api/applications/{id}/history
```

### Matching

Postgres full-text search over `candidate.occupation`, ranked with `ts_rank` and backed by a GIN index. For "bartender tavern bar drinks", Moe Szyslak ranks first — he is the only candidate
matching two query terms.

`ts_rank` is called with normalisation flag `1` (divide by `1 + log(document length)`), and that is load-bearing: without it every candidate matching a single query term scores _identically_, so a
long unrelated occupation containing one generic keyword ties with a short exact match and ordering falls back to alphabetical. Two better-sounding fixes were measured and rejected. **`ts_rank_cd`** (cover density) changes nothing: it scores how close matched terms sit to each other, and when every document matches a single term there is no distance to measure — all results came back at exactly 0.1. **IDF weighting**
would be new machinery rather than a tuning flag, since `ts_rank` reads no corpus statistics at all, and it backfires at this corpus size anyway: "servic" appears in 1 of 1,182 occupations and
"bartend" in 4, so weighting by inverse document frequency would rank a babysitting receptionist above every bartender.

**Known limitation:** among candidates matching the same number of query terms, document length is the only tiebreaker — which term matched contributes nothing to the score. "Owner of Barney's
Bowlarama" matching "owner" and "Bartender at Moho House" matching "bartender" score identically, so an actual bartender can sort below "Owner of Virgin" for a bartending role purely because that
string is shorter. Ranking reflects only what the keywords distinguish.

Terms are OR-ed, not AND-ed. `plainto_tsquery` joins terms with AND, which returns **zero** matches for that query, since no occupation contains all four words. Matching is a ranked "who is closest"
question rather than a filter, so the parsed query is re-joined with OR — still going through `plainto_tsquery` first for stemming, stop-word removal, and safety against arbitrary typed input.

### The state machine

Transitions are enforced by Spring Statemachine, and the allowed table is declared in exactly one place ([`PipelineStateMachineConfig`](src/main/java/com/jivejong/springfieldtalentpipeline/pipeline/PipelineStateMachineConfig.java)).
An invalid move returns **409** with the current stage and what is actually reachable:

```json
{
  "error": "HIRED is a terminal stage; no transition to SCREENING (or anything else) is possible",
  "currentStage": "HIRED",
  "requestedStage": "SCREENING",
  "allowedNextStages": []
}
```

That `allowedNextStages` list is read back out of the machine's own configuration rather than written down a second time, so the error message cannot drift from what is enforced.

Every successful move writes a `stage_transition` audit row, recorded from a state machine listener — so rejected attempts write nothing. One active application per candidate/requisition pair is
enforced in the service layer rather than as a unique constraint, deliberately: re-applying after a rejection or withdrawal has to stay possible.

## AI candidate profile

Needs `GROQ_API_KEY` (or `groq.api-key` in `config/local.yml`).

```bash
curl -X POST http://localhost:8080/api/applications/{id}/ai-profile
curl -X POST "http://localhost:8080/api/applications/{id}/ai-profile?refresh=true"
curl http://localhost:8080/api/applications/{id}/ai-profile   # cached only, 404 if never generated
```

The response says why it did or did not call the model - `CACHED`, `GENERATED_FIRST_TIME`, `REGENERATED_KEYWORDS_CHANGED`, `REGENERATED_ON_REQUEST` - and reports token usage when a
generation actually happened. Roughly 830 tokens per profile on `openai/gpt-oss-20b`.

`PATCH`ing a requisition's `targetKeywords` is what makes its cached profiles stale; the next request regenerates instead of serving cache. Re-submitting the same keywords is a no-op and costs nothing.

Scores are requisition-specific, which is the whole point: Moe Szyslak scores 95 for Bartender and 25 for Nuclear Safety Inspector; Homer scores 55 for the nuclear role and 20 for bartending.

**If you change the scoring prompt, keep the scoring bands.** Asking for a 0-100 score without saying what the numbers mean made the same candidate score anywhere from 20 to 70 across runs.
Temperature was not the cause — 0.4 to 0.1 barely moved it (still 20–65). The model was re-inventing the scale on every call. Explicit bands fixed it, and the same case now returns 55 five
times running:

| Band   | Meaning                                                                               |
| ------ | ------------------------------------------------------------------------------------- |
| 90–100 | the candidate's occupation **is** this role, or names its primary keyword directly    |
| 70–89  | closely adjacent — the core skill transfers, little ramp-up needed                    |
| 40–69  | partial overlap — right field or one significant keyword, missing the specific domain |
| 10–39  | unrelated occupation, but a trainable working adult                                   |
| 0–9    | no relevant background at all                                                         |

Instructing the model to pick the _lower_ band when a candidate sits between two matters as much as the bands themselves.

**On VPNs:** Groq blocks datacenter and VPN IP ranges with a 403 and "Access denied. Please check your network settings." If you are tunnelled, that is the cause - the key is fine.

## Mock interviews and recruiter feedback

```bash
# Generate an interview. One structured call produces the whole thing.
curl -X POST http://localhost:8080/api/applications/{id}/mock-interview

# Every attempt for this application, newest first
curl http://localhost:8080/api/applications/{id}/mock-interviews

# One full transcript (costs no tokens - it is stored)
curl http://localhost:8080/api/mock-interviews/{sessionId}

# Your own verdict on it. sessionId is optional.
curl -X POST http://localhost:8080/api/applications/{id}/feedback \
  -H 'Content-Type: application/json' -d '{"sessionId":"...","rating":1,"comments":"AI was generous."}'
```

**Each generation adds a session rather than replacing the last one**, so attempts can be compared.
This is the opposite of the AI profile, which overwrites in place - don't build anything that assumes an application has only one interview.

Roughly 1,450-1,850 tokens per interview on `openai/gpt-oss-120b`, producing 5-6 turns plus a closing assessment.

### The AI's assessment is not your feedback

`overallAssessment` / `overallRating` on a session are the model's read on the transcript it just wrote. `RecruiterFeedback` is yours. They are stored separately and never merged, because merging
them would quietly launder a generated opinion into a human one. They are free to disagree - in testing the AI rated Mr. Burns 2/5 for Chief of Police while the recruiter gave 1/5 with "AI was
generous".

### If you edit the interview prompt

Two instructions are load-bearing for character voice:

1. **Unsuitability must show through the answers**, never be described. This is what makes Bart
   answer a police-budget question with fourth-grade cafeteria logistics instead of being labelled
   unqualified.
2. **Catchphrases are temperament, not answers** - one or two per interview, inside a sentence.
   Drop this and transcripts become lists of quotes.

## Offer stage

Reaching `OFFER` pauses the pipeline for a real decision. The recruiter enters an annual salary and acceptance is decided **arithmetically against national wage data** — no model involved, so the same number always gives the same answer and the stored rationale is a statement of fact.

```bash
curl -X POST http://localhost:8080/api/applications/{id}/offer \
  -H 'Content-Type: application/json' -d '{"offerAmount":45000}'
```

Inside the occupation's 10th–90th percentile band → **`ACCEPTED`** → `HIRED`. Outside it →
**`DECLINED`** → `WITHDRAWN`, because the candidate walked away rather than being turned down.
Both are terminal, so an offer is one-shot; a second attempt gets the same 409 as any other illegal
transition. This endpoint wraps the transition service rather than bypassing it — the state machine stays the only thing deciding legality.

That also gives `WITHDRAWN` its first real trigger. The demo now has three genuinely distinct outcomes: `HIRED` (offer accepted), `REJECTED` (recruiter declined), `WITHDRAWN` (candidate declined).

### Wage reference data

U.S. Bureau of Labor Statistics, Occupational Employment and Wage Statistics (OEWS), national cross-industry estimates, **May 2025** (`national_M2025_dl.xlsx`, kept in `resources/`). BLS blocks
automated download — every path returns an "Access Denied" page — so the workbook is fetched by hand and converted to `src/main/resources/db/occupation_wage_seed.sql`, which seeds idempotently at
startup.

Only the `total` (`00-0000`, All Occupations) and `detailed` rows are imported — **831 of 1,401**.
The major/minor/broad aggregates are excluded deliberately: "Management Occupations" is not a job anyone holds, and including those rows would let matching resolve a candidate to an umbrella
category. Annual percentiles are used because offers are annual salaries, and BLS's `'*'` suppression marker imports as NULL rather than becoming an invented bound.

Occupations are matched to SOC codes with the same `ts_rank` technique as requisition matching, above a **0.0070** confidence threshold — just under a solid single-term hit, so "Bartender" is
trusted but an incidental shared word is not. Below it, matching falls back to the aggregate row rather than blocking the offer. Moe Szyslak resolves to `35-3011 Bartenders` ($20,110–$73,770);
Marge Simpson's "Unemployed" falls back to All Occupations ($31,200–$128,560). This inherits the documented `ts_rank` limitation — no cross-document IDF — which is exactly why the threshold and
fallback exist.

## Demo frontend

A React front end in [`frontend/`](frontend/) walks the whole story on one screen: open a requisition, rank the pool against it, apply a candidate, score them, interview them, extend an offer, and watch the state machine refuse a second decision.

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173 - needs the API running on 8080
```

Vite proxies `/api` to `localhost:8080`, so the browser only ever makes same-origin requests and neither side needs CORS configuration.

It is verified by driving real Chromium with Playwright rather than by trusting that a build succeeded — `npm run verify`, `verify:flow` and `verify:offer` assert against the live DOM and the actual network calls made, against real Groq generations. See [`frontend/README.md`](frontend/README.md).

## Tests

```bash
./gradlew test
```

74 tests across 10 classes. All but one are plain unit tests with mocked collaborators and no network — including the AI ones, which never call Groq.

The exception is `SpringfieldTalentPipelineApplicationTests.contextLoads`, which starts the real application context and therefore **needs Postgres running**. That is deliberate: "starts and
connects to Postgres" is the thing worth asserting, and an in-memory stand-in would assert nothing.

Two behaviours are deliberately not unit-tested, because a test would assert nothing useful:

- **Full-text ranking**, which needs real Postgres and a populated corpus to mean anything.
- **Character voice in generated interviews**, which is a judgement about prose, not an assertion.

## Attribution and licensing

> Character data provided by [The Simpsons API](https://thesimpsonsapi.com), sourced from The Simpsons Wiki (CC BY-SA).

This is a non-commercial portfolio/demo project. It is not affiliated with, authorized by, or endorsed by Fox or Disney, who own The Simpsons.
