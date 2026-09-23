# The Jive Repo

**Jong Lee ("Jive Jong")**: Solutions Architect / AI Engineer / Data Engineer / Software Engineer | 30+ years in IT | Northern Virginia

This is the public, working portfolio behind my resume: production-style software engineering, applied AI/LLM engineering, data engineering, MLOps, and technical architecture, with the reasoning behind each decision written up alongside the code. If you found this from LinkedIn, Buffer Overflow, or Translation Layer, this is home base. **Recruiters and hiring tools:** see [Skills](#skills) below for a structured breakdown, and [Contact](#contact) to reach me directly.

---

## Architectural Through-Line

This portfolio is organized around architectural judgment, not a technology inventory. The projects show how I create new solutions, modernize existing technology estates without replacing what already works, make cost and other nonfunctional requirements (NFRs) design inputs, and put explicit boundaries around probabilistic systems. The recurring choices are concrete:

- Modernize before replacing - from Java and .NET systems to database scripts and browser-native UI.
- Keep consequential decisions behind deterministic, auditable rules while AI interprets, summarizes, or proposes.
- Treat reliability, security, cost, latency, portability, and operational complexity as first-class constraints.
- Enforce governance structurally through lineage tests, schemas, state machines, and code-owned guardrails.
- Design streaming and distributed systems for duplication, delay, reordering, malformed input, and partial failure.
- Use the smallest technology surface that meets the requirements, and require metered or probabilistic components to justify their value.

For a cross-portfolio view of those decisions, see the [Portfolio Architecture](./ARCHITECTURE.md).

## What's Here

### [`/SpringfieldTalentPipeline`](./SpringfieldTalentPipeline)

A Spring Boot REST API modeling a full recruiting pipeline (ATS): sourcing, ranked matching, stage tracking, AI candidate scoring, structured mock interviews, and salary offers, backed by Simpsons character data instead of real candidate PII. Java 21, Postgres full-text search, a Spring Statemachine that actually enforces its transition table, LLM output parsed against a JSON schema rather than scraped from prose, and offer accept/decline decided arithmetically against BLS national wage data. A React front end demonstrates the whole flow end to end, verified by driving a real browser rather than assuming a build implies a working page. Built in phases against real infrastructure, including the live source API, real Postgres, and real model calls, with the places those systems contradicted the design written up alongside the code they explain. See the [project README](./SpringfieldTalentPipeline/README.md).

### [`/SuperHeroOps`](./SuperHeroOps)

A Blazor Server app pairing real Chicago Police Department crime data with a seeded, fictional superhero roster in a synthetic "intervention" model. Deploy a hero to a neighborhood to see a deterministic, illustrative projected effect per real crime category, followed by an LLM-generated report per hero. C# only, .NET 10, EF Core over Postgres, Groq for structured-JSON report generation with a disclaimer injected by code rather than left to the model. The crime pull is bounded to a trailing 90-day window via Socrata's `$where` filter rather than the multi-million-row full table; the hero roster is a build-time snapshot from SuperheroAPI, with the ingestion script checked in and re-runnable rather than treated as a one-off. The UI marks the exact point where real data ends and the fictional scoring model begins with an explicit banner, not just a code comment. See the [project README](./SuperHeroOps/README.md).

### [`/BBS_Website`](./BBS_Website)

An interactive portfolio site presented as a late-1980s bulletin board system. The complete experience, including a modem handshake, C64-inspired boot sequence, keyboard and mouse navigation, puzzle gate, demoscene-style intro, synthesized audio, Pine-inspired contact screen, and in-page arcade, is built in plain HTML, CSS, and JavaScript without a framework, build step, or runtime dependency. Its deliberately compact architecture demonstrates choosing the smallest viable toolset for the experience rather than defaulting to a framework. See the [project README](./BBS_Website/README.md).

### [`/Chord_Chart_Manager`](./Chord_Chart_Manager)

A local, searchable chord-chart manager for live performance, combining a Python document-import pipeline with a React/Vite progressive web app, Express API, and PostgreSQL. It supports chart editing and transposition, setlists with gig-specific song settings and notes, and IndexedDB-backed offline work that syncs safely when a connection returns. Docker Compose runs the complete local application; cloud and Kubernetes deployment remain documented future work. See the [project README](./Chord_Chart_Manager/README.md).

### [`/Batcave_IDS`](./Batcave_IDS)

A local, streaming intrusion-detection pipeline: a simulated five-stage attack lands telemetry through Kafka (Redpanda) into a partitioned Parquet lakehouse, transformed with dbt on DuckDB and orchestrated by Dagster, with data-quality handling for ten deliberately injected pathologies (duplicates, late/out-of-order events, malformed JSON, schema drift, and clock skew). An LLM analyst then reconstructs attacker identity and MITRE ATT&CK techniques from sensor data alone. A dbt-lineage-level test prevents ground-truth leakage, and evaluation against a rule-based baseline reports attribution and technique-recall metrics by detection-observability tier. Runs entirely locally on Docker; no cloud account required. See the [Batcave_IDS README](./Batcave_IDS/README.md).

### [`/Agentic_AI`](./Agentic_AI)

Five Streamlit applications that each isolate a different agentic pattern while sharing Gemini `gemini-3.1-flash-lite`, local Streamlit Secrets, deliberate user-triggered inference, and session-level demo safeguards. **Agentic Adversarial** is a Bart/Marge/Homer snack negotiation using Chroma-backed nutrition retrieval with grounded and model-knowledge fallbacks. **Agentic Approval** turns a recorded idea into an explicit Bundy-household escalation state machine, with Gemini transcription, Edge TTS, and OpenTelemetry traces. **Agentic Collaborative** uses a TMNT team to transform an image into a verified poem, narration, and mood-matched local music. **South Park Town Hall Debate** coordinates persona prompts, independent judges, call-budget checks, and a live telemetry panel. **No Cap** is a compact structured-output classifier for slang relevance. See the [Agentic_AI README](./Agentic_AI/README.md).

### [`/Data_Engineering`](./Data_Engineering)

Databricks portfolio notebooks demonstrating production-relevant data engineering and LLM-orchestration patterns: medallion architecture (Bronze/Silver/Gold), strict deterministic-versus-LLM separation, resilient API design (backoff/jitter and concurrency caps), and retrieval pipelines. Each is wrapped in a pop-culture theme that is cosmetic to the underlying engineering: a full LLM-as-judge complaint-triage pipeline (Gringotts), a PDF-to-semantic-search RAG pipeline (Spinal Tap), an EAV-to-dimensional urban-triage scoring model with multi-agent synthesis (Wayne-Stark), a deterministic-pre-filter-then-LLM threat analysis with geospatial output (K.A.R.E.N.), and a dependency-free Zork rebuild as a state-machine counterpoint. See the [Data_Engineering README](./Data_Engineering/README.md).

### [`/SQL_Fun`](./SQL_Fun)

ASCII art and small games written entirely in SQL: recursive CTEs rendering a circle, a Mandelbrot set, a sine wave, and fireworks, plus stateful Minesweeper and Battleship played through stored procedures, each implemented in both PostgreSQL and T-SQL. See the [SQL_Fun README](./SQL_Fun/README.md).

### [`/Shell_Scripts`](./Shell_Scripts)

Standalone operations scripts: Bash tooling for database CRUD across three engines, rolling backups, and prompt-injection scanning; PowerShell tooling for Windows duplicate-file/folder cleanup. See the [Shell_Scripts README](./Shell_Scripts/README.md).

### [`/Prompts`](./Prompts)

AI system prompts organized around one principle: **AI should expand human thinking, not replace it.** Six families: `healthy-ai/` (guardrails against dependency and drift), `thinking/` (inward, outward, and pedagogy-based perspective tools), `code-dojo/` (programming practice built on Eastern pedagogical traditions), `training/` (a learning pipeline modeled on the ML lifecycle), `writing/` (corrective and collaborative writing partners), and `health/` (six holistic-health epistemologies). Practical, tested, no hype. See the [prompts README](./Prompts/readme.md).

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

Architectural capabilities are demonstrated in code, tests, ADRs, and project documentation throughout the repository. The supporting technologies remain listed because they are evidence of those capabilities.

**Solutions & Systems Architecture:** solution architecture · modernization of existing technology estates · NFR-driven design · distributed-systems reliability · workflow and state-machine design · offline-first synchronization · platform portability · trade-off analysis · ADRs and technical architecture documentation

**AI Engineering & Governance:** bounded and agentic AI systems · deterministic/probabilistic separation · structured JSON and schema-constrained generation · RAG, embeddings, and semantic retrieval · evaluation against deterministic baselines · human-in-the-loop decision seams · governance-as-code · lineage controls · model-call budgets and access controls

**Data Architecture & Engineering:** streaming · Bronze/Silver/Gold medallion pipelines · dimensional modeling · Kafka-compatible Redpanda · Parquet lakehouses · dbt · Dagster · DuckDB · data quality · observability · Unity Catalog · Delta Lake · PySpark · Spark SQL · Databricks · Socrata Open Data API integration

**Security & Risk:** Zero-Trust boundaries · MITRE ATT&CK-mapped threat detection · prompt-injection scanning · secret-aware configuration · auditability · trust boundaries · ground-truth-leakage prevention · structured-output reliability

**FinOps & Operations:** cost-aware architecture · deterministic pre-filtering before metered inference · bounded model calls · local-versus-managed service trade-offs · Docker and Docker Compose · OpenTelemetry with GenAI semantic conventions · GitHub Actions · deployment-complexity restraint

**Application & Platform Engineering:** Java · C# · Python · JavaScript/JSX · SQL (PostgreSQL, T-SQL, PL/SQL, Spark SQL) · Bash · PowerShell · HTML/CSS · Spring Boot (Web, Data JPA, Statemachine) · .NET 10 · Blazor Server · Entity Framework Core · Node.js/Express · React · Vite · Progressive Web Apps · IndexedDB · REST APIs

**Delivery & Quality:** Gradle · npm · JUnit · xUnit · pytest · Vitest · Playwright/Chromium · Ruff · SQLFluff · pre-commit · browser-driven verification

---

## Background

30+ years in professional IT, and tamer of computers for even longer. Old enough to remember the sound of a modem's handshake, young enough to still ship fast, and wise enough to avoid breaking things. The technical work spans PC field work to enterprise architecture, systems administration to full-stack web and ETL development, and, most recently, hands-on AI/LLM engineering and AI implementation strategy. Roles and responsibilities across that span include software engineer, solutions architect, systems administrator, and technical lead. I have the coffee and aspirin bills to prove it.

---

## Contact

- **LinkedIn**: [Jong Lee](https://www.linkedin.com/in/jong-lee-874aa49/) (best way to reach me)
- **Location**: Northern Virginia, USA

## Channels

- **Translation Layer**: [YouTube](https://www.youtube.com/@TranslationLayer)
- **Buffer Overflow**: [YouTube](https://www.youtube.com/@BufferOverflow-v2j)

---

## Academic Work

White papers published on [Zenodo](https://zenodo.org/communities/jivejong/): peer-indexed, DOI-assigned, citable. Current work examines how organizations can turn legacy data estates into durable AI advantages without recreating the debt through undisciplined ingestion; how AI tools can preserve productive human judgment rather than prematurely closing thought; and how frontier models differ in behavior, self-perception, and image-generation strategy under controlled comparisons. It also proposes digital archaeology, digital therapy, and LLM SEO as emerging disciplines for recovering, repairing, and deliberately preserving knowledge in an age of training compression. Links in `/docs`.

---

## A Note on AI Use

Everything here is HITL: human-in-the-loop, applied as a quality gate before anything gets posted, published, or committed. AI accelerates the work. It doesn't replace the judgment.

---

_The handle predates the internet. The work is ongoing._
