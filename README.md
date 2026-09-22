# The Jive Repo

**Jong Lee ("Jive Jong")** — Solutions Architect / AI Engineer / Data Engineer / Software Engineer | 30+ years in IT | Northern Virginia

This is the public, working portfolio behind my resume: production-style software engineering, applied AI/LLM engineering, Data Engineering, MLOps, and technical architecture, with the reasoning behind each decision written up alongside the code. If you found this from LinkedIn, Buffer Overflow, or Translation Layer — this is home base. **Recruiters and hiring tools:** see [Skills](#Skills) below for a structured breakdown, and [Contact](#Contact) to reach me directly.

---

## What's Here

### [`/SpringfieldTalentPipeline`](./SpringfieldTalentPipeline)

A Spring Boot REST API modeling a full recruiting pipeline (ATS) — sourcing, ranked matching, stage tracking, AI candidate scoring, structured mock interviews, and salary offers — backed by Simpsons character data instead of real candidate PII. Java 21, Postgres full-text search, a Spring Statemachine that actually enforces its transition table, LLM output parsed against a JSON schema rather than scraped from prose, and offer accept/decline decided arithmetically against BLS national wage data. A React front end demonstrates the whole flow end to end, verified by driving a real browser rather than assuming a build implies a working page. Built in phases against real infrastructure — the live source API, real Postgres, real model calls — with the places those systems contradicted the design written up alongside the code they explain. See the [project README](./SpringfieldTalentPipeline/README.md).

### [`/SuperHeroOps`](./SuperHeroOps)

A Blazor Server app pairing real Chicago Police Department crime data with a seeded, fictional superhero roster in a synthetic "intervention" model — deploy a hero to a neighborhood and see a deterministic, illustrative projected effect per real crime category, followed by an LLM-generated report per hero. C# only, .NET 10, EF Core over Postgres, Groq for structured-JSON report generation with a disclaimer injected by code rather than left to the model. The crime pull is bounded to a trailing 90-day window via Socrata's `$where` filter rather than the multi-million-row full table; the hero roster is a build-time snapshot from SuperheroAPI, with the ingestion script checked in and re-runnable rather than treated as a one-off. The UI marks the exact point where real data ends and the fictional scoring model begins with an explicit banner, not just a code comment. See the [project README](./SuperHeroOps/README.md).

### [`/BBS_Website`](./BBS_Website)

An interactive portfolio site presented as a late-1980s bulletin board system. The complete experience — modem handshake, C64-inspired boot sequence, keyboard and mouse navigation, puzzle gate, demoscene-style intro, synthesized audio, Pine-inspired contact screen, and in-page arcade — is built in plain HTML, CSS, and JavaScript without a framework, build step, or runtime dependency. Its deliberately compact architecture demonstrates choosing the smallest viable toolset for the experience rather than defaulting to a framework. See the [project README](./BBS_Website/README.md).

### [`/Chord_Chart_Manager`](./Chord_Chart_Manager)

A local, searchable chord-chart manager for live performance, combining a Python document-import pipeline with a React/Vite progressive web app, Express API, and PostgreSQL. It supports chart editing and transposition, setlists with gig-specific song settings and notes, and IndexedDB-backed offline work that syncs safely when a connection returns. Docker Compose runs the complete local application; cloud and Kubernetes deployment remain documented future work. See the [project README](./Chord_Chart_Manager/README.md).

### [`/Batcave_IDS`](./Batcave_IDS)

A local, streaming intrusion-detection pipeline: a simulated five-stage attack lands telemetry through Kafka (Redpanda) into a partitioned Parquet lakehouse, transformed with dbt on DuckDB and orchestrated by Dagster, with data-quality handling for ten deliberately injected pathologies (duplicates, late/out-of-order events, malformed JSON, schema drift, clock skew). An LLM analyst then reconstructs attacker identity and MITRE ATT&CK techniques from sensor data alone — with a ground-truth-leakage check enforced at the dbt-lineage level — and is scored against a rule-based baseline across attribution and technique-recall metrics, reported by detection-observability tier. Runs entirely locally on Docker; no cloud account required. See the [Batcave_IDS README](./Batcave_IDS/README.md).

### [`/Agentic_AI`](./Agentic_AI)

Five Streamlit applications that each isolate a different agentic pattern while sharing Gemini `gemini-3.1-flash-lite`, local Streamlit Secrets, deliberate user-triggered inference, and session-level demo safeguards. **Agentic Adversarial** is a Bart/Marge/Homer snack negotiation using Chroma-backed nutrition retrieval with grounded and model-knowledge fallbacks. **Agentic Approval** turns a recorded idea into an explicit Bundy-household escalation state machine, with Gemini transcription, Edge TTS, and OpenTelemetry traces. **Agentic Collaborative** uses a TMNT team to transform an image into a verified poem, narration, and mood-matched local music. **South Park Town Hall Debate** coordinates persona prompts, independent judges, call-budget checks, and a live telemetry panel. **No Cap** is a compact structured-output classifier for slang relevance. See the [Agentic_AI README](./Agentic_AI/README.md).

### [`/data_engineering`](./data_engineering)

Databricks portfolio notebooks demonstrating production-relevant data engineering and LLM-orchestration patterns — medallion architecture (Bronze/Silver/Gold), strict deterministic-vs-LLM separation, resilient API design (backoff/jitter, concurrency caps), and retrieval pipelines — each wrapped in a pop-culture theme that's cosmetic to the underlying engineering: a full LLM-as-judge complaint-triage pipeline (Gringotts), a PDF-to-semantic-search RAG pipeline (Spinal Tap), an EAV-to-dimensional urban-triage scoring model with multi-agent synthesis (Wayne-Stark), a deterministic-pre-filter-then-LLM threat analysis with geospatial output (K.A.R.E.N.), and a dependency-free Zork rebuild as a state-machine counterpoint. See the [data_engineering README](./data_engineering/README.md).

### [`/SQL_fun`](./SQL_fun)

ASCII art and small games written entirely in SQL — recursive CTEs rendering a circle, a Mandelbrot set, a sine wave, and fireworks, plus stateful Minesweeper and Battleship played through stored procedures, each implemented in both PostgreSQL and T-SQL. See the [SQL_fun README](./SQL_fun/README.md).

### [`/shell_scripts`](./shell_scripts)

Standalone operations scripts: Bash tooling for database CRUD across three engines, rolling backups, and prompt-injection scanning; PowerShell tooling for Windows duplicate-file/folder cleanup. See the [shell_scripts README](./shell_scripts/README.md).

### [`/Prompts`](./Prompts)

AI system prompts organized around one principle: **AI should expand human thinking, not replace it.** Six families — `healthy-ai/` (guardrails against dependency and drift), `thinking/` (inward, outward, and pedagogy-based perspective tools), `code-dojo/` (programming practice built on Eastern pedagogical traditions), `training/` (a learning pipeline modeled on the ML lifecycle), `writing/` (corrective and collaborative writing partners), and `health/` (six holistic-health epistemologies). Practical, tested, no hype. See the [prompts README](./Prompts/readme.md).

### [`/docs`](./docs)

Long-form writing in three formats: `articles/` (pieces for LinkedIn and other platforms), `Translation_Layer/` (article-form adaptations of the YouTube channel), and `white_papers/` (abstracts and Zenodo links for the academic work). Formal writing meant to last. See the [docs README](./docs/README.md).

### [`/projects`](./projects)

The holding area for applications and code still in progress. Projects move into the repository root once they are ready to stand as complete portfolio work.

---

## File Naming Conventions

- **Java and C#:** `PascalCase`
- **Python:** `snake_case`
- **Prompts:** `kebab-case`

---

## Skills

Skills demonstrated across the applications, pipelines, and supporting documentation in this repository:

**Languages:** Java · C# · Python · JavaScript/JSX · SQL (PostgreSQL, T-SQL, PL/SQL, Spark SQL) · Bash · PowerShell · HTML/CSS

**Application Engineering:** Spring Boot (Web, Data JPA, Statemachine) · .NET 10 · Blazor Server · Entity Framework Core · Node.js/Express · REST APIs · PostgreSQL full-text search · deterministic workflow and state-machine design · offline-first synchronization

**Frontend & Product:** React · Vite · Progressive Web Apps · IndexedDB · responsive HTML/CSS/JavaScript · Web Audio API · keyboard-first interaction design · Playwright-driven browser verification

**AI / LLM Systems:** agentic and multi-agent applications · Groq and Gemini API integration · structured JSON and schema-constrained generation · RAG, embeddings, and semantic retrieval · Chroma and Databricks AI Search · LLM-as-classifier and LLM-as-judge evaluation · cross-model evaluation · Whisper speech-to-text · gTTS and edge-tts · OpenTelemetry with GenAI semantic conventions

**Data, Streaming & Analytics:** PySpark · Spark SQL · Databricks · Unity Catalog · Delta Lake · Bronze/Silver/Gold medallion pipelines · Kafka-compatible Redpanda · Parquet lakehouses · dbt · Dagster · DuckDB · data quality, lineage, and ground-truth-leakage controls · Socrata Open Data API integration

**Delivery & Quality:** Docker and Docker Compose · Gradle · npm · GitHub Actions · JUnit · xUnit · pytest · Vitest · Playwright/Chromium · Ruff · SQLFluff · pre-commit

**Security & Architecture:** MITRE ATT&CK-mapped threat detection · prompt-injection scanning · secret-aware configuration · deterministic/probabilistic system boundaries · observability and evaluation design · Kubernetes and Terraform architecture design · ADRs, handoffs, and technical architecture documentation

---

## Background

30+ years in professional IT, and tamer of computers for even longer. Old enough to remember the sound of a modem's handshake, young enough to still ship fast, and wise enough to avoid breaking things. The technical work spans PC field work to enterprise architecture, systems administration to full-stack web and ETL development, and — most recently — hands-on AI/LLM engineering and AI implementation strategy. Roles and responsibilities across that span include software engineer, solutions architect, systems administrator, and technical lead. I have the coffee and aspirin bills to prove it.

---

## Contact

- **LinkedIn** — [Jong Lee](https://www.linkedin.com/in/jong-lee-874aa49/) (best way to reach me)
- **Location** — Northern Virginia, USA

## Channels

- **Translation Layer** — [YouTube](https://www.youtube.com/@TranslationLayer)
- **Buffer Overflow** — [YouTube](https://www.youtube.com/@BufferOverflow-v2j)

---

## Academic Work

White papers published on [Zenodo](https://zenodo.org/communities/jivejong/) — peer-indexed, DOI-assigned, citable. Current work examines how organizations can turn legacy data estates into durable AI advantages without recreating the debt through undisciplined ingestion; how AI tools can preserve productive human judgment rather than prematurely closing thought; and how frontier models differ in behavior, self-perception, and image-generation strategy under controlled comparisons. It also proposes digital archaeology, digital therapy, and LLM SEO as emerging disciplines for recovering, repairing, and deliberately preserving knowledge in an age of training compression. Links in `/docs`.

---

## A Note on AI Use

Everything here is HITL — human-in-the-loop, applied as a quality gate before anything gets posted, published, or committed. AI accelerates the work. It doesn't replace the judgment.

---

_The handle predates the internet. The work is ongoing._
