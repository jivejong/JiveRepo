# batcave-ids

Streaming data pipeline that simulates a five-stage intrusion, lands the telemetry in a Parquet
lakehouse, transforms it with dbt, and asks an LLM to reconstruct both the attacker's identity and
the MITRE ATT&CK techniques they used — scored against ground truth the model never sees.

Runs entirely locally. Kafka, DuckDB, Docker. No cloud account, no trial period, no credentials.

---

## Repository metadata

This project lives inside `JiveRepo`, a portfolio monorepo with several unrelated projects — not a
standalone repository. The description and topics below describe *this project*, not the whole
repo, so they are not set as JiveRepo's GitHub-level description/topics (that would misrepresent
the other projects sharing the repo). They're recorded here for reference, and would apply directly
if this project is ever split into its own repository.

**Description:**
> Streaming data pipeline: Kafka ingestion, Parquet lakehouse, dbt transformations on DuckDB,
> Dagster orchestration, and evaluated LLM threat triage against MITRE ATT&CK ground truth.

**Topics:**
`data-engineering` `kafka` `streaming-data` `dbt` `duckdb` `dagster` `etl` `elt`
`data-quality` `data-pipeline` `parquet` `python` `redpanda` `mitre-attack` `llm-evaluation`

Reasoning: GitHub Linguist reports this repository as Python and SQL. Tool names like dbt, Kafka,
and Dagster never appear in the language bar. Topics and the description field are the only
structured places they surface — moot while nested in a monorepo, relevant again if split out.

---

## Prerequisites

Docker, Python 3.11+, and **`make`**. The Makefile is the entire documented interface (`make
dev-up`, `make attack`, ...) — on Linux and macOS `make` is preinstalled or ubiquitous; on Windows
it is not and must be installed separately (e.g. `winget install ezwinports.make`). `uv` manages
the Python 3.11 interpreter itself, so no separate Python install is required beyond `uv`.

---

## What this demonstrates

| Capability | Where |
|---|---|
| Streaming ingestion | `services/honeypot/` → Kafka → `services/consumer/` |
| At-least-once semantics and deduplication | `docs/03`, `transform/models/staging/` |
| Multi-grain warehouse design | Requests, attempts, and chat turns joining on session |
| Partitioned Parquet lakehouse | `services/consumer/`, `data/raw/` |
| Transformation as code | `transform/` — staging → intermediate → marts |
| Data quality testing | dbt tests, quarantine path, `mart_reconciliation` |
| Handling malformed, late, duplicate, out-of-order data | `docs/03` — ten deliberate pathologies |
| Window-function analytics | `transform/models/intermediate/` |
| Train/test isolation in a data model | The observed / truth boundary, `docs/02` |
| Orchestration | `orchestration/` — Dagster |
| Measured LLM evaluation | `docs/04` — two tasks, tiered metrics, rule-based baseline |

---

## The scenario

Lex Luthor has done the reconnaissance and left the Rogues Gallery an application. You pick a
villain and run a four-stage intrusion against a honeypot posing as Batcave infrastructure:
network intrusion, initial exploit, lateral movement, and action on objectives.

Each villain's six powerstats gate which MITRE ATT&CK techniques they can even attempt and how
likely each is to succeed. Killer Croc at intelligence 19 gets three blunt options. Ra's al Ghul at
100 gets the full catalog, including the quiet techniques.

Every request and every attempt is captured. SQL scores each session. Sessions crossing the
threshold go to an LLM analyst, which sees **only what the HTTP sensor logged** and must reconstruct
who attacked and what they did.

The theme is a delivery vehicle. Underneath it is a conventional ingest → land → transform → serve
pipeline with documented handling of bad data and a real evaluation harness.

---

## The two evaluations

Ground truth — the villain, the techniques, the outcomes — is isolated in the data model and never
reaches the model. `assert_no_ground_truth_leakage` walks the dbt lineage graph and fails the build
if any model tagged `triage_input` has a `ground_truth` ancestor.

**Attribution:** which of twelve villains was this? Reported as exact match, top-3, and archetype.
Random baselines are 8.3%, 25%, and ~20%.

**Technique reconstruction:** which ATT&CK techniques were used? Reported as precision, recall, and
F1, plus tactic-level recall and hallucination rate.

