# Springfield Talent Pipeline

A Spring Boot REST API modeling a full recruiting pipeline (ATS) — sourcing, stage tracking, and interview feedback — backed by Simpsons character data instead of real candidate PII. AI features generate a per-role candidate profile/fit score and run a structured mock interview in character, grounded in each candidate's actual API-sourced data.

This repo began as a **planning handoff**: the docs here are a technical spec meant to be handed to Claude Code (or worked through manually) to implement. They capture the decisions already made so implementation doesn't have to re-derive them.

**Implementation is complete.** All five phases are built and checkpoint-verified against real infrastructure, the live API and real Groq calls. See `IMPLEMENTATION_PLAN.md` for what each checkpoint actually produced, including the places the live systems contradicted this spec. See `IMPLEMENTATION_PLAN.md` for per-phase status, and the project's root `README.md` for how to actually build and run it. Where the live API contradicted this spec, the docs have been corrected and the correction called out inline rather than quietly rewritten.

## Why this project

Portfolio piece demonstrating enterprise Java/Spring idioms: proper domain modeling with an enforced state machine, a from-scratch structured-generation AI feature (not just a chatbot wrapper), and a genuinely safe dataset for an ATS demo — no real candidate data required. The pun in the name is intentional.

## Tech stack

- **Java 21 / Spring Boot 3.5.16**, built with **Gradle 9.7.1** (via the wrapper — no local Gradle install required). The Boot 3.5 pin is forced by Spring Statemachine, which has no Boot 4 build — see `PIPELINE_STATE_MACHINE.md`
- **Spring Data JPA + PostgreSQL** for storage
- **Spring Statemachine 4.0.2** for the pipeline stage transitions (see `docs/PIPELINE_STATE_MACHINE.md`)
- **Postgres full-text search** (`ts_vector`/`ts_rank`) for candidate-to-requisition matching
- **The Simpsons API** (`thesimpsonsapi.com`) — free, no auth, source for the candidate pool. 1,182 characters over 60 pages, synced in ~20s
- **Groq API** (`openai/gpt-oss-120b` for interviews, `openai/gpt-oss-20b` for scoring) — free tier, for both AI features. `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` were deprecated by Groq in June 2026 (effective for free/developer tier); see `docs/AI_FEATURES.md` for the migration notes and current model choice.

## Important design decisions baked into this spec

