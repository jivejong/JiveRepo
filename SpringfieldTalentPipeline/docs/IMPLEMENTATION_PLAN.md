# Implementation Plan

Phased, with a checkpoint at the end of each phase before moving on — verify against real data before building the next layer on top of an unverified one.

**Status as of 2026-09-09: all five phases are complete**, each checkpoint verified against real
infrastructure, the live Simpsons API and real Groq calls.

## Phase 0 — Project scaffolding ✅ complete

- Spring Boot 3 / Java 21 project, built with **Gradle** (Groovy or Kotlin DSL, either is fine): Spring Web, Spring Data JPA, PostgreSQL driver, Spring Statemachine as dependencies in `build.gradle`.
- Config for the Groq API key via environment variable, never committed.
- **Checkpoint:** app starts (`./gradlew bootRun`), connects to a local Postgres instance, health endpoint responds.

**Checkpoint result:** app starts in ~2.9s on Temurin 21, Tomcat on 8080, `GET /actuator/health`
returns 200 with both overall status and the `db` component `UP` against Postgres 16 in Docker.

Decisions made during this phase that the spec left open:

- **Group ID** — the spec's `com.jonglee.springfieldtalentpipeline` was a placeholder to be swapped
  for the portfolio convention. There were no other JVM projects in the repo to copy, so it became
  `com.jivejong.springfieldtalentpipeline`, matching the repo owner's handle.
- **Boot 3.5.16 / Gradle 9.7.1.** Gradle is on the current supported line. Boot is deliberately
  *not*: 3.5.16 is the last OSS release of the 3.5 line (free support ended 2026-06-30), so this
  branch gets no further free security patches. That is forced rather than chosen — see the
  compatibility note in `PIPELINE_STATE_MACHINE.md`.
- **Secrets** — `config/local.yml` (gitignored) is imported via
  `spring.config.import: optional:file:./config/local.yml`, with `config/local.yml.example` as the
  committed template. Verified precedence: environment variable > `config/local.yml` >
  `application.yml` defaults. The `optional:` prefix keeps a fresh clone and CI working without the
  file.
- **Actuator** was added beyond the dependency list above, because the checkpoint asks for a health
  endpoint and its `db` indicator is what actually proves the Postgres connection.

## Phase 1 — Candidate sync ✅ complete

- Implement `Candidate` entity and repository per `docs/DATA_MODEL.md`.
- Implement the Simpsons API sync client per `docs/SIMPSONS_API.md` (pagination, upsert-on-`externalId`, per-record error isolation).
- Wire up `POST /api/candidates/sync`.
- **Checkpoint:** run a real sync against the live API. Confirm the full ~1,182 character count lands (roughly — some may legitimately fail/skip), spot-check a well-known character (e.g. Homer) has all expected fields, and confirm a re-run updates rather than duplicates.

**Checkpoint result:** all three conditions met against the live API.

- **Full count landed exactly** — 60/60 pages, 1,182 seen, 1,182 created, **0 failed**, 19.3s. The
  "some may legitimately fail" allowance turned out not to be needed.
- **Homer spot-check** — id 1, age 39, Male, "Safety Inspector", `ALIVE`, 15 phrases in order,
  portrait resolving to a live 200/`image/webp`.
- **Re-run is an update, not a duplicate** — second run reported `created: 0, updated: 1182`; row
  count held at 1,182 with 1,182 distinct `externalId`, primary keys unchanged, `importedAt`
  preserved while `lastSyncedAt` advanced, and phrase rows held at 492 rather than doubling.

Stored data matches the live API exactly: identical status distribution across all seven values,
1,115 null ages, and **zero** ages defaulted to 0 or occupations defaulted to empty string.

**Two spec corrections came out of this phase**, both now folded into the docs rather than left as
deltas: the `status` enum has seven values and no `Dead` (`DATA_MODEL.md`), and the portrait CDN
base is confirmed (`SIMPSONS_API.md`).

**Added after the phases:** `GET /api/candidates?q=` appears in the root README's API surface but
was not in any phase's bullet list, so it was skipped during Phase 1 and verification went through
psql instead. That was a gap in the spec rather than something worth skipping, and it is now built.

