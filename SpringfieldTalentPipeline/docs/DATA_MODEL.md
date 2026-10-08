# Data Model

## `Candidate`

Synced from The Simpsons API — see `SIMPSONS_API.md` for the sync process.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `externalId` | integer | The Simpsons API's own character `id` — used to detect updates on re-sync |
| `name` | string | |
| `age` | integer? | null for 94% of the pool (1,115 of 1,182) — most background characters have no age in the source. Never defaulted to 0 |
| `gender` | string? | `Male` / `Female` / `Unknown` in practice; kept as free text rather than an enum, since nothing depends on it |
| `occupation` | string? | free text, e.g. `"Owner of the Kwik-E-Mart"` — this is the field that does the heavy lifting for matching. Populated for **all** 1,182 characters, so matching never has a null to contend with |
| `characterStatus` | enum — see below | the API's `status` field — deliberately named `characterStatus`, not `status`, so it's never confused with a pipeline/application status |
| `portraitPath` | string? | raw `portrait_path` from the API, e.g. `/character/1.webp`. Resolves against `https://cdn.thesimpsonsapi.com/{width}` (widths 200/500/1280) — confirmed against the live CDN, see `SIMPSONS_API.md`. Stored raw so a CDN change is a config edit, not a re-sync |
| `phrases` | string[] | catchphrases — used as flavor input to the AI profile/interview prompts, not shown as literal marketing copy anywhere in the UI. **Not a column on `candidate`** — see below |
| `importedAt` | timestamp | set once on first insert and never updated afterwards |
| `lastSyncedAt` | timestamp | advanced on every sync that touches the record |

### `characterStatus` values

**Corrected 2026-09-09 against the live API.** This table originally read
`enum (Alive, Dead, Unknown)`. The API actually returns seven values, and `Dead` is not one of
them — the value is `Deceased`. Modelling only the predicted three would have misfiled 201 records
(17% of the pool).

| Enum constant | API value | Count |
|---|---|---|
| `ALIVE` | `Alive` | 855 |
| `DECEASED` | `Deceased` | 136 |
| `UNKNOWN` | `Unknown` | 126 |
| `FICTIONAL` | `Fictional` | 50 |
| `NONCANON` | `Noncanon` | 9 |
| `NONCANON_DECEASED` | `Noncanon Deceased` | 4 |
| `DESTROYED_ICON` | `Destroyed Icon` | 2 |

Unrecognised or absent values map to `UNKNOWN` and log a warning, so a value added upstream costs
one record's fidelity rather than failing its import.

`phrases` is an ordered element collection, which JPA maps to a **separate table**, not to a column
on `candidate`. So `SELECT phrases FROM candidate` fails with *column does not exist* — that is the
mapping working, not a missing migration. The rows live in `candidate_phrase`:

| Column | Notes |
|---|---|
| `candidate_id` | FK to `candidate` |
| `phrase` | text |
| `phrase_order` | 0-based; preserves the order the API returned |

with a composite primary key on (`candidate_id`, `phrase_order`). To read them back:

```sql
SELECT c.name, array_agg(p.phrase ORDER BY p.phrase_order) AS phrases
FROM candidate c JOIN candidate_phrase p ON p.candidate_id = c.id
GROUP BY c.name;
```

The collection is empty for 77% of characters and never null — 492 rows across 271 candidates on a
full sync. It is also lazy, so anything reading it outside a transaction needs
`CandidateRepository.findWithPhrasesById`, which is why the mock interview path uses that and the AI
profile path (which runs inside a transaction) does not.

`externalId` carries a unique constraint — it is what makes a re-sync an update rather than a
duplicate.

## `Requisition`

A job opening.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `title` | string | |
| `department` | string? | |
| `targetKeywords` | text | free text used as the full-text search query against candidates — matching logic lives with `Requisition` |
| `hiringManager` | string? | freeform |
| `status` | enum (`Open`, `Filled`, `Cancelled`) | stored as `OPEN` / `FILLED` / `CANCELLED` |
| `openedDate` | date | |
| `keywordsUpdatedAt` | timestamp | **not in the original spec** — added during implementation |

