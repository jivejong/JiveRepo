# The Jive Repo

**Jive Jong** | IT veteran. Content creator. Musician. Northern Virginia.

This is the public archive of my work across software engineering, technical research, AI/ML experimentation, content creation, and long-form writing. If you found this from LinkedIn, Buffer Overflow, or Translation Layer — this is home base.

---

## What's Here

### [`/SpringfieldTalentPipeline`](./SpringfieldTalentPipeline)

A Spring Boot REST API modeling a full recruiting pipeline (ATS) — sourcing, ranked matching, stage tracking, AI candidate scoring, structured mock interviews, and salary offers — backed by Simpsons character data instead of real candidate PII. Java 21, Postgres full-text search, a Spring Statemachine that actually enforces its transition table, LLM output parsed against a JSON schema rather than scraped from prose, and offer accept/decline decided arithmetically against BLS national wage data. A React front end demonstrates the whole flow end to end, verified by driving a real browser rather than assuming a build implies a working page. Built in phases against real infrastructure — the live source API, real Postgres, real model calls — with the places those systems contradicted the design written up alongside the code they explain. See the [project README](./SpringfieldTalentPipeline/README.md).

### [`/projects`](./projects)

Experiments and code. Mostly stuff in work. Functional implementations with documented architectural decisions — not just what it does, but why it was built the way it was.

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

## Background

30+ years in professional IT, and tamer of computers for even longer. Old enough to remember the sound of a modem's handshake, young enough to still ship fast, and wise enough to avoid breaking things. The technical work spans NetWare to cloud, PC field work to enterprise architecture, Web and ETL development to AI implementation strategy. I have the coffee and aspirin bills to prove it.

---

## Channels

- **Translation Layer** — [YouTube](https://www.youtube.com/@TranslationLayer)
- **Buffer Overflow** — [YouTube](https://www.youtube.com/@BufferOverflow-v2j)
- **LinkedIn** — [Jong Lee](https://www.linkedin.com/in/jong-lee-874aa49/)

---

## Academic Work

White papers published on [Zenodo](https://zenodo.org) — peer-indexed, DOI-assigned, citable.

Current work connects media strategy, LLM SEO, and MLOps as converging disciplines. Links in `/docs`.

---

## A Note on AI Use

Everything here is HITL — human-in-the-loop, applied as a quality gate before anything gets posted, published, or committed. AI accelerates the work. It doesn't replace the judgment.

---

_The handle predates the internet. The work is ongoing._