1. **The mock interview is a single structured generation call, not a multi-turn chat.** Per the "fixed set of ~5-8 questions" scope decision, the whole interview (questions + in-character answers + closing assessment) is generated in one Groq call and stored, rather than resending growing conversation history turn by turn. This is both simpler to build and meaningfully cheaper — see `docs/AI_FEATURES.md` for the token math.
2. **AI-generated content is cached, not regenerated on every view.** Profile/score and interview transcripts are stored on first generation and only redone on explicit request or underlying data change — same cache-aside pattern used elsewhere in this portfolio.
3. **The AI's own interview assessment and the human recruiter's feedback are kept as separate fields**, not merged into one — they represent different things (the AI's in-character performance summary vs. your actual judgment reviewing the transcript).

## Licensing & attribution

Character data comes from `thesimpsonsapi.com`, which sources from [The Simpsons Wiki](https://simpsons.fandom.com/wiki/Simpsons_Wiki) under CC BY-SA. Credit line for the README:

> Character data provided by [The Simpsons API](https://thesimpsonsapi.com), sourced from The Simpsons Wiki (CC BY-SA).

This is a non-commercial portfolio/demo project and isn't affiliated with or endorsed by Fox, Disney, or the show. Worth keeping that line visible in the README rather than just in this doc.

## Repo structure

```
SpringfieldTalentPipeline/
├── README.md                     # how to build and run
├── .gitignore
├── .gitattributes
├── build.gradle
├── settings.gradle
├── gradle.properties
├── gradlew
├── gradlew.bat
├── gradle/wrapper/               # Gradle wrapper — no local Gradle install required
├── config/
│   ├── local.yml.example         # committed template
│   └── local.yml                 # GITIGNORED — the one place real secrets live
├── docs/
│   ├── README.md                 # this file — the spec overview
│   ├── DATA_MODEL.md
│   ├── SIMPSONS_API.md
│   ├── PIPELINE_STATE_MACHINE.md
│   ├── AI_FEATURES.md
│   └── IMPLEMENTATION_PLAN.md
└── src/
    ├── main/
    │   ├── java/com/jivejong/springfieldtalentpipeline/
    │   │   ├── SpringfieldTalentPipelineApplication.java   # @SpringBootApplication entrypoint
    │   │   ├── candidate/    # Candidate, SimpsonsApiClient, sync, search
    │   │   ├── requisition/  # Requisition, full-text-search matching
    │   │   ├── pipeline/     # JobApplication, StageTransition, Statemachine config, RecruiterFeedback
    │   │   ├── ai/           # GroqClient, AiCandidateProfile + scoring, MockInterviewSession/Turn
    │   │   ├── config/       # GroqProperties, SimpsonsApiProperties
    │   │   └── web/          # REST controllers, one per resource
    │   └── resources/
    │       ├── application.yml   # Postgres, base URLs, model choices — no secrets
    │       └── data.sql          # the full-text GIN index; a functional index cannot be a JPA annotation
    └── test/
        └── java/com/jivejong/springfieldtalentpipeline/   # mirrors main/ package-for-package
```

Package name above was a placeholder in the original spec; it resolved to `com.jivejong.springfieldtalentpipeline` (group `com.jivejong`), matching the repo owner's handle, there being no other JVM project in the portfolio to copy a convention from. The pipeline entity is named `JobApplication`, not `Application`, to avoid colliding with `SpringfieldTalentPipelineApplication`/Spring's own `ApplicationContext` vocabulary — see `docs/DATA_MODEL.md`. `RecruiterFeedback` sits in `pipeline/` rather than `ai/` on purpose: it is the one verdict in the system that no model produced.

## API surface

All implemented. Every endpoint below was exercised against real data during its phase's checkpoint.

### Candidates

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/candidates/sync` | Full sync from The Simpsons API; upserts on `externalId`, ~20s |
| `GET` | `/api/candidates?q=` | Search/list. Case-insensitive substring on name and occupation; whole pool when `q` is omitted |

### Requisitions and matching

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/requisitions` | Create a job opening |
| `GET` | `/api/requisitions/{id}` | Read one |
| `PATCH` | `/api/requisitions/{id}` | Partial update. Changing `targetKeywords` marks cached AI profiles stale |
| `GET` | `/api/requisitions/{id}/matches` | Full-text-search-ranked candidate matches |

### Applications and the pipeline

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/applications` | Create an application (candidate ↔ requisition). 409 if one is already active |
| `GET` | `/api/applications/{id}` | Read one, including the stages currently reachable |
| `POST` | `/api/applications/{id}/transition` | Move to a new stage. **409** with the allowed stages on an invalid move |
| `GET` | `/api/applications/{id}/history` | Audit trail, oldest first |

### AI features

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/applications/{id}/ai-profile` | Generate or return the cached profile + fit score. `?refresh=true` forces regeneration |
| `GET` | `/api/applications/{id}/ai-profile` | Cached profile only; never calls the model. 404 if none yet |
| `POST` | `/api/applications/{id}/mock-interview` | Generate an interview. Each call **adds** a session rather than replacing one |
| `GET` | `/api/applications/{id}/mock-interviews` | Every attempt for this application, newest first |
| `GET` | `/api/mock-interviews/{sessionId}` | One full transcript |

### Recruiter feedback

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/applications/{id}/feedback` | The human's own rating and comments, stored separately from the AI's |
| `GET` | `/api/applications/{id}/feedback` | Feedback for this application, newest first |

### Status codes worth knowing

| Code | When |
|---|---|
| `409` | An invalid stage transition (body carries the reachable stages), or a second active application for the same candidate/requisition pair |
| `502` | The Simpsons API or Groq is unreachable — the fault is upstream, not the caller's |
| `400` | Missing `targetKeywords`, a feedback rating outside 1–5, a missing `toStage` |

## Prerequisites

1. **Groq API key** — free tier, via console.groq.com. Not needed until Phase 3; Phases 0-2 run without one.
2. No signup needed for The Simpsons API — just a reasonable request pace during the initial sync (see `docs/SIMPSONS_API.md`).
3. JDK 21 and a local Postgres — see the project's root `README.md` for the concrete setup.

## Where to start

Read `docs/IMPLEMENTATION_PLAN.md` — phased with a checkpoint at the end of each phase, same pattern used on the other portfolio projects: verify against real data before building the next layer on top.