`keywordsUpdatedAt` exists because `AiCandidateProfile` is supposed to regenerate "if the
requisition's `targetKeywords` changed since `generatedAt`", and nothing else in the model records
*when* they changed. It advances only when the text genuinely differs, so re-saving an unchanged
form does not invalidate every cached profile on the requisition.

Editing is `PATCH /api/requisitions/{id}`; without it the invalidation rule above would be
unreachable through the API.

## `JobApplication`

Join of `Candidate` ↔ `Requisition`, the actual pipeline record. Named `JobApplication` rather than `Application` to avoid colliding with Spring Boot's own `@SpringBootApplication` main class and `ApplicationContext` vocabulary — a bare `Application` entity sitting alongside `SpringfieldTalentPipelineApplication.java` would be confusing to read even though it compiles fine.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `candidateId` | UUID (FK) | |
| `requisitionId` | UUID (FK) | |
| `currentStage` | enum (`Sourced`, `Screening`, `Interviewing`, `Offer`, `Hired`, `Rejected`, `Withdrawn`) | governed by the state machine — see `PIPELINE_STATE_MACHINE.md` |
| `createdAt` | timestamp | |
| `updatedAt` | timestamp | |

Enforce one active (non-`Rejected`/non-`Withdrawn`) application per candidate–requisition pair at the service layer; allow re-application after a rejection/withdrawal if it ever comes up, rather than a hard DB constraint that would block that case.

## `StageTransition`

Audit log — one row per stage move.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `applicationId` | UUID (FK) | |
| `fromStage` | enum | |
| `toStage` | enum | |
| `transitionedAt` | timestamp | |
| `note` | string? | |

## `AiCandidateProfile`

Cached AI-generated profile and fit score. One per `JobApplication` (fit is requisition-specific, not just candidate-specific).

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `applicationId` | UUID (FK) | |
| `generatedBio` | text | LLM-written narrative profile, built from `occupation`/`age`/`phrases` |
| `fitScore` | integer | 0–100 |
| `fitRationale` | text | short LLM explanation tied to the requisition's `targetKeywords` |
| `modelUsed` | string | e.g. `openai/gpt-oss-20b` |
| `generatedAt` | timestamp | |

Regenerate only on explicit request (a "Refresh" action) or if the requisition's `targetKeywords` changed since `generatedAt` — never silently on every page load.

## `MockInterviewSession`

One structured interview per application. Generated in a single Groq call per `AI_FEATURES.md` — not a live back-and-forth.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `applicationId` | UUID (FK) | |
| `status` | enum (`Generated`, `Failed`) | stored as `GENERATED` / `FAILED`; a failed attempt is recorded rather than discarded, so a run of failures is visible |
| `overallAssessment` | text | LLM's own closing summary of how the candidate did — **this is the AI's self-assessment of the fictional candidate's performance, not a stand-in for recruiter judgment** |
| `overallRating` | integer? | 1–5, LLM-generated alongside `overallAssessment` |
| `modelUsed` | string | e.g. `openai/gpt-oss-120b` |
| `generatedAt` | timestamp | |

## `MockInterviewTurn`

The individual Q&A pairs within a session.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `sessionId` | UUID (FK) | |
| `questionNumber` | integer | 1 through 5–8 |
| `question` | text | generated as part of the same call, tailored to the role and character |
| `answer` | text | the candidate's in-character response |

## `RecruiterFeedback`

The human's own notes after reviewing a `MockInterviewSession` — deliberately a separate entity from `overallAssessment` above.

Lives in the `pipeline` package rather than `ai`, which is the point: it is the one verdict in the
system that no model produced.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `applicationId` | UUID (FK) | |
| `sessionId` | UUID? (FK) | which interview this feedback is responding to, if any |
| `rating` | integer | your own rating, independent of the AI's `overallRating` |
| `comments` | text | |
| `createdAt` | timestamp | |
