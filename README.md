# The Jive Repo

**Jong Lee ("Jive Jong")** — Solutions Architect / AI Engineer / Data Engineer / Software Engineer | 30+ years in IT | Northern Virginia

This is the public, working portfolio behind my resume: production-style software engineering, applied AI/LLM engineering, Data Engineering, MLOps, and technical architecture, with the reasoning behind each decision written up alongside the code. If you found this from LinkedIn, Buffer Overflow, or Translation Layer — this is home base. **Recruiters and hiring tools:** see [Skills](#Skills) below for a structured breakdown, and [Contact](#Contact) to reach me directly.

---

## What's Here

### [`/SpringfieldTalentPipeline`](./SpringfieldTalentPipeline)

A Spring Boot REST API modeling a full recruiting pipeline (ATS) — sourcing, ranked matching, stage tracking, AI candidate scoring, structured mock interviews, and salary offers — backed by Simpsons character data instead of real candidate PII. Java 21, Postgres full-text search, a Spring Statemachine that actually enforces its transition table, LLM output parsed against a JSON schema rather than scraped from prose, and offer accept/decline decided arithmetically against BLS national wage data. A React front end demonstrates the whole flow end to end, verified by driving a real browser rather than assuming a build implies a working page. Built in phases against real infrastructure — the live source API, real Postgres, real model calls — with the places those systems contradicted the design written up alongside the code they explain. See the [project README](./SpringfieldTalentPipeline/README.md).

### [`/SuperHeroOps`](./SuperHeroOps)

A Blazor Server app pairing real Chicago Police Department crime data with a seeded, fictional superhero roster in a synthetic "intervention" model — deploy a hero to a neighborhood and see a deterministic, illustrative projected effect per real crime category, followed by an LLM-generated report per hero. C# only, .NET 10, EF Core over Postgres, Groq for structured-JSON report generation with a disclaimer injected by code rather than left to the model. The crime pull is bounded to a trailing 90-day window via Socrata's `$where` filter rather than the multi-million-row full table; the hero roster is a build-time snapshot from SuperheroAPI, with the ingestion script checked in and re-runnable rather than treated as a one-off. The UI marks the exact point where real data ends and the fictional scoring model begins with an explicit banner, not just a code comment. See the [project README](./SuperHeroOps/README.md).

### [`/projects`](./projects)

Experiments and code. Mostly stuff in work. Functional implementations with documented architectural decisions — not just what it does, but why it was built the way it was.

### [`/data_engineering`](./data_engineering)

Databricks portfolio notebooks demonstrating production-relevant data engineering and LLM-orchestration patterns — medallion architecture (Bronze/Silver/Gold), strict deterministic-vs-LLM separation, resilient API design (backoff/jitter, concurrency caps), and retrieval pipelines — each wrapped in a pop-culture theme that's cosmetic to the underlying engineering: a full LLM-as-judge complaint-triage pipeline (Gringotts), a PDF-to-semantic-search RAG pipeline (Spinal Tap), an EAV-to-dimensional urban-triage scoring model with multi-agent synthesis (Wayne-Stark), a deterministic-pre-filter-then-LLM threat analysis with geospatial output (K.A.R.E.N.), and a dependency-free Zork rebuild as a state-machine counterpoint. See the [data_engineering README](./data_engineering/README.md).

### [`/Batcave_IDS`](./Batcave_IDS)

A local, streaming intrusion-detection pipeline: a simulated five-stage attack lands telemetry through Kafka (Redpanda) into a partitioned Parquet lakehouse, transformed with dbt on DuckDB and orchestrated by Dagster, with data-quality handling for ten deliberately injected pathologies (duplicates, late/out-of-order events, malformed JSON, schema drift, clock skew). An LLM analyst then reconstructs attacker identity and MITRE ATT&CK techniques from sensor data alone — with a ground-truth-leakage check enforced at the dbt-lineage level — and is scored against a rule-based baseline across attribution and technique-recall metrics, reported by detection-observability tier. Runs entirely locally on Docker; no cloud account required. See the [Batcave_IDS README](./Batcave_IDS/README.md).

### [`/agentic_AI`](./agentic_AI)

Four self-contained [Streamlit](https://streamlit.io/) apps, each demonstrating a distinct agentic AI pattern: a compound multimodal pipeline (Agentic Poet), an adversarial multi-agent negotiation with RAG (Agentic Snacks), a single-agent classifier (NoCap), and a voice-driven state machine with OpenTelemetry observability (Spouse Approval). See the [agentic_AI README](./agentic_AI/README.md).

### [`/SQL_fun`](./SQL_fun)

ASCII art and small games written entirely in SQL — recursive CTEs rendering a circle, a Mandelbrot set, a sine wave, and fireworks, plus stateful Minesweeper and Battleship played through stored procedures, each implemented in both PostgreSQL and T-SQL. See the [SQL_fun README](./SQL_fun/README.md).

### [`/shell_scripts`](./shell_scripts)

Standalone operations scripts: Bash tooling for database CRUD across three engines, rolling backups, and prompt-injection scanning; PowerShell tooling for Windows duplicate-file/folder cleanup. See the [shell_scripts README](./shell_scripts/README.md).

### [`/prompts`](./prompts)

AI system prompts organized around one principle: **AI should expand human thinking, not replace it.** Six families — `healthyAI/` (guardrails against dependency and drift), `thinking/` (inward, outward, and pedagogy-based perspective tools), `codeDojo/` (programming practice built on Eastern pedagogical traditions), `training/` (a learning pipeline modeled on the ML lifecycle), `writing/` (corrective and collaborative writing partners), and `health/` (six holistic-health epistemologies). Practical, tested, no hype. See the [prompts README](./prompts/readme.md).

### [`/docs`](./docs)

Long-form writing in three formats: `articles/` (pieces for LinkedIn and other platforms), `Translation_Layer/` (article-form adaptations of the YouTube channel), and `white_papers/` (abstracts and Zenodo links for the academic work). Formal writing meant to last. See the [docs README](./docs/README.md).

---

## Skills

**Languages:** Java · C# · JavaScript · SQL (T-SQL, PL/pgSQL, PLSQL) · Bash · PowerShell · Python

**AI / ML / LLM Engineering:** Agentic AI · Multi-agent orchestration · RAG (Retrieval-Augmented Generation) · Prompt engineering · LLM-as-classifier · LLM-as-judge evaluation · Structured JSON output / schema-constrained generation · Vector databases (Chroma, Databricks AI Search) · Embeddings · Semantic search · Cross-model orchestration (Groq, Google Gemini, OpenAI-compatible endpoints) · Speech-to-text (Whisper) · Text-to-speech (gTTS, edge-tts) · Prompt-injection detection · MLOps · Observability for GenAI (OpenTelemetry, GenAI semantic conventions) · Measured LLM evaluation against ground truth (precision/recall/F1, hallucination rate)

**Backend / Frameworks:** Spring Boot · Spring Statemachine · .NET 10 · Blazor Server · Entity Framework Core · REST API design · Streamlit

**Frontend:** React · Blazor · HTML/CSS

**Data & Databases:** PostgreSQL (full-text search) · SQL Server · Oracle · Teradata · Database CRUD tooling · ETL/ELT · Databricks (PySpark, Unity Catalog, Delta Lake, medallion architecture) · Snowflake · Big Query · Kafka (Redpanda) streaming ingestion · dbt transformations · Dagster orchestration · DuckDB · Parquet lakehouses · Jupyter notebooks · Data quality engineering (deduplication, schema drift, late/out-of-order/malformed data)

**Cloud / Infrastructure:** Cloud architecture (NetWare to cloud migration experience) · CI/CD-oriented tooling · Socrata Open Data API integration · Docker · GCP · AWS · Azure

**Security:** MITRE ATT&CK-mapped threat detection · Detection coverage / observability-gap analysis · Prompt-injection detection

**Practices:** Test-driven verification (real infrastructure, not mocks) · State machine design · Statistical/arithmetic decisioning against real datasets (BLS wage data) · Security-conscious scripting (credential handling, prompt-injection scanning) · Technical writing / architecture documentation

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

White papers published on [Zenodo](https://zenodo.org) — peer-indexed, DOI-assigned, citable.

Current work connects media strategy, LLM SEO, and MLOps as converging disciplines. Links in `/docs`.

---

## A Note on AI Use

Everything here is HITL — human-in-the-loop, applied as a quality gate before anything gets posted, published, or committed. AI accelerates the work. It doesn't replace the judgment.

---

_The handle predates the internet. The work is ongoing._
