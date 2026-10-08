# Springfield Talent Pipeline — Master Engineering Log

Sep 27, 2026 · compiled by Jong

## Project snapshot

Springfield Talent Pipeline is a Spring Boot REST API modeling a recruiting pipeline (ATS): candidate sourcing from Simpsons character data, requisition-to-candidate matching, staged pipeline transitions with an audit trail, AI-generated candidate profiles and fit scores, AI mock interviews, and a deterministic BLS-backed offer stage. A React/Vite frontend and Playwright suite demo the full flow.

| Layer              | Choice                                                                                                          |
| ------------------ | --------------------------------------------------------------------------------------------------------------- |
| Language / runtime | Java 21 (Temurin), Gradle 9.7.1 wrapper                                                                         |
| Framework          | Spring Boot 3.5.16 (pinned by Spring Statemachine 4.0.2)                                                        |
| Persistence        | Spring Data JPA, Hibernate 6.6, PostgreSQL 16 in Docker (`springfield-postgres`), `ddl-auto: update`, no Flyway |
| Pipeline           | Spring Statemachine 4.0.2; `StageTransition` audit rows via listener                                            |
| Matching           | Postgres full-text search (`ts_rank`, GIN indexes)                                                              |
| Candidate data     | The Simpsons API (`thesimpsonsapi.com`), 1,182 characters                                                       |
| Wage data          | BLS OEWS national, May 2025, 831 rows seeded locally                                                            |
| AI                 | Google Gemini `gemini-3.1-flash-lite` (migrated 2026-09-23 from Groq `gpt-oss-20b` / `gpt-oss-120b`)            |
| Frontend           | React 19, Vite 8, dev proxy `/api` → `localhost:8080`                                                           |
| Verification       | Playwright + headless Chromium; JUnit backend suite (76 tests)                                                  |
| Package            | `com.jivejong.springfieldtalentpipeline`                                                                        |

This log merges four source documents into one chronological account:

1. Design/planning chat, 2026-09-09 — project inception through the Claude Code handoff.
2. Claude Code execution log, 2026-09-09–10 — Phase 0 through the demo frontend and offer stage.
3. Design/review chat, continued 2026-09-09–10 — verification, frontend scoping, offer stage design, final push.
4. Groq→Gemini migration session, 2026-09-23.

Where the planning chat and the Claude Code execution ran in parallel over the same stretch (Phase 0–4, roughly 2026-09-09 20:00Z–2026-09-10 02:00Z), the two threads are merged phase by phase below rather than kept separate.

## Session 1 — Design & planning (2026-09-09)

**Portfolio context.** The two-project split — Java API plus a C# desktop/database app — was set from the outset: this project is a REST API only, no frontend, until the frontend need re-emerged in Session 3.

**Design v1, superseded.** A first design (Song Enrichment & Setlist Generator over MusicBrainz + GetSongBPM + Groq/Gemini RAG) was fully spec'd (`song-setlist-app/`: README, DATA_MODEL, MUSICBRAINZ_API, GETSONGBPM_API, RAG_SETLIST_GENERATOR, IMPLEMENTATION_PLAN) then abandoned once MusicBrainz was claimed by a different app. The spec is retained but unused.

**Design v2 — Springfield Talent Pipeline.** Live-verified against `thesimpsonsapi.com`: no auth, `/characters?page=n`, fixed 20/page, 1,182+ characters, fields `id, age, birthdate, gender, name, occupation, portrait_path, phrases[], status`. Data is CC BY-SA (The Simpsons Wiki); the README carries an attribution line and a Fox/Disney non-affiliation disclaimer. Domain model: `Candidate`, `Requisition`, `Application` (renamed `JobApplication`, see below), `StageTransition`, `InterviewFeedback`. Chosen so the ATS demo needs neither fake people nor real PII; "Springfield" plays on "Spring."

**V1 scope decision.** AI candidate profiles/fit scores and a mock interview were kept in V1 — Groq's free tier is rate-limited, not budget-limited, so the real risk is unpaced bulk calls, not cost. The interview format is a fixed 5–8 question set, generated as **one structured call returning the whole transcript**, not a multi-turn chat — estimated \~1.5–2.5K tokens per interview versus tens of thousands for a resent-history chat. AI outputs are cache-first: generated once, stored, regenerated only on request or input change. The AI's own assessment and human `RecruiterFeedback` are kept as separate entities. Initial model split: `llama-3.1-8b-instant` (scoring) / `llama-3.3-70b-versatile` (interview).

**Groq model deprecation.** Both initial models were deprecated for free/developer tiers. Migrated to `openai/gpt-oss-20b` (scoring) and `openai/gpt-oss-120b` (interview), with `qwen/qwen3.6-27b` noted as an A/B fallback candidate. The old 8B/70B split had relied partly on a daily-request asymmetry (14,400/day vs. 1,000/day); both GPT-OSS models share roughly the same free-tier ceiling (\~30 RPM / 1,000 RPD), so the split became speed/quality-only.

