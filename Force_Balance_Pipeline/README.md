# Force Balance Pipeline

A streaming ETL system that detects disturbances in the Force.

An intergalactic probe sweeps all 60 planets in the Star Wars API every 15 minutes, reading three
Force channels. A web intake lets anyone report what they witnessed, with a model inferring
sensor-equivalent values from free text. Everything lands in a Databricks lakehouse, is scored
against rolling 90-day per-planet baselines, and is classified into disturbance signatures. When
the Force becomes imbalanced, a Yoda agent decides whether to deploy Jedi — which ones, on which
ship, and why.

All telemetry is simulated. Reference data comes from [SWAPI](https://swapi.info), extended by a
governed AI enrichment layer.

---

## Why this project exists

It's a portfolio piece. The Star Wars framing is deliberate — it makes an otherwise dry
demonstration of streaming ingestion, late-arriving data handling, and dimensional modeling into
something a person will actually read.

The engineering problems are real ones:

| Problem | Where it shows up |
|---|---|
| Late-arriving data and event-time skew | `DISCONNECTED` buffers offline; `BURST` replays hours later |
| Idempotent ingestion | ULID-named immutable files, dedup on `event_id`, exactly-once Auto Loader |
| Contextual null semantics | `STEALTH` nulls are expected; fault-injected nulls are rejects. Same shape, different meaning |
| Data quality quarantine | 2–3% deliberate fault injection feeds `silver.rejects` |
| Schema evolution without migration | `VARIANT` payload column in bronze |
| Governed AI-generated data | Enrichment is frozen, reviewed, version-controlled, and provenance-tracked |
| Statistical detection | Composite deviation score over signed z-scores vs. rolling 90-day baselines |
| Deterministic classification | Signature typing in SQL, not by an LLM |
| Trust boundaries in a data model | Inferred values can trigger emergencies but never enter baselines |
| Agent guardrails and evaluation | Tool-calling agent, code-enforced constraints, regression fixtures |
| Human/machine decision parity | Manual deployments run the identical constraint layer |
| Cost-constrained architecture | Entire system runs inside free tiers |

---

## The three channels

| Channel | Behavior | Driven by |
|---|---|---|
| **Midichlorian density** (ppm) | Stable, σ 2–5% of baseline | Population density, sentient life, Force connection |
| **Kyber resonance** (0–100) | Moderately variable, σ 8–15% | Geology, crystal deposits, Force-attuned sites |
| **Dark side activity** (0–100) | Quiet with rare spikes | Sith history, atrocity, dark side nexuses |

Per-planet baselines and spreads come from the AI enrichment layer, so Mustafar is dark-volatile,
Coruscant is midichlorian-rich and steady, and Utapau's kyber readings run high.

## Signatures

Disturbances are classified deterministically from the *pattern* across the three signed
z-scores, which is what makes Jedi selection non-arbitrary:

| Signature | Pattern | Responds with |
|---|---|---|
| `sith_presence` | dark ↑↑, kyber ↓ | combat |
| `dark_adept` | dark ↑, midi ↑ | combat |
| `nexus_awakening` | midi ↑↑, kyber ↑↑ | investigation |
| `force_drain` | midi ↓↓, kyber ↓↓ | investigation |
| `kyber_cache` | kyber ↑↑ only | diplomacy |
| `civil_unrest` | dark ↑, high population | diplomacy |
| `veiled_presence` | dark ↑ under partial coverage | stealth |

---

## Architecture

```
  ┌──────────────────────┐      ┌──────────────────────┐
  │ Intergalactic probe  │      │ Web intake           │
  │ RPi 3 · 60 planets   │      │ free-text report →   │
  │ /15min · 4 modes     │      │ model-inferred values│
  └──────────┬───────────┘      └──────────┬───────────┘
             │  MQTT                  HTTP │
             └────────────┬────────────────┘
                          ▼
                ┌───────────────────┐
                │ Collector bridge  │   GCP e2-micro (always free)
                │ batch → NDJSON    │   + report inference endpoint
                └─────────┬─────────┘
                          │ Files API
  ┌───────────────────────┼────────────────────────────────┐
  │  Databricks Free Edition                               │
  │                       ▼                                │
  │  ┌────────────┐  ┌─────────────┐  ┌─────────────────┐  │
  │  │ UC volume  │→ │ Auto Loader │→ │ bronze → silver │  │
  │  │ landing    │  │ availableNow│  │ → gold  (dbt)   │  │
  │  └────────────┘  └─────────────┘  └────────┬────────┘  │
  │   rolling 90d baselines · composite scoring │          │
  │   signature classification · incident rules │          │
  └─────────────────────────────────────────────┼──────────┘
                        ┌──────────────────────┴───────┐
                        ▼                              ▼
              ┌───────────────────┐         ┌───────────────────┐
              │ Jedi Council      │         │ Yoda agent        │
              │ dashboard +       │         │ deploy/stand down │
              │ manual deployment │         │ + audit trail     │
              └─────────┬─────────┘         └─────────┬─────────┘
                        └──────── shared constraint layer ──────►
```

Full detail: [`docs/01-architecture.md`](docs/01-architecture.md)

---

## Stack

| Layer | Technology |
|---|---|
| Edge — probe | Python 3, paho-mqtt, SQLite store-and-forward, Raspberry Pi 3 |
| Edge — intake | Vanilla JS, IndexedDB buffering |
| Transport | Mosquitto (MQTT), Databricks Files API |
| Lakehouse | Databricks Free Edition, Delta Lake, Unity Catalog, Auto Loader |
| Transformation | dbt Core + `dbt-databricks`, orchestrated by Lakeflow Jobs |
| Serving | PostgreSQL |
| Inference | Gemini (`gemini-3.1-flash-lite`) — report intake and Yoda agent, each with its own fixture suite |
| Dashboard | Node.js + React |
| Reference data | SWAPI + governed AI enrichment layer |
| Local development | Docker Compose |

---

## Quickstart

Runs the full pipeline locally with no cloud account. Postgres stands in for the lakehouse; the
dbt models are the same ones that run on Databricks.

```bash
git clone https://github.com/jivejong/JiveRepo
cd JiveRepo/Force_Balance_Pipeline
make demo
```

Starts Mosquitto, Postgres, the probe simulator, the bridge, and the dashboard, then loads a
90-day synthetic history so baselines are populated immediately. Open http://localhost:3000.

The probe runs a compressed schedule, so you'll see a `DISCONNECTED` gap and its `BURST` replay
within a few minutes. To trigger a specific disturbance:

```bash
make inject SIGNATURE=sith_presence PLANET=coruscant
```

To run against Databricks instead, see [`docs/05-platform-setup.md`](docs/05-platform-setup.md).

---

## Documentation

| Doc | Contents |
|---|---|
| [01 — Architecture](docs/01-architecture.md) | Component boundaries, platform constraints, design decisions |
| [02 — Event contract](docs/02-event-contract.md) | Envelope schema, payloads, landing zone, `source_type` semantics |
| [03 — Data model](docs/03-data-model.md) | Medallion layers, composite scoring, signature classification |
| [04 — Edge simulator and intake](docs/04-edge-simulators.md) | Four probe modes, fault injection, report inference |
| [05 — Platform setup](docs/05-platform-setup.md) | Databricks workspace, dbt project, job topology |
| [06 — Yoda agent](docs/06-yoda-agent.md) | Tools, constraint layer, evaluation fixtures |
| [07 — Implementation plan](docs/07-implementation-plan.md) | Phased build order with verification checkpoints |
| [08 — AI enrichment](docs/08-ai-enrichment.md) | Enrichment prompts, review process, provenance rules |

---

## A note on the AI enrichment layer

Planet Force parameters and Jedi attributes are LLM-generated. This is done once, with the model's
default sampling settings, human-reviewed, and committed as version-controlled seed CSVs with
recorded provenance. Reruns would not reproduce the committed values, which is why the reviewed
CSVs, not the model, are the source of truth. It is never called at runtime.

That constraint is load-bearing rather than cosmetic: the 90-day backfill derives from these
parameters, and every rolling baseline derives from the backfill. Regenerating enrichment would
silently invalidate all historical statistics. See [`docs/08-ai-enrichment.md`](docs/08-ai-enrichment.md).

Enrichment content is generated from model knowledge and human-reviewed. It is not scraped from
any wiki.

---

## Attribution

Star Wars reference data courtesy of [swapi.info](https://swapi.info), a community-maintained
mirror of the original SWAPI project by Paul Hallett. Star Wars is a trademark of Lucasfilm Ltd.
This is an unaffiliated, non-commercial portfolio exercise.
