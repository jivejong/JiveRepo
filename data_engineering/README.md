# Databricks Portfolio Notebooks

A set of Databricks notebooks built to demonstrate production-relevant data engineering and LLM-orchestration patterns. Each notebook wraps its technical content in a pop-culture theme — the theming is cosmetic; the engineering underneath (medallion architecture, deterministic-vs-LLM separation, resilient API design, retrieval pipelines) is the actual point.

| Notebook                                                                     | What it demonstrates                                                                                                |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| [`Gringotts_Financial.ipynb`](#gringotts-complaint-triage)                   | Full Bronze→Silver→Gold pipeline with LLM-as-judge evaluation, separated generation/scoring, and full observability |
| [`Spinal_Tap.ipynb`](#spinal-tap--unstructured-documents-to-semantic-search) | Unstructured PDF library → chunked, metadata-tagged knowledge base → vector search / RAG retrieval                  |
| [`wayne_stark.ipynb`](#project-wayne-stark)                                  | EAV-to-dimensional data modeling, composite scoring, multi-agent LLM synthesis                                      |
| [`Spiderman_Karen.ipynb`](#project-karen)                                    | Deterministic pre-filtering to cut LLM token cost, concurrent API calls with backoff, geospatial visualization      |
| [`zork.ipynb`](#zork--the-unofficial-python-edition)                         | Data-driven state machine design in pure Python (no external dependencies)                                          |

## Each notebook is available with or without ouput (\_output)

## Common patterns across the pipeline notebooks

The four data pipeline notebooks (Gringotts, Spinal Tap, Wayne-Stark, K.A.R.E.N.) share a consistent set of engineering decisions, independent of their subject matter:

- **Deterministic logic and LLM inference are kept strictly separate.** Anything that needs to be reproducible and auditable — routing, scoring weights, composite indices — is computed in Spark/SQL. LLM calls are reserved for tasks that genuinely require language understanding: sentiment, summarization, persona-driven writing, qualitative synthesis.
- **Generation and evaluation are separate calls.** Where a notebook has an LLM write something and then grade it (Gringotts), those are two independent API calls in separate contexts, avoiding the self-grading bias that inflates LLM-judge scores.
- **Resilient API design.** Every Gemini call is wrapped with exponential backoff + jitter on retryable failures (429/500/502/503/504), and fails fast on non-retryable ones (malformed schema, bad JSON, token limits) instead of burning retries on errors that won't resolve.
- **Concurrency with hard caps.** API calls run through `concurrent.futures.ThreadPoolExecutor` rather than a serial loop, with total call counts explicitly capped (e.g. 4 calls per pipeline run) to keep cost and runtime predictable for a portfolio demo.
- **Unity Catalog schema isolation.** Every notebook creates its own dedicated schema (`workspace.gringotts`, `workspace.spinal_tap`, etc.) so it can be dropped cleanly without touching other workspace assets.
- **Structured output contracts.** LLM responses are locked to JSON (`responseMimeType: application/json`) so downstream parsing is a contract, not a guess.

---

## Gringotts Complaint Triage

**`Gringotts_Financial.ipynb`**

An LLM-orchestrated medallion pipeline built on the CFPB Consumer Complaint Database, using an LLM-as-judge pattern to generate and evaluate simulated complaint responses at scale. The Hogwarts/Gringotts framing maps the four "Houses" to four response personas (advocacy, analysis, practical resolution, strategy); "Dumbledore" is the LLM-as-judge evaluator.

**Pipeline:**

| Layer           | What happens                                                                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Bronze          | Pull a sample of complaints with narrative text from a Databricks Marketplace zero-ETL share                                                     |
| Silver          | Deterministic rules-based routing (CFPB `Issue` → House, with load-balancing fallback for unmapped issues) + Gemini sentiment/summary enrichment |
| Stage 2         | Per-House persona response generation → independent evaluator pass scoring 7 quality dimensions → Spark-computed weighted composite score        |
| Gold            | Business-friendly renamed views that retain full operational metadata (status, retry count, latency, model version)                              |
| Dashboard views | 9 presentation-only views for a BI dashboard — no inference happens at this layer                                                                |

**Notable engineering details:**

- Routing is a fixed lookup table, not an LLM decision — the prompt explicitly tells the model routing is final and not to reinterpret it.
- The evaluator scores 7 dimensions (relevance, grounding, complaint-addressed, actionability, professionalism, persona fidelity, completeness) with explicit anchors telling the model to reserve 9s/10s for genuinely exceptional cases, since unconstrained LLM judges cluster scores near the top of the range.
- Every enrichment step returns a typed error code, HTTP status, retry count, model version, and latency alongside the output — a batch run is fully diagnosable from the resulting table without re-hitting the API.
- Row limits throughout keep the demo cheap to re-run; the notebook is explicit that these sizing choices aren't production guidance.

**Stack:** PySpark, Spark SQL, Delta Lake (Bronze/Silver/Gold), Gemini 3.8 Flash via REST, Databricks Unity Catalog.

---

## Spinal Tap — Unstructured Documents to Semantic Search

**`Spinal_Tap.ipynb`**

An end-to-end unstructured-data-to-semantic-retrieval pipeline, built on a library of 14 PDF books (music theory, music business, copyright, marketing, business law, entrepreneurship, filmmaking). The goal is turning documents designed for human reading into a knowledge layer queryable by natural language and semantic similarity.

**Pipeline:**

- **Bronze** — PyMuPDF extracts text page-by-page; header/chapter-number noise is stripped, whitespace is normalized, and pages under 150 characters are skipped.
- **Silver** — Fixed-size (900 char), overlapping (150 char) chunks with punctuation-aware boundaries; a termination guard prevents infinite loops when remaining text is shorter than the overlap window; each chunk retains its source book, page number, and sequence.
- **Gold** — Deterministic domain tagging (`music_business`, `performance_theory`, `copyright_ip`, `filmmaking`, etc.) assigned by source book rather than inferred by an LLM, keeping the metadata reproducible and independently testable.
- **Retrieval** — The Gold Delta table is indexed by Databricks AI Search (managed vector search + embeddings). The final cell is an interactive retrieval loop: a natural-language question returns the most relevant source chunks with book, page, domain, and text.

**Notable engineering detail:** the notebook explicitly separates _knowledge_, _retrieval_, _generation_, and _persona_ as independent layers. The retrieval layer here is deliberately generation-free — it surfaces evidence rather than synthesizing an answer — so a generation/persona layer (e.g., an LLM answering in the voice of Spinal Tap's manager, Ian Faith) could be attached later without touching the underlying knowledge or search layers.

**Stack:** PyMuPDF, PySpark, Delta Lake, Databricks AI Search (vector search), sentence-transformers.

---

## Project Wayne-Stark

**`wayne_stark.ipynb`**

_Multi-Dimensional Urban Triage._ Takes a long-format (EAV) city demographics dataset and turns it into a ranked, AI-narrated intervention plan across two "philanthropic" domains (Wayne Foundation: economic/education; Stark Industries: health/infrastructure).

**Pipeline:**

1. **Dimensional modeling** — Pivots the raw Entity-Attribute-Value rows into three domain-isolated wide Delta tables (economic distress, systemic health deficits, vulnerable demographics), filtering to `Gender = 'Both'` / `Race_Ethnicity = 'All'` to avoid double-counting segmented groups.
2. **Composite scoring** — Joins the three tables in PySpark and computes a weighted `Intervention_Priority_Score`. Positive metrics (HS graduation rate, preschool enrollment) are inverted into deficits (`100 - rate`) so every input metric points the same direction, and every term is wrapped in `coalesce()` so missing data can't artificially inflate a city's score.
3. **Multi-agent synthesis** — The top 3 cities are sent to Gemini (simulating J.A.R.V.I.S./Batcomputer co-analysis) to produce structured per-city intervention briefs, then a second LLM pass synthesizes those briefs into a 3-paragraph executive memo.

**Notable engineering detail:** the scoring model is a deliberate case study in EAV-to-dimensional pivoting under real constraints — reconciling metrics that are directionally inconsistent (some "higher is worse," some "lower is worse") into one composite index without silently distorting the ranking.

**Stack:** Spark SQL, PySpark, Delta Lake, Gemini 3.8 Flash via REST, `concurrent.futures`.

---

## Project K.A.R.E.N.

**`Spiderman_Karen.ipynb`**

_AI-Enriched Threat Analysis._ A hybrid pipeline built to make the point that raw, unstructured datasets shouldn't go straight to an LLM — deterministic data engineering does the heavy lifting, and AI is reserved purely for synthesis, keeping token cost and hallucination risk down.

**Pipeline:**

1. **Deterministic filtering (PySpark)** — Ingests NYC public school crime data, null-safes the numeric columns with `coalesce()`, and engineers a weighted `Spidey_Threat_Score` to isolate the top 3 high-risk zones out of the full dataset before any LLM is involved.
2. **Concurrent AI extraction** — 3 concurrent Gemini calls (via `ThreadPoolExecutor`, with exponential backoff + jitter on 429/503) turn the numeric rows into structured tactical JSON briefs.
3. **Geospatial UI** — Folium renders the 3 hotspots on an OpenStreetMap tileset (explicit ODbL attribution, no CartoDB key required), with custom HTML popups and an auto-fit bounding box.
4. **Strategic synthesis (4th LLM call)** — A final pass weighs civilian density against threat level to produce a plain-language deployment recommendation.

**Notable engineering detail:** total LLM calls are strictly capped at 4 for the entire pipeline run — a deliberate cost/latency constraint that forces the deterministic layer to do as much of the work as possible before any model is invoked.

**Stack:** PySpark, Databricks Unity Catalog, Gemini 3.8 Flash via REST, `concurrent.futures`, Folium.

---

## Zork: The Unofficial Python Edition

**`zork.ipynb`**

A notebook-safe rebuild of the classic text adventure — no Spark, no external APIs, just a data-driven state machine in plain Python. Included as a counterpoint to the AI-pipeline notebooks above: same emphasis on clean state management and separation of data from logic, applied to a game engine instead of a data pipeline.

**Design:**

- Rooms, exits, and actions are defined entirely as data (a `rooms` dict), not as a chain of `if/elif` statements — adding a room or action means adding a data entry, not new control flow.
- A `game_state` dict tracks inventory, container state (mailbox opened), and NPC state (ogre alive/dead) across the whole session.
- Exits support both unconditional string targets and conditional dict targets (`required_item`, `target_room`, `fail_message`) for item-gated paths, e.g. needing a machete to clear vines.
- Custom action sentinels (`__ATTACK_OGRE__`, `__WIN__`, etc.) route special-case logic without polluting the plain data structure with executable code.
- Death and win states offer a clean respawn/replay loop rather than crashing the notebook kernel.

**Stack:** Pure Python (`input()`-driven REPL loop), no dependencies.

---

## Running these notebooks

All five were built and run in Databricks. To reproduce:

- A Databricks workspace with **Unity Catalog** enabled (each notebook creates and uses its own schema).
- Access to the relevant **Databricks Marketplace** shares: `us_cities_demographics`, `us_crime_data`, `consumer_complaints` (Wayne-Stark, K.A.R.E.N., and Gringotts respectively pull from these zero-ETL shares).
- A Gemini API key stored via `dbutils.secrets.get(catalog=..., schema=..., key="gemini_api_key")`, scoped per-notebook schema (Gringotts, Wayne-Stark, K.A.R.E.N.).
- Notebook-specific `%pip install` cells: `folium` (K.A.R.E.N.), `pymupdf sentence-transformers pandas numpy` (Spinal Tap).
- Spinal Tap additionally needs a Unity Catalog **Volume** populated with the source PDFs, and a **Databricks AI Search** vector index built on `workspace.spinal_tap.gold_chunks`.
- `zork.ipynb` has no dependencies beyond a Python kernel that supports `input()`.

---

## Implementation notes: paid Databricks and other platforms

These notebooks were built against the constraints of a free/entry-level Databricks environment — small `LIMIT`-bounded samples, manual notebook-cell orchestration, and hand-rolled retry logic around an external LLM API. The architecture (medallion layers, deterministic/LLM separation, resilient API calls, vector-indexed retrieval) is intentionally platform-agnostic; what follows is how each piece would actually be built with more platform access.

### With paid Databricks (Premium/Enterprise)

- **Lakeflow Declarative Pipelines (formerly Delta Live Tables)** would replace the manual cell-by-cell Bronze→Silver→Gold execution in Gringotts and Spinal Tap with declarative pipeline definitions — automatic dependency resolution, built-in data-quality expectations, and pipeline-level lineage instead of hand-rolled status/error columns per row.
- **Databricks Workflows** would replace "run the notebook top to bottom" with proper multi-task job orchestration: task-level retries, alerting on failure, and scheduled runs instead of one-shot manual execution.
- **Databricks Foundation Model APIs / Model Serving** would replace the raw `requests.post()` calls to the external Gemini endpoint in all four AI notebooks. Inference would happen through a governed, rate-limited serving endpoint inside the Unity Catalog boundary — most of the custom exponential-backoff/retry code becomes unnecessary because the platform handles it.
- **Production-tier Vector Search** would give Spinal Tap real-time index sync (as new PDFs land) instead of a manually triggered batch sync, plus higher-throughput query endpoints.
- **Unity Catalog at full scale** — row/column-level security, attribute-based access control, and full audit logging/lineage graphs across all five notebooks' tables, not just schema isolation.
- **MLflow tracking** would version the LLM prompts and capture Dumbledore's evaluation scores as tracked experiments, so response-quality drift over time (across prompt or model changes) is measurable rather than anecdotal.
- **Databricks Asset Bundles** would let the whole portfolio deploy as versioned, CI/CD-managed bundles instead of manually copied notebooks, and autoscaling job clusters would replace the small `LIMIT 20`-style demo sampling with full production volume.

### Snowflake (Cortex)

- `SNOWFLAKE.CORTEX.COMPLETE()` would collapse the custom REST-call-plus-retry Python pattern (used in all four AI notebooks) into a single SQL function call — Snowflake manages retries and rate limits internally, so the enrichment, scoring, and synthesis steps become SQL statements rather than Python UDFs hitting an external endpoint.
- **Cortex Search** is the direct analog to Databricks AI Search for Spinal Tap — a managed hybrid (vector + keyword) search service built directly on Snowflake tables, with no separate embedding pipeline to stand up.
- Built-in Cortex functions (`SENTIMENT`, `SUMMARIZE`) could replace some of Gringotts' Silver-layer enrichment prompts outright.
- **Snowpark Python** replaces PySpark for the dataframe transformations (dimensional pivoting in Wayne-Stark, scoring logic in K.A.R.E.N.).
- **Streams + Tasks** replace Databricks Workflows for medallion pipeline orchestration; native RBAC + object tagging replace Unity Catalog for governance; the Snowflake Marketplace is the equivalent zero-ETL share source.

### BigQuery

- `ML.GENERATE_TEXT()` remote model functions (calling Vertex AI / Gemini directly) would replace the custom Python REST calls with native SQL, the same collapsing effect as Snowflake Cortex.
- BigQuery's `VECTOR_SEARCH()` function or Vertex AI Vector Search would serve as the RAG index for Spinal Tap.
- **Dataform** (BigQuery's native SQL transformation tool, dbt-like) is the closer analog to the layered Bronze/Silver/Gold SQL used in Gringotts and Wayne-Stark, versus writing everything as PySpark.
- **Document AI** would replace PyMuPDF for the PDF extraction step in Spinal Tap, particularly useful if the source library included scanned or non-text-layer PDFs.
- **Cloud Composer** (managed Airflow) handles orchestration; **Dataplex** provides the catalog/governance layer in place of Unity Catalog.

### Microsoft Fabric

- Fabric's **Lakehouse (OneLake)** stores everything as Delta tables, so the medallion architecture in Gringotts and Spinal Tap is directly portable in format, if not in tooling.
- **Fabric notebooks** provide the same Spark runtime as Databricks for the PySpark transformation logic.
- **Dataflow Gen2 / Data Factory pipelines** replace manual notebook execution for orchestration.
- LLM calls would go through **Azure OpenAI Service** — Fabric doesn't have a single built-in SQL-callable completion function the way Cortex/BigQuery ML do, so the retry/backoff pattern from the original notebooks would largely carry over.
- **Azure AI Search** is the vector search layer for Spinal Tap; **Microsoft Purview**, which Fabric integrates with natively, provides governance and lineage.

### AWS

- **S3** is the Bronze landing zone; **Glue** (managed Spark) or **EMR** handles the Silver/Gold transformations in place of Databricks' managed Spark runtime.
- **Amazon Bedrock** replaces the direct Gemini REST calls — Claude, Llama, or Titan models hosted on Bedrock, called through the boto3 SDK, which has retry logic built in, reducing the amount of hand-rolled backoff code needed.
- **Bedrock Knowledge Bases** (backed by OpenSearch Service with its vector engine) is the managed RAG layer for Spinal Tap, replacing the separate chunking-then-indexing steps with a more managed ingestion flow.
- **Textract** would replace PyMuPDF for PDF extraction, especially useful for any scanned pages in the source library.
- **Glue Data Catalog + Lake Formation** provide the governance/fine-grained-access equivalent to Unity Catalog; **Step Functions** or **Managed Workflows for Apache Airflow (MWAA)** handle orchestration; **QuickSight** would sit on top of Gringotts' Gold views for the dashboard layer.