Technique recall is reported **by observability tier**, because an HTTP sensor cannot see everything.
Of 23 techniques in the catalog, 7 leave a distinctive trace in web logs, 7 leave ambiguous signal,
and 9 leave essentially nothing. T1078 Valid Accounts sits in the last group for the same reason it
is one of the most common real initial-access techniques: a successful login with legitimate
credentials looks like a successful login.

That turns a bare accuracy number into a detection coverage gap analysis, which is what a security
team would actually produce.

Both tasks are also run by a rule-based baseline. If the LLM does not beat a regex on
high-observability techniques, the README says so.

---

## Architecture

```
Simulator ──► attempt events ──┐
    │                          │
    │ drives                   ▼
Honeypot ──► request events ──► Kafka (3 partitions, keyed by session_id)
                                       │
                                       ▼
                                 Consumer service
                          (batch → partitioned Parquet,
                           offsets committed after flush)
                                       │
                                       ▼
                          data/raw/<event_kind>/dt=/hour=/
                                       │
                                       ▼
                                DuckDB ◄── dbt
                                       │
              ┌────────────────────────┴────────────────────────┐
              ▼                                                 ▼
    OBSERVED [triage_input]                      GROUND TRUTH [ground_truth]
    requests, chat turns, features               attempts, runs, stage progression
              │                                                 │
              ▼                                                 │
    Triage service → LLM ─────► intervention orders ────────────┤
                                                                ▼
                                                   attribution + technique evaluations
                                                   detection coverage by tier

                        Orchestrated by Dagster
```

---

## Quickstart

```bash
make dev-up                                # Redpanda + honeypot + consumer
make attack VILLAIN=riddler DURATION=120   # run a full kill chain headlessly
make transform                             # dbt run + dbt test
make triage                                # LLM triage (or baseline without a key)
make eval                                  # attribution + technique accuracy report
make reconcile                             # data quality reconciliation
make dev-down
```

See [Prerequisites](#prerequisites) above. `GROQ_API_KEY` is optional — without it, triage runs the
rule-based baseline and every metric still reports.

---

## Documentation

| Doc | Contents | Track |
|---|---|---|
| [`docs/01-architecture.md`](docs/01-architecture.md) | Components, data flow, decisions and rejected alternatives | All |
| [`docs/02-data-model.md`](docs/02-data-model.md) | Event contracts, observed/truth boundary, dbt layers, tests | All |
| [`docs/03-attack-simulation.md`](docs/03-attack-simulation.md) | Villain behavior, signatures, ten data pathologies | All |
| [`docs/04-llm-triage.md`](docs/04-llm-triage.md) | Prompt contract, both evaluations, detection coverage | All |
| [`docs/05-local-environment.md`](docs/05-local-environment.md) | Docker, Kafka config, Dagster, Makefile, CI | A |
| [`docs/06-implementation-plan.md`](docs/06-implementation-plan.md) | Phases and checkpoints across all three tracks | All |
| [`docs/07-attack-chain.md`](docs/07-attack-chain.md) | Stage machine, technique catalog, gating, probability model | A |
| [`docs/08-interactive-experience.md`](docs/08-interactive-experience.md) | Console, bat bot, finale, dashboard | B |

**Tracks.** A is the headless pipeline and the shippable milestone. B adds the interactive console
and bat bot. C is an optional cloud landing. Each ends somewhere complete.

Infrastructure work — Terraform and Kubernetes — lives in the companion repository
`k8s-data-platform`. It was removed from this project because nothing here needed it.

---

## Roster note

Twelve villains, verified present in the source dataset. Clayface and Mad Hatter are absent from it
under any name or alias. Man-Bat is present but excluded — his wings are not conducive to a
keyboard.

---

## Attribution

Villain metadata from [akabab/superhero-api](https://github.com/akabab/superhero-api) (MIT), a
static dataset of 563 characters, vendored into the repository so the pipeline has no runtime
dependency on a third-party service.

Technique catalog mapped to [MITRE ATT&CK](https://attack.mitre.org/). ATT&CK is a registered
trademark of The MITRE Corporation.

Batman and related characters are trademarks of DC Comics. This is a non-commercial technical
demonstration.
