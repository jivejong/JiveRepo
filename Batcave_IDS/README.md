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

## Decision log

Every non-obvious technology choice, with what it displaced. Full rationale: `docs/01-architecture.md`.

| Chosen | Over | Why |
|---|---|---|
| Kafka (Redpanda) | A managed cloud streaming service | One container, no account, no expiry. Real sub-second delivery semantics rather than simulated ones — managed alternatives had minimum buffering windows in the tens of seconds. Kafka also appears in far more data-engineering job descriptions than any vendor-specific service. |
| DuckDB | A cloud warehouse (Snowflake, BigQuery) | Trial warehouses expire, which means the repository stops being runnable months after it's written. DuckDB reads the landed Parquet directly, needs no server, and produces identical SQL semantics for everything here. |
| At-least-once, deduplicated downstream | Idempotent producing | Duplicates are made to actually occur (idempotent producing is deliberately disabled) so the deduplication logic is exercised against real duplicate rows, not asserted against a scenario that never happens. |
| One topic, four event kinds | Separate topics per kind | A discriminated envelope keeps ordering guarantees intact across every event for a session, since they all share the `session_id` partition key. Separate topics would break per-session ordering across kinds — exactly what `mart_detection_correlation` depends on. |
| MITRE ATT&CK technique IDs | Invented technique names | The catalog gets authored either way, so real ATT&CK IDs cost nothing extra and turn the project from a game into a detection-engineering exercise. Every ID verified against attack.mitre.org. |
| Observability tiers on techniques | A flat accuracy number | An HTTP sensor cannot see every technique. Modeling that explicitly turns a bare accuracy claim into a detection coverage gap analysis — what a real security team would actually produce. |
| Dagster | Airflow | Native dbt integration gives asset-level lineage almost for free, and the resulting asset graph is far more legible to a reader than a DAG of opaque shell tasks. Airflow is used in the companion repo `k8s-data-platform`, so both are represented across the portfolio rather than duplicated. |
| Infrastructure moved to a separate repo | Terraform/Kubernetes in this repo | A single stateless service and a local broker don't justify a cluster, and provisioning infrastructure whose only purpose is hosting a demo isn't a demonstration of infrastructure skill. That work lives in `k8s-data-platform`, where it's the actual subject. **Removing a technology because it didn't earn its place is a stronger signal than including it because it looks good.** |

---

## Honest limitations

Stated before the numbers below, not after — a reader should know what the results can and can't
claim before seeing what they say.

- **Synthetic data.** Every villain, request, and outcome is simulated. Nothing here reflects a real
  intrusion, and the point is the pipeline and the evaluation methodology, not the realism of any
  individual attack.
- **Single-node broker.** Redpanda runs as one container with no replication — the durability and
  failover story a real production Kafka cluster provides isn't being demonstrated, only the
  producer/consumer/ordering semantics that run identically on top of it.
- **DuckDB's single-writer lock.** Only one process can hold a write connection to the warehouse at a
  time. Dagster and an open DuckDB CLI session can't write concurrently, and the orchestration layer's
  own concurrency pool exists specifically to keep the scheduler and the sensor from colliding with
  each other for the same reason.
- **Cookie-based sessionization hands the defender continuity a real attacker wouldn't give up.** An
  opaque, gap-enforced cookie lets a villain who rotates source IPs and user agents (Penguin's
  henchmen) stay correlated as one session for measurement purposes. A real attacker rotating
  identifiers specifically to evade correlation is unlikely to also carry a stable cookie that
  re-links their own requests — this is a deliberate simplification so the rotation features can
  exist and be measured at all, not a claim that session continuity survives real evasion.
- **Compressed-clock timing, with the compression factor recorded per run.** Most corpus generation
  runs with idle gaps between requests compressed for iteration speed, which measurably flattens
  `inter_request_stddev_ms` below roughly `time_scale` 0.1 — the compressed gaps fall under the HTTP
  round-trip noise floor and the feature starts measuring network jitter instead of villain pacing.
  Every run records its real `timing_compression_factor` on `attack_runs`, so which numbers came from
  a faithful clock versus a compressed one is always answerable from the data itself, not from
  memory.
- **Low observability is one label covering two different detection postures, split 8-to-1.** Eight
  catalog techniques never produce an HTTP request at all — no event exists, so recall there is a
  hard ceiling, not a model failure. One (`valid_accounts`) does land a real request that's
  indistinguishable from legitimate use — camouflaged, not invisible, and recall is at least
  theoretically possible from session context.