**Reasoning configuration.** GPT-OSS models default to `reasoning_effort: "medium"` and spend output tokens on internal reasoning. Set to `"low"` on both calls (`gpt-oss-20b`/`120b` accept `low|medium|high`; Qwen's scale differs — `none|default` — so swapping models means swapping this value too). `reasoning_format: "hidden"` (Groq only allows `parsed` or `hidden` together with JSON mode).

**Claude Code handoff method.** Agreed to hand off phase by phase rather than "follow the instructions," with a `CLAUDE.md` recommended so every new Claude Code session reads the docs and respects phase gates.

**Build tool, layout, entity rename.** Gradle (wrapper only, no system install) was the chosen build tool. `Application` was renamed to `JobApplication` to avoid clashing with `SpringfieldTalentPipelineApplication`/Spring's own `ApplicationContext` vocabulary (REST paths stayed `/api/applications`). Package layout: `candidate/`, `requisition/`, `pipeline/`, `ai/`, `web/`, with `docs/` at the repo root (Markdown doesn't affect GitHub's language stats). The package name was left as a placeholder pending reconciliation with whatever Claude Code actually generated — resolved in Session 2 as `com.jivejong.springfieldtalentpipeline`.

## Phase 0–1 — Scaffolding & candidate sync (2026-09-09)

**Local environment (Windows).** Planned installs: JDK Temurin 21 via winget, Docker Desktop via winget, Postgres as a container rather than a native service:

```
docker run --name springfield-postgres -e POSTGRES_PASSWORD=<pw> -e POSTGRES_DB=springfield -p 5432:5432 -d postgres:16
```

Issues worked through: `docker` not recognized until a fresh terminal + Docker Desktop launch; the Docker daemon not running (`npipe` error) traced to `wsl --status` reporting virtualization not detected — **root cause was CPU virtualization (VT-x/AMD-V) disabled in BIOS**, fixed in firmware (a plain reboot doesn't fix it); a firewall prompt for the JDK/Tomcat port offered only "Public" because the WSL2 virtual adapter is classified Public — fixed by setting that adapter's network profile to Private (whether this was actually applied was never confirmed). Outcome: JDK 21, Docker, and Postgres-in-Docker all running.

**Secrets handling.** No DB password in tracked files: `${DB_PASSWORD}` has no fallback, so a missing value fails loudly at startup. Supplied per-session via `$env:DB_PASSWORD`, with a gitignored `application-local.yml` as the longer-term plan. Secrets later moved to a gitignored `config/local.yml`, imported via `spring.config.import: optional:file:./config/local.yml`, with **verified precedence** env var > `local.yml` > `application.yml` default (a wrong env-var password failed startup; the file password alone succeeded).

**Scaffolding.** Gradle project generated with Web, Data JPA, Actuator, Postgres driver, and the Statemachine starter (via its BOM); the wrapper was pulled from Gradle's GitHub tag and checksum-verified against Gradle's published SHA-256. `GroqProperties` bound from `GROQ_API_KEY`. Actuator was added beyond the original spec — its `db` health indicator is what actually proves the Postgres connection.

**Decisions kept from this phase:** stay on Boot 3.5.16 (OSS support ended 2026-06-30, but Statemachine 4.0.2 — the latest — targets Framework 6.2, and no Boot-4-compatible Statemachine release exists); Gradle upgraded 8.14.3 → 9.7.1 (8 was past support), verified against Boot 3.5.16's plugin with a clean build and tests.

**Issues along the way:** a DB name mismatch (container used `springfield`, spec guessed `springfield_talent_pipeline` — default `DB_URL` corrected); a `bootRun` "exit -1" that was actually a live, serving JVM whose background task wrapper had simply been torn down (a pattern that recurred all session — Gradle's "non-zero exit value" line is never the root cause; look for `APPLICATION FAILED TO START`); a genuine port-8080 conflict from a stray leftover `java.exe`, killed once found; a `--stacktrace` log redirect that came out UTF-16 because PowerShell 5.1 defaults Unicode redirects (readable in VS Code, garbled in-terminal).

**Phase 0 checkpoint (2026-09-09 23:53Z):** `bootRun` sits at "EXECUTING 80%" indefinitely — normal, since the task never completes while the app is up. `GET /actuator/health` → 200, `db: UP`, \~2.9s boot. ✅

**Candidate sync (Phase 1).** `Candidate` entity; `phrases` modeled as `@ElementCollection` → a separate `candidate_phrase` table with `@OrderColumn` (this shape caused a false-alarm "missing field" investigation later — see Session 3). `SimpsonsApiClient` (RestClient), `CandidateUpserter` as its **own bean** (a self-invoked `@Transactional` method is bypassed by the Spring proxy and would let one bad record poison the batch), `CandidateSyncService` (serial pages, 200ms delay, page- and record-level isolation), `POST /api/candidates/sync`. Page-1 failure aborts the whole sync (502, since there's no page count without it); later-page failures skip and continue.

**Findings from the live API, all contradicting the original spec:**

- `status` has **7** values, not the spec's 3: `Alive 855, Deceased 136, Unknown 126, Fictional 50, Noncanon 9, Noncanon Deceased 4, Destroyed Icon 2` — no `Dead`. The spec's 3-value enum would have misfiled 201 records (17%); the enum was extended, with unmapped values falling back to `UNKNOWN` plus a warning. (A later check against the live app showed the enum as `ALIVE, DECEASED, UNKNOWN, FICTIONAL, NONCANON_DECEASED`.)
- Portrait CDN confirmed as `https://cdn.thesimpsonsapi.com/{200|500|1280}/character/{externalId}.webp`; other widths 403.
- Sparsity: age null in 94% of records, phrases empty in 77%, occupation populated 100%.

**Checkpoint:** 1,182/1,182 candidates created, 0 failed, 19.3s. Re-run (idempotency): `created 0, updated 1182`, same primary keys, `importedAt` preserved, `candidate_phrase` row count held at 492 (no accumulation). ✅

**Gap found and closed: `GET /api/candidates?q=`.** Listed in the README's API table but never assigned to a build phase, so it 404'd. Implemented as case-insensitive substring (`ILIKE`) over `name` and `occupation` — no FTS ranking, since this is a lookup tool and it sidesteps the `plainto_tsquery` AND-semantics issue found in Phase 2. Verified: `q=Homer` → 10 results, matching from occupation text and from the middle/end of names, not just prefix; `q=simp` → all Simpsons plus "Shary Bobbins" via occupation text; no `q` → 1,182 (matches the full sync count).

## Phase 2 — Requisitions, matching, state machine (2026-09-09)

**Execution.** Statemachine 4.0.2's API was verified via `javap` against the jar before coding (reactive API: `sendEventCollect`, `resetStateMachineReactively`, `ResultType`). Entities: `Requisition`, `JobApplication`, `StageTransition`. `PipelineStateMachineConfig` is the **sole** declaration of legal transitions; `PipelineTransitionRules` derives the allowed-next-stages list straight from the machine's own config for 409 response bodies, rather than maintaining a second copy; a `StageTransitionRecorder` listener writes the audit trail from actual transitions. `CandidateMatchRepository` runs a native Postgres FTS query with a GIN index added via `data.sql` (`defer-datasource-initialization`).

**Two structural decisions:** every incoming stage-transition request is checked against the machine's actual configured transitions before an event is sent, rather than mapping stage → event directly — that mapping is lossy (Sourced → Offer would fire `ADVANCE`, be accepted, and silently land on Screening instead). And one active application per candidate/requisition pair is enforced in the service layer, not a DB unique constraint, so re-application after a terminal stage stays possible.

**Requisition created:** Bartender, department "Moe's Tavern," keywords `bartender mixology customer service`, hiring manager Moe Szyslak (id `72235f00…`, later deleted during cleanup — see below).

**Matching, attempt 1 — fail.** All 5 candidates returned an **identical** `ts_rank` score (`0.01519817765802145`); order fell back to alphabetical, and Moe ranked 4th. Root cause: `plainto_tsquery` ANDs its terms, so "bartender tavern bar drinks" matched nothing. Noise: "Babysitting Service" matched via the shared "service" lexeme. Fix: rewrite the parsed query with `&` → `|` (keeps stemming/safe parsing).

**Matching, attempt 2 — ties resolved, new issue found.** Ranks differentiated (Brandt/Travolta/Titania 0.00760, Moe 0.00655, Babysitting Lady 0.00507), but two duplicate Bartender requisitions were also found and deleted — one of the deleted rows, `72235f00…`, was reused a moment later and caused the 404 investigation below.

**Matching, attempt 3 — the length-normalization / no-IDF limitation.** Query `bartender tavern owner` against the live Bartender fixture (`74c5eac3…`, keywords `bartender tavern bar drinks`) produced 4 score tiers. Moe topped it (matches all 3 terms). But **Brandt, an actual bartender, ranked below Richard Branson** ("Owner of Virgin"). Two compounding causes, confirmed with evidence:

1. `ts_rank` gives every matched term equal weight — Postgres has no corpus-wide IDF. Evidence: Al Gumble ("owner") ties Brandt ("bartend") **exactly** at `0.010132118128240108`, despite "owner" matching \~35 occupations and "bartend" only \~4.
2. Among ties on term-count, length normalization favors shorter occupation strings, so 2-lexeme "owner" occupations outrank 3-lexeme "bartender" ones.

IDF weighting was considered but not built — noted as unreliable at 1,182 short documents (it would have ranked "Babysitting Service" first on the earlier query). Documented as a known limitation; the AI candidate profile (Phase 3) is the semantic second signal that compensates.

**404 investigation (`POST /api/applications`).** Creating Moe → Bartender against the just-deleted `72235f00…` returned 404. Elimination ruled out route-mapping and package-scanning issues; a direct SQL check confirmed the row no longer existed — it had been removed in the matching-attempt-2 cleanup. **Lesson:** a 404 for a missing referenced entity looks identical to an unmapped route from the client side; a distinct error body was noted as an open item.

**State machine verification** (fixture `74c5eac3…`, application `c9e00207…`):

| Transition                                                                | Expected           | Actual |
| ------------------------------------------------------------------------- | ------------------ | ------ |
| SOURCED → SCREENING                                                       | allowed            | 200    |
| SCREENING → INTERVIEWING                                                  | allowed            | 200    |
| INTERVIEWING → HIRED (skips OFFER)                                        | blocked            | 409    |
| HIRED → SCREENING (a separate, pre-existing HIRED+SOURCED duplicate pair) | blocked (terminal) | 409    |

Transition body: `{"toStage": "<STAGE>", "note": "<optional>"}`; responses include `allowedNextStages`.

## Phase 3–4 — AI candidate profile & mock interview (2026-09-09–10)

**Phase 3 build.** Groq calls run with `reasoning_effort: low`, `reasoning_format: hidden` (JSON mode rejects `raw`); per-feature temperatures — profile 0.1, interview 0.8. Cache origin is surfaced in the response (`CACHED`, `GENERATED_FIRST_TIME`, `REGENERATED_KEYWORDS_CHANGED`, `REGENERATED_ON_REQUEST`); a `keywordsUpdatedAt` field was added to `Requisition` (not in the original spec) that advances only on a genuine text change, to drive regeneration correctly. A `PATCH /api/requisitions/{id}` endpoint was added at the user's request, since the keyword-invalidation path had no way to be exercised via the API otherwise.

**Issues found and fixed:**

- Groq returned **403 "Access denied"** with NordVPN on (NordLynx route, a datacenter/PacketHub ASN) — Groq hard-blocks VPN/datacenter IPs. VPN turned off; an unauthenticated call then correctly returned 401. (Scope: only _generation_ needs a clear network path — cached rows are served from Postgres with no call.)
- A suspected UTF-8/mojibake issue in stored phrases was a **false alarm** — the DB held clean UTF-8; a diagnostic Python script on Windows was reading stdin as cp1252.
- **Unstable fit scores** — the same input scored 20/40/55/60/70 across runs; lowering temperature 0.4→0.1 still gave 20–65. Root cause was an **unanchored scale**, not temperature: explicit bands (90–100 / 70–89 / 40–69 / 10–39 / 0–9, "choose the lower band" on a tie) fixed it to 55 across 5 reruns.
- 429s mid-batch — added retry (4 attempts, honors a fractional-second `Retry-After`, else 2/4/8s backoff); non-429 failures fail fast.

**Verification (Phase 3).** Moe → Bartender fit score **95/100** (fixture `c9e00207…`), rationale citing bartender/tavern/bar/drinks, `modelUsed: openai/gpt-oss-20b`. A deliberate mismatch — a new application, Moe → Elementary School Teacher (`5e5668a2…`) — scored **20**, rationale naming what's missing (teaching, school administration, elementary students); tokens 696 in / 132 out / 828 total (spec had estimated 300–600 in / 200–400 out — actual input ran higher, output lower, total still trivial). A same-input second call returned `cached: true`, `origin: CACHED`, same `generatedAt`, zero token usage. All 3 cache-origin paths verified live (11 profiles / 4 requisitions / 0 failures overall: Moe 95 bartender vs. 25 nuclear; Homer 55 nuclear vs. 20 bartender). A console-only `â` in "Moe's" was PowerShell code-page display, not a stored-data problem.

**Phase 4 build.** `MockInterviewSession`/`Turn`, `MockInterviewGenerator`, `MockInterviewStore`, `RecruiterFeedback` (kept as a separate entity from the AI's own assessment, per the Session-1 decision). Regenerating **appends** a new session rather than overwriting (unlike the profile, which overwrites); failed generations are recorded as `FAILED` sessions via `REQUIRES_NEW`; the Groq call runs outside any transaction.

**Issue:** the session-list endpoint 500'd — `turns` was a lazy collection accessed after the transaction closed. Fixed with `@EntityGraph` on the list finder; `open-in-view: false` is what made the bug fail loudly instead of silently.

**Verification (Phase 4).** `POST /api/applications/c9e00207…/mock-interview` → `status: GENERATED`, `modelUsed: openai/gpt-oss-120b`, tokens 806 in / 1,103 out / 1,909 total (inside the 1.5–2.5K estimate), `overallRating: 4`. Questions were role-specific (inventory control, consistency under volume, de-escalation, POS adoption); voice skewed toward a generic competent bartender rather than Moe specifically, with one good in-character callback ("What's the matter, Homer?"). The assessment made an inference the profile feature couldn't — Moe owns his own bar, so leaving it is a retention risk. Separately, 9 sessions / 50 turns / 0 failures were produced overall, with Burns/Bart/Wiggum on "Chief of Police" producing clearly distinct voices (ratings 2/1/3) and a deliberately dissenting recruiter feedback (1/5 vs. the AI's 2/5), both persisted.

**Milestone.** All phases 0–4 built, 67 tests green (later 76 once idempotency fixes landed in Session 3), 9 tables. Every endpoint in the original spec implemented except `?q=` (closed above). Docs reconciled against the two live-API-vs-spec contradictions (status enum, endpoint gap), the FTS AND-semantics trap, the unanchored-score instability, and the VPN gotcha.

## Session 3 — Post-Phase-4: docs, cleanup, and the frontend (2026-09-09–10)

**`phrases` "gap" — a false alarm.** `SELECT phrases FROM candidate LIMIT 5` returned `column "phrases" does not exist`, read initially as data loss. Investigation nearly produced a fix spec (a new `text[]` column) before `DATA_MODEL.md`/`AI_FEATURES.md` surfaced that both AI prompts already depend on `phrases`, which didn't fit a "cosmetic, skip it" framing. The actual finding: `phrases` was fully implemented as an `@ElementCollection` mapped to a separate `candidate_phrase` table (492 rows across 271 candidates, matching the 77%-empty rate found in Phase 1) — the diagnostic query had simply queried the wrong table. Both AI generators call `getPhrases()` correctly (`MockInterviewService` via `findWithPhrasesById`/`@EntityGraph`, since its Groq call runs outside a transaction); the interview prompt handles the empty case explicitly. Runtime proof: Clancy Wiggum's stored phrase "Book 'em, Lou." appeared in a generated transcript, tracing the full path from API → DTO → upsert → `candidate_phrase` → prompt → transcript. **No code change** — only a doc clarification that phrases live in `candidate_phrase`, one row per phrase. Lesson: check the JPA mapping before diagnosing a missing field.

**Test-data cleanup spec.** Decided to clean data before any UI existed, since a frontend would make stray rows visible — the pre-existing HIRED+SOURCED duplicate pair in particular would look like it broke the terminal-HIRED rule. Spec: delete orphaned "Bartender - Retest" requisitions (flagging any with real applications instead of cascading), and delete the seeded duplicate pair, then recreate that state through the API/state machine rather than SQL. Outcome: 3 then 2 orphaned duplicates deleted (0 dependents each); the Moe duplicate pair turned out to have one application already at INTERVIEWING with a real profile and 6-turn interview (not SOURCED as first assumed) — the "bypassed service layer" premise was wrong; it had been created via the API to verify the re-application rule. Only the second application was deleted (FK-ordered transactional delete), and the 409 regression was re-verified on the surviving HIRED row. Backups were taken with `pg_dump --column-inserts` before each destructive step.

**Frontend scoping.** The project was backend-only by design (the Session-1 API/C# split), and the planned C# companion turned out to be an unrelated separate project. Three tiers considered: Swagger UI (\~30–60 min, dev diagnostic — decided on, never executed), a read-only React demo (1–2 sessions), a full interactive frontend (1–2+ weeks). Chosen: a demo-tool build extended into an interactive flow (search → requisition → apply → interview → hire/reject), on React/Vite to match the rest of the portfolio.

**Demo flow design.** One candidate's story across all phases, with the fit score added back in (it's the strongest single artifact and gives "Apply" a payoff). Reject modeled natively via the state machine's `ADVANCE`/`REJECT`/`WITHDRAW` events. Built staged, with a checkpoint: Stage A (scaffold + search, proven in a real browser) before Stage B (everything else).

**Endpoint discovery** (PowerShell `Select-String` over the controllers, no grep on Windows) confirmed the API surface: search, sync, requisition CRUD (+`PATCH`), matches, apply, transition, history, ai-profile (POST/GET, `?refresh=`), mock-interview (POST/GET), feedback (POST/GET). `/requisitions/{id}/matches` was added as a 7th demo step — it's the main way to pick a candidate, and shows the FTS engine (and its documented length-normalization quirk) live.

**Stage A: scaffold + search.**

- **Node 12 blocker.** Installed Node was v12.17.0 (EOL April 2022); only Vite 2.9.18 runs on it. Rather than build on an EOL Vite, upgraded — initially proposed as Node 20 or 22, corrected to **Node 24 LTS** (20's LTS ends 2026-04-30; 24 "Krypton" is Active LTS through May 2028). Installed via winget after a 20+ minute stall traced to a hidden UAC prompt; confirmed Node **24.19.0**.
- **Pre-work while blocked:** measured `/api/candidates` behavior directly — no `q` or blank `q` → 200, all 1,182 (293 KB); a real query → filtered results; a no-match query → 200 + `[]`. This fixed two UI rules: cap the rendered list at 50 with a true total count, and treat "no results" (200+`[]`) and "error" as separate states.
- **Verification approach:** Playwright + headless Chromium (a real rendering engine, fewer tokens than a manual loop, reusable for Stage B).
- **Results:** 13/13 checks + 3 error-path checks passed; Vite 8 up in 639ms; proxy worked first try (no CORS/port issues); production build 222 KB JS / 70 KB gzipped. The **first Playwright run failed with 7 failures that were the harness's fault** — `settle()` matched the previous render's list, reading stale DOM; fixed by waiting for the API response and the loading indicator's detachment, not a selector alone. `net::ERR_ABORTED` from React 19 StrictMode's double-invoked effects was expected and filtered as info. Also verified: backend unreachable, backend 500, and recovery — 0 console errors.
- **Rules Stage B inherited:** relative `/api` paths only; four render states (loading/error/empty/success); assert on committed renders and network calls, never a selector alone.

**Mid-session git check.** `git log` showed the project **already pushed** (a session-summary claim to the contrary was stale). `.gitignore` excludes `docs/` repo-wide, confirmed intentional (private planning space). Secrets exclusions verified (`.env*`, `application-local.yml/.properties`, `config/*.yml`). An unrelated Lego-manager project sat untracked nearby and was left alone.

## Frontend Stage B, and the ai-profile race condition

**Spec corrections found by probing before building** (both would otherwise have broken the UI silently):

1. `toStage` must be **upper-case** — Jackson matches the enum by exact name, so `"Screening"` returns 400 and `"SCREENING"` returns 200; the original spec used title case.
2. **`HIRED` is reachable only from `OFFER`** — a single-call Hire button 409'd on first run. Hire now walks all four legal transitions (SOURCED → SCREENING → INTERVIEWING → OFFER → HIRED) sequentially, showing the path in the UI; Reject stays a single call.

**Results: 26/26 checks against the live backend with real Groq generations.**

| Step              | Result                                                                                                                                                                        |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Requisition       | POST 201                                                                                                                                                                      |
| Matches           | 6 ranked; Moe first; "Bar owner" (0.00959) outranks three bartenders (0.00760) — a shorter document, per the Phase-2 length-normalization finding. Left unmodified in the UI. |
| Apply             | 201                                                                                                                                                                           |
| Fit score         | 95/100, 839 tokens                                                                                                                                                            |
| Interview         | 7 turns, assessment 4/5, 1,777 tokens                                                                                                                                         |
| Hire              | 4 sequential transitions, all 200                                                                                                                                             |
| Repeat transition | 409, `currentStage: HIRED`, `requestedStage: REJECTED`, `allowedNextStages: none`                                                                                             |

A new verification wrinkle: Chromium logs every non-2xx fetch as a console error, so the deliberate 409 above tripped a "no console errors" check — filtered explicitly, with the test asserting it fires **exactly once**.

**Bug: 500 on fit score after Apply.** `PSQLException: duplicate key value violates unique constraint "uk_ai_profile_application"` — two `POST .../ai-profile` calls fired for the same application, most likely React 19 StrictMode double-invoking the auto-trigger effect; client-side aborting doesn't help once the request has reached the server. Fix, applied regardless of root cause: backend returns the existing profile when `refresh=false`, and catches the constraint violation as a fallback if a race still slips through (`generateOrGet` no longer `@Transactional`, with a dedicated `AiCandidateProfileStore` bean doing the `REQUIRES_NEW` write + re-read); this reintroduced a self-inflicted `LazyInitializationException` on `phrases`, fixed with `findWithPhrasesById`. Frontend: a `requestedFor` ref guard per `applicationId`, cleared on failure. Verified with two concurrent curl POSTs → both 200, one row; Playwright asserts exactly one `ai-profile` POST at the network level. 76 backend tests green after the fix.

**Demo data left behind:** three `Bartender demo <timestamp>` requisitions (kept, harmless); Brandt's application moved to SCREENING by a casing probe (reverted later, see the Offer-stage section).

## Offer stage — design and implementation

**Terminal-panel UX issue that prompted it.** Post-Hire copy read "HIRED is reachable only from OFFER, so hiring advances one legal stage at a time… Try moving again and the state machine should refuse with a 409" — confusing, since OFFER was a stage the user never actually interacted with (the Hire button had already handled it correctly; only the copy was wrong). A copy fix was proposed; the user asked for a real, interactive Offer stage instead.

**Design iteration.** First proposal was an LLM accept/decline decision anchored on salary, fit score, and temperament. The chosen approach instead uses **deterministic code against a public salary database (BLS)** — no LLM in the decision — fitting the project's "assist not replace" philosophy and avoiding the instability the unanchored fit-score scale had already shown in Phase 3.

**Decisions:**

| Question                    | Decision                                               | Why                                                                                             |
| --------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| Declined-offer target stage | Reuse `WITHDRAWN`                                      | No state-machine changes; keeps REJECTED = recruiter-initiated, WITHDRAWN = candidate-initiated |
| Input                       | Recruiter enters a salary amount                       | Concrete, demoable, gives the rule a real input                                                 |
| Retry after decline         | One-shot                                               | Matches real-world semantics and the no-backward-transitions rule                               |
| Data source                 | BLS OEWS national table, seeded locally                | No runtime dependency or rate limit; matches the app's local-Postgres pattern                   |
| Occupation → SOC mapping    | Postgres FTS (`ts_rank`) against SOC titles            | Reuses the technique already proven in Phase 2; no hand-curated table, no LLM                   |
| Unmappable occupations      | Fall back to SOC `00-0000` (All Occupations), labelled | Feature always works                                                                            |
| "Within range"              | 10th–90th percentile, national                         | No real geography in Springfield                                                                |
| Rationale text              | Templated sentence with real numbers                   | Deterministic, no LLM prose                                                                     |

New entity `OfferDecision` (`applicationId, offerAmount, matchedSocCode, matchedOccupationTitle, wageRangeLow, wageRangeHigh, decision, decisionRationale, decidedAt`). `POST /api/applications/{id}/offer` validates `currentStage == OFFER` (409 otherwise), resolves the wage range, decides, persists, then drives the existing transition service to HIRED or WITHDRAWN — it wraps the state machine rather than bypassing it. Frontend: Hire auto-walks to OFFER and pauses for a one-shot salary form.

**Data acquisition.** BLS blocked automated download (403 on every path/header combination tried) — the source file (`national_M2025_dl.xlsx`, 283 KB) was supplied manually. Inspecting the real file rather than assuming its shape: 32 columns, 1,401 rows; used columns `OCC_CODE`, `OCC_TITLE`, and the annual percentile columns (`A_PCT10…A_PCT90`, annual because offers are salaries). `O_GROUP` breaks into total (1) / major (22) / minor (94) / broad (454) / detailed (830) — **only total + detailed imported** (831 rows), so a candidate can't match an umbrella group like "Management Occupations." BLS's `*` suppression marker imports as NULL, treated as unmatched rather than an invented bound. Seeded via `occupation_wage_seed.sql` with `ON CONFLICT DO NOTHING` (idempotent on a fresh clone).

**Verified outcomes:**

| Case                       | Result                                                                        |
| -------------------------- | ----------------------------------------------------------------------------- |
| Moe, $45,000               | ACCEPTED → HIRED; matched `35-3011 Bartenders` ($20,110–$73,770)              |
| Moe, $15,000               | DECLINED → WITHDRAWN; "falls below the typical national range for Bartenders" |
| Marge, "Unemployed"        | Fell back to `00-0000`, labelled, no crash                                    |
| Wrong stage / repeat offer | 409                                                                           |

Sample SOC mappings: Homer "Safety Inspector" → Health and Safety Engineers; Edna → Elementary School Teachers; Burns → Power Plant Operators.

**Bug: offer form silently did nothing.** Native click fired, no `submit` event. Cause: `min="1" step="1000"` on a `type=number` input — the step base defaults to `min`, so valid values were 1, 1001, 2001…, and $45,000 was a step mismatch that constraint validation blocked silently. Fixed by removing `step`; caught because the Playwright check waited on the POST response rather than the DOM alone.

**Result:** 17/17 Playwright checks, 76 backend tests. `WITHDRAWN` got its first real UI trigger, giving the demo three outcomes: HIRED, REJECTED, WITHDRAWN.

**Manual validation:** at $45,000, a stuntman and juggler were accepted and an engineer declined — consistent with the engineer's real range being well above $45k (the stuntman/juggler matched SOC codes weren't recorded). Brandt's casing-probe transition was reverted by deleting the probe's `StageTransition` row and resetting `current_stage` to SOURCED directly in SQL, after tracing timestamps to confirm the application itself — and its one real AI profile, fit 95 — predated the probe by a day and was not itself a probe artifact.

## Final push

`git status` before the commit: modified `README.md` (root + project), `AiCandidateProfileService.java`, `JobApplicationController.java`, `application.yml`, `data.sql`, `AiCandidateProfileServiceTest.java`; untracked `frontend/`, `resources/`, `ai/AiCandidateProfileStore.java`, `offer/`, `src/main/resources/db/`.

Decisions: `frontend/.gitignore` covers `node_modules/`, `dist/`, `screenshots/`, `.vite/`; screenshots stay out of the repo. `resources/national_M2025_dl.xlsx` (283 KB) is **included** — it lets someone check the seed against its source, which matters since the BLS API was unreachable from the build environment (excluding it was considered and rejected — the file's inclusion stood). Both README diffs were reviewed manually; changes were staged explicitly and checked with `git diff --staged --stat` before committing. One commit covered both the idempotency fix and the Offer feature, since they touched overlapping files: "Add demo frontend and Offer stage (BLS-backed deterministic accept/decline)," with the frontend, Offer stage, and ai-profile race fix in the body. Pushed — project complete for this round.

## Groq → Gemini migration (2026-09-23)

**Request and constraints.** Swap the LLM behind both AI features from Groq to Google Gemini `gemini-3.1-flash-lite` (key already placed in `config/local.yml` by the user); neither key may be revealed anywhere; update the README; nothing committed by Claude.

**Discovery.** A regex search across the repo for `groq|gpt-oss|qwen` matched 162 occurrences in 28 files. Architecture: `application.yml`'s `groq:` block; `GroqProperties`, `GroqClient` (Spring `RestClient`, Bearer auth, `/chat/completions`, 429 retry, structured JSON via `response_format: json_schema`), `GroqChat`, `GroqException` (→ HTTP 502); callers `CandidateProfileGenerator` and `MockInterviewGenerator`. Models before the swap: profile `openai/gpt-oss-20b`, interview `openai/gpt-oss-120b`. Groq-specific request fields identified: `reasoning_format: "hidden"` and `reasoning_effort`. The frontend had user-facing "generating with `openai/gpt-oss-…`" strings and comments referencing "real Groq call."

**Decisions:**

| Decision                                                                                                                  | Reason                                                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Never opened `config/local.yml`; read only key _names_ via a match-only regex                                             | Confirms the property prefix (`gemini`) without the key ever entering the transcript                                                         |
| Gemini's OpenAI-compatible endpoint (`generativelanguage.googleapis.com/v1beta/openai/chat/completions`, Bearer auth)     | Same wire format — client logic (structured output, 429 retry) carries over, no new SDK                                                      |
| Renamed `Groq*` → `Gemini*` everywhere (classes, config prefix, env var `GEMINI_API_KEY`, comments, tests, frontend text) | `GroqClient` calling Gemini would be misleading                                                                                              |
| Plain `mv`, not `git mv`, for file renames                                                                                | User handles git themselves                                                                                                                  |
| Both features on `gemini-3.1-flash-lite`                                                                                  | Only one model was specified                                                                                                                 |
| Dropped `reasoning_format`; kept `reasoning_effort: low`                                                                  | `reasoning_format` is Groq-only; Gemini 3 thinks before replying and those tokens count against `max_completion_tokens`, so effort stays low |
| `docs/AI_FEATURES.md` left untouched                                                                                      | Gitignored, out of scope                                                                                                                     |
| Stale README figures removed rather than carried over (gpt-oss token counts, the Groq VPN note)                           | Those facts described the old provider/model                                                                                                 |

**Execution.** Moved `GroqChat/Client/Exception/Properties(Test)` to `Gemini*`; one `sed` pass over source, frontend, scripts, README, `.gitignore`, `config/local.yml.example` swapping env var, class, and model names. Hand-fixes after the sed pass: removed `reasoning_format` from `GeminiChat`/`GeminiClient`; corrected a bogus base URL the sed had produced (`api.gemini.com/openai/v1`); replaced a leftover `gsk_…` placeholder in the example config; updated the `reasoningEffort` javadoc and the properties test's base-URL assertion; updated `application.yml` comments.

**Verification, round 1 (no DB).** 76 tests, 75 passed — `contextLoads` failed only because Postgres wasn't running (unrelated to the change). `git check-ignore` confirmed `config/local.yml` stays ignored. A key-pattern scan (`AIza…`, `gsk_…`, `xai-…`) across the monorepo found no leaks in this project; flagged (not fixed) other projects' secret files for a gitignore check. A direct `curl` to the live Gemini endpoint (key read from the shell, redacted from output) returned 200 with valid schema-matching JSON, confirming `response_format`/`json_schema`/`strict`/`reasoning_effort`/`temperature`/`max_completion_tokens` are all accepted; found the response carries an extra `extra_content.google.thought_signature` field the old response records don't model (tolerated by Jackson) and that **thinking tokens count toward `total_tokens` but not `completion_tokens`** (31 prompt + 29 completion ≠ 185 total).

**Verification, round 2 (Postgres up).** 76 tests, 0 failures, including `contextLoads`. Port 8080 conflicted with an unrelated project's console — ran on 8081 instead, which meant the frontend's hardcoded proxy (`vite.config.js` → `localhost:8080`) couldn't be exercised, so the Playwright flow was **not** run against Gemini. `/actuator/health`: UP. Live end-to-end against Gemini: sync 60/60 pages, 1,182 records, 30.7s (README says "\~20s," left as-is); created a Bartender requisition, matched Moe Szyslak; `ai-profile` → fitScore 100, `modelUsed: gemini-3.1-flash-lite`, 542/97/757 tokens (prompt/completion/total — total doesn't sum, per the thinking-token finding above); `mock-interview` → GENERATED, ≥5 turns, 612/856/1,578 tokens. App log searched for the literal key, `AIza`, and `Bearer` — 0 matches — then the app was stopped and the log deleted.

**Issues along the way:** the sed-produced bad base URL and `gsk_` placeholder (caught on review, hand-fixed); `contextLoads` failing without a DB (expected, passed once Postgres was up); a first end-to-end script guessing the wrong JSON field (`id` instead of `candidateId`); an `endpoints.txt` file that printed as garbled spaced text because it's UTF-16 (ignored, used the README's tables instead); the port-8080 conflict.

**State at end of session.** Working tree not committed by Claude; by 2026-09-26 `git status` showed no pending changes for the project, suggesting the user committed it separately. Left in the local dev DB: 1 requisition, 1 application, 1 AI profile, 1 mock interview.

## Consolidated open items, errata, and recurring lessons

**Open items outstanding as of the last session (2026-09-23):**

- `config/local.yml` still has an unused `groq:` block with the old key — should be removed.
- `docs/AI_FEATURES.md` and the rest of the gitignored `docs/` folder were never checked or updated for the Gemini migration; code comments still point to that doc.
- Frontend Playwright flow (`verify`, `verify:flow`, `verify:offer`) has never been run against Gemini — blocked by the hardcoded `:8080` proxy port colliding with an unrelated project.
- Token costs per feature on Gemini are unmeasured, and the existing log line ("N prompt + M completion = T tokens") now prints a total that doesn't sum, since thinking tokens count toward the total but not completion tokens.
- Gemini rate-limit behavior (429s, whether `Retry-After` is sent) is untested; the Groq-era retry/backoff was carried over unchanged.
- Other secret files found by the migration's key scan (`Agentic_AI/*/.streamlit/secrets.toml`, `Batcave_IDS/.env`, `SuperHeroOps/.env`) were never confirmed gitignored.
- `bin/` (gitignored Eclipse output) still contains stale compiled Groq config — harmless.
- README says sync takes \~20s; a measured run took 30.7s.

**Open items from earlier sessions, status where known:**

- Swagger UI (Tier 1 frontend) — decided on, never implemented.
- `phrases` missing from list responses — resolved as a false alarm (Session 3); doc clarification applied.
- Package namespace placeholder — resolved as `com.jivejong.springfieldtalentpipeline`.
- Mock-interview turn count ≥5 — confirmed at 7 turns in Stage B verification.
- Whether the `vEthernet (WSL)` firewall-profile fix was actually applied — never confirmed.
- Distinct 404 error bodies for "missing entity" vs. "unmapped route" — not built.
- HIRED-blocks-reapplication rule — the specific test (a candidate/requisition pair whose only application is HIRED) was never run; see the errata below.
- Demo requisitions (`Bartender demo <timestamp>` ×3) left in the dataset — harmless, cleanup optional.

**Errata (corrections to conclusions stated earlier in the source chats, kept here so they aren't inherited):**

- The IDF disagreement in Phase 2 was overstated in the moment. The original claim — `ts_rank` has no cross-document IDF, so "owner" and "bartend" count equally — was correct; the Branson-over-Brandt ordering comes from length normalization **on top of** that equal weighting, not instead of it. Both factors are real and both are documented above.
- The HIRED+SOURCED duplicate pair found in Phase 2 was assumed to be a seeding artifact because HIRED rejected every transition tried against it — that doesn't actually follow; terminal-state enforcement says nothing about whether the _create-time_ duplicate check treats HIRED as blocking. The spec reads as if it should block (HIRED is neither Rejected nor Withdrawn). The intended test — find a pair whose only application is HIRED and try to create a new one — was never run.
- A diagnostic query in the Phase-2 404 investigation assumed a `requisition.created_at` column that doesn't exist in the schema; minor, but worth not repeating.

**Recurring lessons, observed across every session:**

1. Verify against the live system, not the spec — the Simpsons status enum, the CDN path, enum casing, the Hire-path reachability, and several BLS assumptions were all spec-wrong.
2. Unanchored LLM scales are unstable; temperature is not the fix, explicit bands are.
3. Assert on committed renders and on network calls, not on selectors or rendered text alone — the stale-DOM bug and the double-POST bug would both have passed a DOM-only check.
4. React 19 StrictMode's double-invoked effects reach the server for POSTs — guard on the client with a ref, and make the backend idempotent regardless.
5. Removing `@Transactional` exposes lazy JPA collections — fetch eagerly with `@EntityGraph`.
6. Check dependents (and re-check the premise) before deleting anything — two "stray" artifacts turned out to be legitimate data.
7. `ts_rank` carries no corpus statistics; length normalization is the only discriminator among candidates with an equal term-match count.
8. Check the actual JPA mapping before diagnosing a "missing" field — a `column does not exist` error can mean "different table," not "no data."

## Appendix — test fixtures and reference IDs

| Entity                                        | ID                                                                             | Notes                                                                  |
| --------------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Candidate — Moe Szyslak                       | `3bd4170d-28f5-47b7-adae-e08efe76790d`                                         | externalId 16                                                          |
| Candidate — Homer Simpson                     | `5db7f252-06ef-4c98-98a5-19d36d62b182`                                         | externalId 1                                                           |
| Requisition — Bartender (original, deleted)   | `72235f00-d3a1-41e9-850e-c650f6a51121`                                         | Removed during duplicate cleanup; caused the Phase-2 404 investigation |
| Requisition — Bartender                       | `74c5eac3-af11-4e42-8e26-1a80c9e550c6`                                         | Keywords `bartender tavern bar drinks`                                 |
| Requisition — Elementary School Teacher       | `12e83503-62ab-41b9-8947-32aa3910f6df`                                         |                                                                        |
| Requisition — Chief of Police                 | `ca3f3106-568f-4fe0-acae-c60f08c17965`                                         |                                                                        |
| Requisition — Nuclear Safety Inspector        | `0fb5d66c-0ff1-4c9d-a322-fbdc2947bca5`                                         |                                                                        |
| Requisition — Bartender - Retest (×2)         | `1c30ff0d-5cd0-4e96-ba29-bad03b2e979f`, `37f19fd9-7587-4a48-8b2a-df588f914012` | Duplicate rows, cleanup candidates                                     |
| Application — Moe → Bartender                 | `c9e00207-e5f3-4897-8910-324acade5f26`                                         | Walked to INTERVIEWING; used across Phase 2–4 and Stage B verification |
| Application — Moe → Bartender (HIRED)         | `0bbaf72b-33b1-4ee3-bb84-fe787d1f327a`                                         | Pre-existing; origin unresolved, see Errata                            |
| Application — Moe → Elementary School Teacher | `5e5668a2-4fc7-49a9-b340-2f66752627f5`                                         | Deliberate low-fit-score test case                                     |
| Mock interview session                        | `e206a246-44db-4b72-a0df-5117786ab97b`                                         |                                                                        |
| Requisition — Bartender (Gemini-era)          | `764740df-4fe8-4f8c-97e2-50f3d6071fb5`                                         | Created during Gemini verification                                     |
| Application (Gemini-era)                      | `1d2f87d2-5ab4-49d4-95c0-279b7aa3e1bd`                                         | Moe matched against the Gemini-era Bartender requisition               |