It uses `ILIKE` substring matching on name and occupation, **not** the full-text search behind
`GET /api/requisitions/{id}/matches`. The two endpoints answer different questions: this one is
lookup, so someone typing `szys` should find the Szyslaks and `burns` should find Charles Montgomery
Burns. Full-text search would stem the input, drop stop words, and AND the terms together - the
last of which the matches endpoint had to work around explicitly. The trade is that a multi-word
query is treated as one literal substring, so `bartender tavern` matches nothing; that is correct
for a lookup box and wrong for relevance ranking, which is exactly why the two are separate.

## Phase 2 — Requisitions and applications ✅ complete

- Implement `Requisition`, `JobApplication`, `StageTransition` entities.
- Implement the state machine per `docs/PIPELINE_STATE_MACHINE.md`, wired to `POST /api/applications/{id}/transition`.
- Implement full-text search matching (`GET /api/requisitions/{id}/matches`) against `Candidate.occupation`.
- **Checkpoint:** create a real requisition (e.g. "Bartender"), confirm the match endpoint surfaces sensible candidates (Moe Szyslak should rank highly), create an application, and walk it through several valid transitions plus at least one deliberately invalid one to confirm the state machine actually rejects it with a 409.

**Checkpoint result:** all conditions met.

- **Moe ranks first** (0.01309 vs 0.00959 for the runner-up), because he is the only candidate
  matching two query terms rather than one. Six candidates matched in all; the non-obvious one,
  "Chief Inspector", earns its place through "Tipsy McStagger's Good-Time *Drinking* and Eating
  Emporium", and sorts last.

  *(Ranks were originally 0.03040/0.01520 without length normalisation, which was added later - see
  "Ranking needed length normalisation" below. Moe's position was unaffected.)*
- **Valid moves** Sourced→Screening→Interviewing→Offer→Hired all returned 200.
- **Invalid moves all returned 409** — skipping a stage (Interviewing→Hired), moving backwards
  (Interviewing→Screening), and leaving a terminal stage (Hired→anything) — each with the current
  stage and the reachable stages in the body.
- **The audit log holds exactly 5 rows** for the 5 real moves. The four rejected attempts wrote
  nothing, which is the point of recording from the machine's own listener rather than from the
  call site.
- **The duplicate rule works both ways:** a second application while one is active returns 409,
  while re-applying after a terminal one returns 201.
- The GIN index is used by the planner (`Bitmap Index Scan on idx_candidate_occupation_fts`).

### The one thing the spec did not anticipate: `plainto_tsquery` means AND

The obvious implementation returns **zero** matches for target keywords of
"bartender tavern bar drinks", because `plainto_tsquery` joins terms with AND and no candidate's
occupation matches all four. Matching is a ranked "who is closest to this" question, not a filter,
so the parsed query is re-joined with OR before use. Parsing still goes through `plainto_tsquery`
first, which keeps the stemming and stop-word handling and stops arbitrary recruiter-typed text from
reaching `to_tsquery`, where a stray `&` or `!` would be a syntax error. `ts_rank` then does the
discriminating.

### Ranking needed length normalisation (found after the phases)

The original `ts_rank` call took no normalisation argument, which meant **every candidate matching
exactly one query term scored identically**. With target keywords of "bartender tavern bar drinks"
that went unnoticed, because Moe matches two terms ("bartender" and "tavern") and wins on that
alone. Change the keywords to something where he matches only one - "bartender mixology customer
service" - and the entire result set ties at 0.01519817765802145, the ordering collapses to
alphabetical, and an unrelated "Receptionist for the Rubber Baby Buggy Bumper Babysitting Service"
sits level with him on the strength of "service".

The checkpoint above was therefore passing for a weaker reason than it appeared: not because ranking
was good, but because that particular keyword set happened to give Moe a second hit.

Adding normalisation flag `1` (divide by `1 + log(document length)`) fixes it. A long occupation
that contains one incidental keyword now sorts below a short exact match, in both keyword sets.

Two better-sounding fixes were measured and rejected:

- **`ts_rank_cd`** (cover density) changes nothing. It scores how close matched terms sit to each
  other, and when every document matches a single term there is no distance to measure - all five
  results came back at exactly 0.1.
- **IDF weighting backfires** on a corpus this small. "servic" appears in 1 of 1,182 occupations and
  "bartend" in 4, so weighting by inverse document frequency would rank the babysitting receptionist
  *above* every bartender. Rarity across 1,182 short strings is not a proxy for importance.

**Verified against a keyword set where every term is live in the corpus** ("bartender tavern owner"
- 35, 4 and 1 occupations respectively), to confirm ranking rewards real overlap rather than merely
surviving a mostly-inert query:

| # | rank | candidate | terms matched | doc size |
|---|---|---|---|---|
| 1 | 0.026182 | Moe Szyslak - "Bartender and Owner of Moe's Tavern" | **3** | 40 |
| 2 | 0.012785 | Richard Branson - "Owner of Virgin" | 1 | 20 |
| 4 | 0.012785 | William MacDougal II - "Bar owner" | 1 | 17 |
| 8 | 0.010132 | Brandt - "Bartender at Moho House" | 1 | 34 |

Moe wins by better than 2x **while having the longest document of the leaders**, so multi-term
overlap comfortably outweighs the length penalty. That is the property the normalisation change
needed to preserve.

### Residual limitation: length is the only tiebreaker

The same table exposes what remains: among candidates matching the **same number** of query terms,
document length decides the order and nothing else does. Brandt, an actual bartender, sits below
Richard Branson for a bartending role.

The mechanism is worth stating precisely, because it is easy to get wrong. `ts_rank` scores from
term frequency within the document, position weights, and - with normalisation flag `1` - document
length. It reads **no corpus-wide statistics whatsoever**. The identity of the matched term
contributes nothing to the score, which the data shows directly:

| doc lexemes | rank | candidates |
|---|---|---|
| 2 | 0.01278531 | Richard Branson *(matched "owner")*, William MacDougal II *(matched "owner")* |
| 3 | 0.010132118 | Al Gumble *(matched "owner")*, Brandt, John Travolta, Titania *(all matched "bartender")* |

Al Gumble matching "owner" scores identically to Brandt matching "bartender" - same match count,
same document length, same rank. Brandt ranks below Branson purely because "Bartender at Moho House"
is one lexeme longer than "Owner of Virgin".

So this is the same normalisation lever from the first test, seen from the other side. Demoting the
long, incidental "Babysitting Service" match and demoting the relevant "Bartender at Moho House"
are the same behaviour: length normalisation is blunt, and it is blind to whether the matched term
was central to the role. It happens to help in the first case and hurt in the second.

Weighting terms by importance would address it, and an earlier note in this file proposed deriving
those weights from corpus document frequency. That was based on a wrong premise - it implied
`ts_rank` was already weighting terms in a way IDF would correct, and it isn't weighting them at
all. Any such scheme would be new machinery on top, justified by a problem the live data does not
currently exhibit: the real Bartender requisition uses "bartender tavern bar drinks", which contains
no high-frequency term to drag in unrelated candidates, and Moe wins there on genuine two-term
overlap. Recorded as a known limitation rather than built.

What normalisation cannot do is invent signal that is not there. Under "bartender mixology customer
service", Moe places fourth behind three other literal bartenders - and that is correct: nothing in
those keywords distinguishes him, and his occupation is longer, which normalisation mildly
penalises. "Tavern" is the word that makes Moe distinctive, and the newer keyword set drops it.
Ranking reflects the keywords it is given.

### Decisions the spec left open

- **The transition table is declared exactly once**, in the state machine config. The 409's
  "allowed next stages" list is derived from the machine's own configuration rather than written
  down a second time, so the error message cannot drift from the enforcement.
- **A requested stage is checked against the machine's transitions before any event is sent.**
  Mapping a target stage onto an `ADVANCE`/`REJECT`/`WITHDRAW` event is lossy: asking for
  Sourced→Offer would send `ADVANCE`, be accepted, and land on Screening — a move nobody asked for.
- **The GIN index lives in `data.sql`**, since a functional index cannot be expressed as a JPA
  annotation and there are still no Flyway migrations. Its `to_tsvector` expression must stay
  identical to the repository's or Postgres silently reverts to a sequential scan.

## Phase 3 — AI candidate profile ✅ complete

- Implement the Groq client and the profile/score generation call per `docs/AI_FEATURES.md` Feature 1.
- Wire up `POST /api/applications/{id}/ai-profile`, persisting to `AiCandidateProfile`.
- **Checkpoint:** generate profiles for 5–10 real applications spanning different requisitions. Confirm the fit score and rationale actually reflect the requisition's keywords (not a generic score regardless of role), and confirm calling it twice without a keyword change doesn't regenerate (cache is respected).

**Checkpoint result:** 11 real profiles across 4 requisitions, 0 failures.

Scores are role-specific, not generic. The decisive evidence is the same candidate against two
different roles:

| Candidate | Role | Score |
|---|---|---|
| Moe Szyslak | Bartender | 95 |
| Moe Szyslak | Nuclear Safety Inspector | 25 |
| Homer Simpson | Nuclear Safety Inspector | 55 |
| Homer Simpson | Bartender | 20 |

Rationales name the actual keywords - Moe's cites "bartender, tavern, bar, and drinks"; Homer's for
the nuclear role notes he "lacks specific nuclear power plant or reactor expertise". Elsewhere:
Clancy Wiggum 95 for Chief of Police against Bart's 0, Edna Krabappel 95 for the teaching role
against Bart's 25.

All three cache paths verified live: a repeat call serves `CACHED` with no tokens spent, editing the
requisition's target keywords produces `REGENERATED_KEYWORDS_CHANGED`, and the row count stays at 11
because a regeneration overwrites in place rather than inserting.

### Fit scores were unstable, and temperature was not the cause

The first implementation scored Homer against the nuclear role at **20, 40, 55, 60, 70** across
repeated runs on identical input. A cached score that depends on when it happened to be generated
is not a judgement, it is a coin flip.

Lowering temperature from 0.4 to 0.1 barely helped (still 20-65). The actual cause was an
unanchored scale: "how well does this candidate fit" leaves the model to re-invent what 50 means on
every call. Adding explicit scoring bands to the system prompt - 90-100 for a direct match on the
role's primary keyword, 70-89 adjacent, 40-69 partial, 10-39 unrelated-but-trainable, 0-9 none -
made it repeatable: **55, 55, 55, 55, 55** for the same case, and 25 across the board for Moe
against the nuclear role.

Per-feature temperatures were kept anyway (`groq.temperature.profile` 0.1,
`groq.temperature.interview` 0.8): scoring wants repeatability, the Phase 4 interview wants voice.

### Rate limits are a normal operating condition, not an exception

Generating a batch walks straight into Groq's per-minute free-tier limit; several generations failed
with 429 mid-run. The client now retries up to 4 times, honouring `Retry-After` when present
(Groq sends fractional seconds there, which `Integer.parseInt` would reject) and otherwise backing
off 2s/4s/8s. Every other status fails immediately - retrying a bad request only burns budget. A
subsequent unpaced run of all 11 completed with zero failures.

### Token usage vs the estimate

Measured **691 prompt + 139 completion = 830 tokens** per profile. `AI_FEATURES.md` estimated
300-600 input and 200-400 output. Input runs high because the scoring rubric above lives in the
system prompt; output runs low because the reply is three short fields. The total sits inside the
estimated envelope, and the rubric is worth its tokens - it is what bought score stability. Being
in the system prompt, it is also the part eligible for Groq's prompt caching.

### Gap found and closed: nothing could edit a requisition

The keyword-invalidation rule was unreachable through the API - there was no way to change a
requisition after creating it, so a cached profile could never actually go stale in normal use. The
first pass verified that path with a direct SQL update, which proves the logic but not the feature.

`PATCH /api/requisitions/{id}` closes it. Partial update: an omitted field is left alone, so
keywords can be changed without restating the requisition. Clearing an optional field deliberately
is not expressible, which keeps null unambiguous.

Verified end to end through the API: editing the Nuclear Safety Inspector role's keywords to
pastry-and-baking terms dropped Homer from 55 to 25 with a rationale naming the new keywords;
restoring them brought him back to 55. Re-submitting identical keywords is a no-op that does not
move `keywordsUpdatedAt` and does not cost a regeneration - the same for a whitespace-only
difference, or editing an unrelated field. Blank keywords are rejected with 400 rather than
producing a requisition that silently matches nothing.

## Phase 4 — Mock interview ✅ complete

- Implement the single-shot structured interview generation per `docs/AI_FEATURES.md` Feature 2.
- Wire up `POST /api/applications/{id}/mock-interview`, persisting `MockInterviewSession` + `MockInterviewTurn` rows.
- Implement `POST /api/applications/{id}/feedback` for `RecruiterFeedback`, kept separate from the AI's own `overallAssessment`.
- **Checkpoint:** generate real mock interviews for a handful of applications with genuinely different characters and roles (e.g. Mr. Burns interviewing for a role vs. Bart interviewing for the same role) — confirm the questions and answers actually differ meaningfully by character rather than reading like generic filler, and confirm total token usage roughly matches the estimate in `AI_FEATURES.md`.

**Checkpoint result:** 9 sessions across 4 requisitions, 50 turns, 0 failures.

The spec's own example - Mr. Burns against Bart for the same role - is the clearest evidence, with
Clancy Wiggum added as the candidate who actually holds the job. Asked about budget management for
Chief of Police:

- **Bart** answers with fourth-grade cafeteria logistics and choosing a comic book over a video game
  because he "could trade it later for a snack".
- **Burns** would "allocate resources to the most profitable divisions" and adopt body cameras "only
  after a thorough review to ensure the footage does not reveal any... inconvenient truths".
- **Wiggum** makes sure "the patrol cars have gas - otherwise we can't chase the bad guys" and keeps
  money aside for handing out candy on Halloween, with spreadsheets that look "a little... creative".

These are not interchangeable, and no answer describes a candidate as unsuitable - unsuitability
shows through what they choose to talk about. The AI ratings track it: Bart 1, Burns 2, Wiggum 3,
with Edna Krabappel and Moe Szyslak reaching 4 in their own fields.

Catchphrase discipline held: Wiggum's "Book 'em, Lou" arrives inside a sentence about filing
paperwork rather than as a standalone answer, which is what the prompt asks for. A transcript that
reads as a list of quotes was the failure mode to avoid.

**Token usage: 1,436-1,845 per interview**, against the doc's 1.5-2.5K estimate. Input sat at
769-806 (the upper end of the estimated 400-800), output at roughly 650-1,050. Turn counts came out
at 5-6, inside the specified 5-8.

### Design points

- **Regenerating appends.** Each request creates a new session rather than overwriting, so attempts
  can be compared - the opposite of the Phase 3 profile, which overwrites in place. Verified: three
  retained sessions for Mr. Burns.
- **The 1-5 rating is banded**, applying the Phase 3 lesson about unanchored scales before it could
  bite again.
- **Failed attempts are recorded**, not discarded. `saveFailed` commits in its own transaction
  (`REQUIRES_NEW`) because it runs on the way to throwing; joining the caller's transaction would
  roll the failure record back along with the request.
- **Turns are renumbered sequentially** after dropping any that arrive without a question or answer,
  so a model that misnumbers or skips cannot produce a transcript with holes.
- **The Groq call runs outside any transaction.** It takes seconds, and holding a database
  connection across it would tie up the pool for nothing.
- **Recruiter feedback is stored separately** from the AI's own assessment, per the spec. Verified
  by disagreeing on purpose: the AI rated Mr. Burns 2/5, the recruiter 1/5 with "AI was generous",
  and both persist independently.

### One bug worth recording

The interview list endpoint returned 500 on first use: `turns` is lazy, the store's read-only
transaction closes when it returns, and the controller then counted turns on a detached collection.
The single-session endpoint was unaffected because it already used an `@EntityGraph`; the list
finder now does too. Worth remembering that `open-in-view: false` - the right setting - makes this
class of mistake fail loudly rather than silently issuing N+1 queries.

## Notes for whoever implements this (including Claude Code)

- Treat each checkpoint as a hard stop — real data (the actual live Simpsons API, real Groq calls) before starting the next phase.
- Isolate and report bad/unmatched records rather than failing a whole sync batch.
- Keep the Groq API key out of source control from Phase 0 onward.
- Don't build the mock interview as a multi-turn chat endpoint even if it seems like a natural extension later — that's a deliberate scope decision (see `AI_FEATURES.md`), not an oversight to "fix."