- **`deploy_batbot`'s evidence is structurally unmeasurable in Track A specifically**, independent of
  sample size or model quality: its detection signature is `chat_turns_completed`, and Track A never
  produces a chat turn — the endpoint it targets 404s until Track B's bat bot exists. It's attempted
  like any other technique (technique selection doesn't know this), but no amount of data will ever
  produce recall on it until Track B lands.
- **The LLM evaluation ran on Groq's free tier against a substituted model.** The originally planned
  model was removed from Groq's serving catalog entirely during this project; the replacement that
  actually works is a smaller model than intended, and free-tier rate limits contributed to roughly a
  third of the LLM's reported parse failures. The comparison below is a real measurement, not a
  simulation — but it's a measurement of one specific free-tier-accessible model under real rate
  limits, not a ceiling on what a better-resourced run would show.
- **The Killer Croc and Ra's al Ghul result — arguably the project's most interesting finding — rests
  on 3 sessions each**, of which only 2 (Croc) and 2 (Ra's al Ghul) actually produced a real LLM
  response before hitting a rate limit or a parse failure. It's a real, verified result, and it's
  also an n of 2. Treat it as a finding worth a larger follow-up run, not a settled conclusion.

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
Of 23 techniques in the catalog, 7 leave a distinctive trace in web logs and 7 leave ambiguous
signal. The remaining 9 aren't one bucket: 8 never reach the honeypot at all — no HTTP request
happens, so recall there is a hard ceiling, not a model failure — and 1, T1078 Valid Accounts, does
land a real request that just looks like legitimate use, for the same reason it's one of the most
common real initial-access techniques: a successful login with legitimate credentials looks like a
successful login. Recall on that one is theoretically possible from session context; recall on the
other 8 isn't possible from the event log at all, because there is no event.

**A second limit, resolved in Phase 6 rather than stated as an open gap:** the simulator originally
picked techniques in strict catalog order rather than at random, so the last entries in a long
stage-4 list were never reached, leaving 4 of 23 techniques with zero attempts — recall for them was
undefined rather than zero, since an attacker can't fail to be caught doing something they never did.
Fixed by shuffling candidates per stage before selection (deterministic per seed, uniform over many).
Confirmed on the re-run corpus: **all 23 catalog techniques are reachable**, and every coverage figure
below is measured over the full 23, not a reduced denominator.

That turns a bare accuracy number into a detection coverage gap analysis, which is what a security
team would actually produce — including being explicit about what the corpus cannot measure.

Both tasks are also run by a rule-based baseline. If the LLM does not beat a regex on
high-observability techniques, the README says so.

### Results (real run, 36 sessions across all twelve villains)

| | baseline | LLM (`qwen/qwen3.8-27b`, Groq free tier) | random |
|---|---|---|---|
| Attribution — exact | 30.6% | 16.7% | 8.3% |
| Attribution — top-3 | 77.8% | 36.1% | 25.0% |
| Attribution — archetype | 41.7%¹ | 36.1% | ~20% |
| Technique — precision / recall / F1 | 65.4% / 39.7% / 0.49 | 57.0% / 20.5% / 0.30 | — |
| Coverage — high tier recall | 89.7% | 44.3% | — |
| Coverage — partial tier recall | 0.0% | 0.0% | — |

![Bar chart of technique recall by observability tier: baseline reaches 89.7% on high-observability techniques and 0% elsewhere; the LLM reaches 44.3% on high, 11.1% on camouflaged low-observability techniques, and 0% on partial and no-evidence tiers](docs/images/detection-coverage.svg)

*Generated from `mart_detection_coverage` by `docs/images/generate_coverage_chart.py` — hand-written
SVG, regenerates whenever the numbers do, no plotting dependency.*

¹ Not apples-to-apples with the LLM's number: the baseline's archetype guess is a mechanical lookup
on its own villain guess (right villain ⇒ right archetype, for free), and 11 of its 15 archetype
matches are exactly that — only 4 are genuine wrong-villain-same-family hits. The LLM's archetype
field is independently predicted, asked for separately from the villain guess, so its 36.1% is real
family-level recognition. Full breakdown: `docs/04-llm-triage.md`.

**The baseline wins on pattern-matchable evidence. The LLM wins where evidence is sparse or absent.
The partial tier is 0% for both.** That's the actual result, not the accuracy table above it.
High-tier signatures are pattern-matchable by definition, and the baseline cleans up there. But on
Killer Croc and Ra's al Ghul — two villains the baseline scored **0 for 3** on, flagged as
structurally hard in earlier phases — the LLM identified Killer Croc correctly on both of its real
responses ("high volume of requests (149 rpm) concentrated almost exclusively on a single endpoint
... 93% error rate, matching Killer Croc's signature"), and on Ra's al Ghul it correctly found
*nothing* — confidence dropping to 0.45 from a typical 0.85–0.92, reasoning that named the exact
signatures it couldn't find ("the lack of specific signatures (riddles, IP rotation, burst patterns)
makes attribution uncertain"). That calibrated non-answer, not a guess, is a behavior the baseline
has no equivalent of, and it lines up with an independent finding from Phase 3's ablation: Ra's al
Ghul's discriminator wasn't load-bearing to begin with. Two different methods, same conclusion.

Reported without adjustment: the LLM's parse-failure rate was 27.8% (10/36). 4 of those never got a
real response at all — Groq's free-tier rate limit cut them off before generation — the other 6 are
genuine failures to produce parseable JSON. The environment measurably contributed to that number;
the number itself is reported as measured, not adjusted for what the infrastructure cost it. The LLM
half of this comparison ran on a free tier against `qwen/qwen3.8-27b`, a substitute for the originally
planned model (Groq removed every plain Llama chat model from serving during this project; a second
substitute, a reasoning model, produced zero valid parses across 72 calls before this one was found to
work) — that bounds what the comparison establishes. Full results, the Killer Croc / Ra's al Ghul
detail, and a hand spot-check of the LLM's evidence fields: `docs/04-llm-triage.md`.

### One deliberate simplification, stated up front

Requests are grouped into sessions by an opaque cookie the honeypot sets, gap-enforced. That lets a
villain who rotates source IPs and user agents (Penguin's henchmen, high-intelligence evasion) stay
one session while the rotation still registers in the `distinct_source_ips` / `distinct_user_agents`
features. It also hands the defender continuity a real rotating attacker wouldn't give up — someone
rotating identifiers to evade correlation is unlikely to also carry a cookie that re-links their
requests. Real sessionization under adversarial rotation is probabilistic and hard; the cookie is a
deliberate simplification so the rotation features can exist and be measured, not a claim that
cookie continuity survives evasion. Called out here rather than left implied (details: `docs/02`).

---

## Data quality: ten deliberate pathologies

Most portfolio pipelines only ever run on clean data. This one injects ten failure modes into the
**observed** request stream — never into ground truth — at fixed rates (`services/simulator/pathologies.yml`),
so the consumer and the dbt layer have something real to handle. `make pathology-check` runs a
seeded corpus with every pathology enabled, consumes the topic back raw, and counts each one in real
data. Counts below are from a `runs=12`, `time_scale=0.02`, `seed=0` corpus — 144 sessions, 2,592
request events on the topic:

| # | Pathology | Injection | Observed count | Handled in |
|---|---|---|---|---|
| 1 | Duplicate delivery | ~2% re-sent with identical `event_id` | 40 | dedupe (Phase 5) |
| 2 | Out-of-order `client_ts` | shuffled within a 30s window | 137 | never order on `client_ts` |
| 3 | Unkeyed messages | ~1% with a null Kafka key | 24 | land normally; span partitions |
| 4 | Late arrivals | ~1% with `client_ts` 1–6h old | 36 | partition on `received_at` |
| 5 | Malformed JSON bodies | ~3% truncated/unbalanced | 21 | `body_is_valid_json = false` |
| 6 | Undeserializable | ~0.2% raw non-JSON bytes | 2 | quarantine (Phase 4 consumer) |
| 7 | Missing required fields | ~0.5% null `source_ip` / null `path` | 12 / 16 | null-ip kept, null-path quarantined |
| 8 | Schema drift | mid-run bump to `schema_version=v2` + `tls_fingerprint` | 1,141 | staging tolerates the new column |
| 9 | Clock skew | future `received_at` / negative `response_time_ms` | 20 / 24 | quarantine / clamp to null |
| 10 | Burst | occasional 10x rate spike | 78 | consumer lag recovers, no loss |

Two counts are split because the halves diverge downstream: missing fields (null `source_ip` is kept
and fed to a ratio, null `path` is quarantined) and clock skew (future `received_at` is quarantined,
negative `response_time_ms` is clamped to null and counted). Schema drift is high because the bump
persists for the rest of a run once it fires mid-session, so every later request in that run carries
the new column. Phase 3 verifies these are all **present and distinguishable in real consumed data**;
the consumer (Phase 4) and dbt (Phase 5) verify each is correctly handled. Two more coexisting cases
the checkpoint separates: Two-Face's deliberate duplicate *requests* (distinct `event_id`) must
survive the dedupe that removes duplicate *delivery* (identical `event_id`).

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

**The asset lineage graph, from `dagster dev`** (`make dagster`, UI on `:3000`):

![Dagster asset lineage: raw_events feeding staging models, through intermediate and marts layers, into a triage source asset, into the evaluation marts](docs/images/dagster-lineage.png)

*Capture instructions: `make dagster`, open `http://127.0.0.1:3000`, go to Assets → view as graph,
select all, and screenshot the full lineage. The interesting shape to have on screen: `raw_events` on
the left, the dbt-model bands (`raw`/`staging`/`marts` group colors) in the middle, and the
`triage/raw_triage_predictions` node sitting between `mart_threat_scores` and
`fct_intervention_orders` — that's the Python step in the middle of the dbt graph, and it's the one
thing a static docs/01 diagram can't show as convincingly as the real tool can.*

**Consumer lag during a burst, from Redpanda Console** (`docker compose up`, console on `:8080`):

![Redpanda Console showing attack.events consumer lag climbing during a burst of simulator traffic, then draining back to zero](docs/images/redpanda-lag.png)

*Capture instructions, verified for real before writing them down: with the stack up, open
`http://localhost:8080` → Topics → `attack.events` → Consumer Groups, start watching lag, then in
another terminal run `make attack-all TIME_SCALE=0.02` (the harness's fast default, not `1.0`) to
produce a real burst. **`TIME_SCALE=1` (faithful) does not work for this** — at real villain pacing,
requests trickle in well within the consumer's 30-second flush window and lag never leaves zero; only
a compressed time scale bunches all twelve villains' traffic tightly enough to outrun it. Verified: a
fresh `TIME_SCALE=0.02` run took `TOTAL-LAG` from 0 to a peak of 322 (`rpk group describe
attack-events-writer`) and back to 0 within the 30-second window, no manual tuning needed. Screenshot
while lag is visibly nonzero and climbing, ideally with enough history in view to also show it draining
back down — that drain is the more informative half, since it's the proof the consumer keeps up rather
than falls permanently behind.*

---

## Quickstart

```bash
make dev-up                        # Redpanda + honeypot + consumer
make attack VILLAIN=558-riddler    # run one villain against the honeypot
make transform                     # dbt run + dbt test
make triage                        # LLM triage (or baseline without a key)
make eval                          # attribution + technique accuracy report
make reconcile                     # data quality reconciliation
make coverage                      # detection coverage by observability tier
make dagster                       # orchestration UI on :3000
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
| [`docs/09-engineering-log.md`](docs/09-engineering-log.md) | Bugs, diagnoses, and tuning decisions — what broke, how it was caught, what changed | All |
| [`docs/exercises.md`](docs/exercises.md) | One-off demonstrations against the live stack, with real output | A |

**Tracks.** A is the headless pipeline and the shippable milestone. B adds the interactive console
and bat bot. C is an optional cloud landing. Each ends somewhere complete.

Infrastructure work — Terraform and Kubernetes — lives in the companion repository
`k8s-data-platform`. It was removed from this project because nothing here needed it.

**The engineering log is worth a specific mention.** A rule-based classifier confused two villains on
the same feature twice, in two unrelated places, months apart — a dbt test and, independently, a
baseline model — before either was traced to the same underlying ambiguity. A verification run for
this very phase silently shifted a reported number by 2.4 points before the mismatch against an
already-published figure caught it. A JSON-body-validity check flagged 43 of 59 requests as invalid
in a corpus with zero malformed bodies, because it was answering the wrong question, not the wrong
answer. `docs/09-engineering-log.md` has the rest, organized by what broke and how it was actually
caught — most of them by a number that didn't add up, not by a test.

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
