# CLAUDE.md

Context for Claude Code working in this repository.

---

## ▶ CURRENT POSITION — update this as work proceeds

```
TRACK:  A  (headless core pipeline)
PHASE:  7  (orchestration and presentation) — IN PROGRESS
        Done: Dagster orchestration layer (orchestration/definitions.py),
        verified zero-credential end to end via `dagster job execute`; the
        four overdue Makefile stubs (reconcile/coverage/docs/dbt-test); the
        README's decision log, honest limitations, and results chart
        (docs/images/generate_coverage_chart.py, hand-written SVG);
        docs/09-engineering-log.md; a second real exercise in
        docs/exercises.md; the CI workflow's missing dbt-build step, added
        and verified in a clean git worktree.
        In flight: verifying .github/workflows/batcave-ids.yml is actually
        green on GitHub Actions via a `ci-verify` branch (never main, per
        instruction) — two real issues found and fixed so far
        (astral-sh/setup-uv@v10 doesn't exist as a moving tag, pinned to
        v10.1.0; the workflow's own `paths: ["Batcave_IDS/**"]` filter means
        an edit to the workflow file itself never triggers a push-based run,
        so workflow_dispatch was added). ci-verify branch to be deleted once
        confirmed green.
        Still open: the two GUI screenshots (Dagster lineage, Redpanda
        consumer lag) need a human to capture — instructions are in the
        README's Architecture section. `make dagster` has been left running
        on :3000 for this.
IN SCOPE:    docs/01, 02, 03, 04, 05, 06, 07
OUT OF SCOPE: docs/08  — no console, no bat bot, no finale, no dashboard
```

**Phase 3 status (paused mid-separability-loop, nothing pushed).** Built and committed: cookie
session derivation (replaced the Phase 1 IP+UA key — rotation broke it; see docs/02), per-run/
per-request identity (RFC 5737 IPs), absurd-method logging, Layer 1 `BehaviorProfile` +
intelligence-aware detection (calibration gap 0.006 over non-detected attempts, INT↔detection
r=-0.957), Layer 1 traffic shaping, Layer 2 signatures, the separability harness (`make
separability`: N-run averaging, effect-size metric, SQL features for Phase 5 to lift), and the
request-budget volume fix (volume now 5-46 attempts, ~6-9x; Croc highest, matching docs/03).

Corrected against real data (docs/03/06/07): pivot_ratio leader is Joker not Ra's, retry leader is
Croc; low-durability villains identifiable by signature not outcome; Ra's discriminator is low
wasted_request_ratio + max_path_tier 4 (a pairing, not one feature). `wasted_request_ratio` redefined
progress-based (r=-0.05 vs error_ratio).

**Separability Part 1 — mostly validated; resolved since the status above:**
- Freeze's response-delay signature landed — he's longest-duration again (via holding, not volume).
- Volume fix made Croc highest `request_count` (docs/03 true, not edited).
- Bane's signature (path concentration) added — it was missing entirely; the mid-stat cluster now
  separates on signature features (Bane/Ivy 1.20, all Freeze pairs 2.7–5.0). The residual
  Bane/Harley/Ivy weakness was a **measurement artifact**: `inter_request_stddev_ms` is flat under a
  compressed clock (gaps below the HTTP noise floor). At faithful timing the Joker/Harley burst pair
  separates (effect ≈ 1.2). Timing model + which features are faithful: docs/05, docs/03.
- **Timing/concurrency, for Phase 4:** faithful full-corpus runs are slow (idle-gap-dominated);
  thread concurrency to speed them up hits a fatal C-extension GIL crash and was abandoned. The
  **sequential path the simulator uses is clean** — `make attack` runs fine; the crash was only the
  harness's threaded optimization. Compressed-by-default + faithful subset is the model; the
  compression factor will be recorded on `attack_runs`. Phase 4's larger pathology corpus should
  dial `time_scale` up for size rather than expect a fast faithful run.

**Separability Part 1 — DONE.** All named checkpoint separations hold (measured, `make
separability`): Riddler/Two-Face on signature features (riddle d≈6, duplicate d≈1.7, outcome flat),
Scarecrow/Penguin and Joker/Harley both out of the closest-10, Joker/Harley validated at faithful
timing (effect ≈1.2), Croc highest request_count + retry_ratio, Freeze longest duration. Closest
remaining pair is Catwoman/Ra's al Ghul (0.77) — an *intended* similarity (both efficient operators
reaching tier 4 in <25 requests), not a failure. Ablation: `error_ratio` load-bearing for 11/66
pairs (strong keep), `wasted_request_ratio` for 2/66 (Riddler pairs — marginal keep, weight lightly
in Phase 6). docs/02 ambiguity pass done (7 features flagged for Phase 5). Optional refinement
recorded, not built: Harley's bimodal burst shape (docs/03/04).

**Part 2 — DONE (verified against real consumed data, `make pathology-check`).** All ten docs/03
pathologies injected into the observed request stream only (never ground truth) via
`services/simulator/pathologies.yml` + `pathologies.py` (single decision-maker; simulator-direct vs
honeypot-executed via the `X-Sim-Pathology` header). Each verified present with a count on a
`runs=12`/`time_scale=0.02`/`seed=0` corpus (144 sessions, 2,592 request events): duplicate_delivery
40, out_of_order 137, unkeyed 24, late_arrival 36, malformed_body 21, undeserializable 2,
missing_source_ip 12, missing_path 16, schema_drift 1141, clock_skew_future 20, clock_skew_negative
24, burst 78; attack_run 144. Clock skew and missing-fields kept as **two counts each** (they diverge
downstream). The three specific checks pass: Two-Face distinct-`event_id` duplicates coexist with and
are distinguishable from identical-`event_id` delivery duplicates; undeserializable emitted now
(consumer quarantine is Phase 4); distinct runs → distinct `session_id`s (Phase 2 merge bug stays
fixed). `attack_runs` published as a 5th `event_kind`, keyed by `run_id`, carrying `villain_slug` +
`timing_compression_factor`. `run_id` now links attempt/request/attack_run events. Reconciliation
(2,442 sent / 2,592 on topic) captured in docs/03; Phase 5's `mart_reconciliation` must reproduce it
(cross-check line added to docs/06 Phase 5). PyYAML declared as a direct dep (pyproject/docs05/CLAUDE);
dep audit found no other gaps. Invented mechanisms documented: `X-Sim-Pathology`/`X-Run-Id` headers
(docs/01 consolidated table), `attack_run` kind (docs/02).

**Phase 4 — DONE (verified by killing the consumer mid-batch, `docs/exercises.md`).** Consumer
(`services/consumer/`) lands `attack.events` as partitioned Parquet:
`data/raw/<event_kind>/dt=/hour=/part-<uuid>.parquet`, hand-declared schema per kind (parity with the
Pydantic models enforced by test — inferring per batch broke on all-null nullable columns), atomic
temp-then-rename writes, flush on 5,000 messages/30s. **Offset-commit ordering proven live, not read
from the code:** a debug-only pre-commit delay (`CONSUMER_DEBUG_PRE_COMMIT_DELAY_S`, default 0, never
set in compose) widened the write-then-commit window; killed the process inside it; `rpk group
describe` showed a fully-written 92-row batch with zero offsets committed (full lag); restart replayed
the exact same 92 rows byte-for-byte (184 landed = 92+92, one event_id/offset pair shown landing a
minute apart). Undeserializable messages quarantine to `data/quarantine/undeserializable/`
(offset in filename + content, idempotent on replay) and the consumer *continues* — proven via
later-landed rows on the same partition, not just a file existing. `make landing-check` (DuckDB over
the Hive layout, same as Phase 5's dbt sources will read it) verifies partitions, reconciliation
against `attack_runs`, and the two duplicate populations Phase 5 must treat oppositely (replayed/
delivery dup by identical `event_id` → dedupe removes; Two-Face's signature by distinct `event_id` at
same session+path → dedupe keeps, properly villain-scoped via `run_id`→`attack_run.villain_slug` after
a bug found live where Killer Croc's real retries — highest `retry_ratio`, docs/03 — inflated an
unscoped version of that count). **Conflict C resolved:** `docs/05` was the layout outlier (a single
`attack_events/` dir + unpartitioned `attack_runs/`) and was fixed to match docs/01/CLAUDE.md/the
`.gitignore` pattern/the actual consumer output, which all already agreed. `.gitignore`'s
`sample-*` negation verified against a real landed Parquet file (copied, checked, deleted — tree left
clean). LocalStack note: checked (grep + `git log -S`), never existed, nothing to remove. Two DuckDB
traps recorded for Phase 5 in docs/06: `union_by_name=true` required (schema drift), and
`received_at` (TIMESTAMPTZ) renders in local time unless `SET TimeZone='UTC'`.

**Phase 5 — DONE.** 70 dbt nodes green (`make transform`), 179 pytest, sqlfluff clean.
staging → intermediate → marts per docs/02, tagged model by model.

- **The boundary.** `assert_no_ground_truth_leakage` is a pytest over `target/manifest.json` walking
  full upstream lineage, wired as a named CI step, in **two permanent layers**: the real manifest, and
  synthetic manifests with known-bad/known-good lineage so the checker stays honest when refactored.
  **Proven to bite on real input** — pointing `stg_attack_events` at `stg_attack_runs` failed with the
  exact path, then reverted. `attempt_id` is stripped at staging and survives only in
  `int_request_attempt_link` `[ground_truth]`.
- **The lift was SPLIT, not copied.** "Lift, don't translate" taken literally would have moved
  `retry_ratio`/`pivot_ratio`/`attempts_per_stage_reached` — attempt-derived — into the model the LLM
  reads. Observed half → `int_session_features_observed`, truth half → `int_session_features_truth`.
  `make feature-crosscheck`: **max_delta = 0 on all 17 shared features across 144 sessions.**
- **`mart_reconciliation` reproduces the Phase 3 harness exactly**: 2442 sent, 2592 landed, 40
  duplicate copies, 21 invalid bodies, 36 late arrivals, 2102 attempts = attempts_made, 144 runs.
  Counted by distinct `(kafka_partition, kafka_offset)` so the committed sample overlapping the corpus
  can't double-count.
- **Sample partition committed**: 450 rows, 12 sessions (one per villain). Clean-clone `dbt build`
  verified with the stack down — 70 nodes green, all 5 evidence features non-zero, dedupe and
  quarantine both exercised. `make sample-partition` regenerates it.
- **Findings that changed things:** `is_valid_json` judges only JSON-*shaped* bodies (a bare
  `json_valid()` marked 43 of 59 requests invalid in a corpus with zero malformed rows); the
  traversal/injection matchers overlapped until a test caught it; `assert_probability_calibration` now
  uses a 3-sigma binomial band, not a flat gap (overall calibration gap 0.0038, confirming Phase 3's
  0.006); models materialize as **tables** because a view over `read_parquet` re-resolves its glob
  against whoever opens the warehouse.
- **Resolved in Phase 6**: the 4-of-23-unreachable limitation above was fixed by randomizing
  technique selection (`services/simulator/session.py`); confirmed 23/23 reachable on the re-run
  corpus. docs/04, docs/07, and README all updated to the real denominator.
- New dep: `sqlfluff-templater-dbt` (version-locked to sqlfluff AND dbt-core; sqlfluff must run from
  `transform/`).

Phases 0-6 are all locally complete and verified against real output; none has been
pushed, so **CI green is unconfirmed for all seven** — nothing has gone to the public remote yet
(deliberately, per instruction: local first). Phase 7 authorizes one exception: a `ci-verify` branch
push to confirm the GitHub Actions workflow itself, deleted afterward, never `main`. Do not treat any
phase as fully closed until CI is observed green.

**Phase 1 summary** (full detail: docs/02 "Session derivation", docs/01 header table): gap-based
session derivation (not a fixed time bucket — docs/06 corrected), three invented request headers
(`X-Client-Ts`, `X-Sim-Delay-Ms`, `X-Forwarded-For`).

**Phase 2 checkpoint** (docs/06): run a scripted session, confirm Croc gets exactly the low-gate
techniques and Ra's al Ghul the full catalog, confirm no gate violations, confirm attempts and
their HTTP traffic share an `attempt_id`. Done for real against the live stack (`rpk topic
consume`, cross-checked by hand and programmatically against `gated_techniques`), not asserted from
the code:

- **All 23 ATT&CK IDs verified against live attack.mitre.org** — none wrong. Full table and the
  TA0005 (Defense Evasion → Stealth) rename note: docs/07's commit history.
- **Docs/07's narrative was wrong on both named villains, found by computing gates, not reading
  prose.** Killer Croc stalls at stage 2 (no stage-3 technique's `min_intelligence` is low enough
  for him — 19 vs. a floor of 45) and never reaches `data_local_system`. Ra's al Ghul clears every
  `min_intelligence` gate but fails `privesc_exploit`'s `min_power=40` (his power is 27), so he
  doesn't reach "the full catalog." **Fixed the narrative, not the seed thresholds** — full
  twelve-villain gated-technique table now in docs/07.
- **`produces_traffic` column added** (`techniques.csv`) — whether a technique touches the honeypot
  is fixed per-technique, independent of outcome. Splits `low` observability into two real
  detection postures (8 no-evidence, 1 camouflaged) — carried into docs/07, docs/04, and the README.
- **`detected` outcome defined** — named in the schema, never specified anywhere. Orthogonal to
  success/failure, driven by `noise_generated` (docs/07).
- **`X-Attempt-Id` (request) / `X-Session-Id` (response)** wire formats added to the honeypot —
  the actual attempt-to-request correlation mechanism, verified by hand against real consumed
  Kafka messages (one `attempt` event, its exact matching `request` event(s), same `attempt_id`
  *and* `session_id`).
- **Two Docker bugs found and fixed while bringing the stack up for the checkpoint**: the honeypot
  image was missing `services/common/` (crash-looping, but reported "healthy" because it had no
  real healthcheck — both fixed); Docker's own healthcheck was polluting `attack.events` with
  synthetic traffic once a real healthcheck existed (`/healthz` added, verified 0 messages over 4
  healthcheck cycles).
- **Two open questions handed to Phase 3 with real data attached, not decided here**: whether
  Killer Croc's stage-3 wall is the intended story (full twelve-villain distribution in docs/07);
  per-run `source_ip` variation, needed before `attack-all`'s sequential runs will separate into
  distinct sessions (confirmed empirically — two villains run back-to-back landed in one session).

`k8s-data-platform/` has been moved out to be a sibling of `Batcave_IDS` at the JiveRepo root,
matching its own HANDOFF.md. Resolved.

**Do not build anything from `docs/08-interactive-experience.md` while Track A is in progress**,
even if it would be quick, even if the spec is right there. Track A must run end to end headlessly
first. If a Track A phase seems to require a user interface, it does not — re-read the phase in
`docs/06-implementation-plan.md`.

Track A phases are 0–7 and end at a shippable, public-ready repository. Track B is 8–10. Track C is
an optional cloud landing.

---

## What this project is

A portfolio data engineering project: a streaming pipeline that simulates a five-stage intrusion and
evaluates an LLM's ability to reconstruct it. The Batman theme is presentation. Every architectural
decision must be defensible as production data engineering practice.

**Primary audience:** data engineering team leads and internal recruiters who will *read* this
repository, not run it. Legibility of the artifacts matters more than feature count.

**Design constraint:** Track A runs locally with Docker and Python. No cloud account, no trial
credentials, no service that can expire. This project must still run unchanged in two years.

## Working principles

1. **Verify against real output before building the next layer.** Do not write transformation models
   against an assumed event shape. Emit real events first, inspect them, then build on top. Every
   phase has a checkpoint. Stop at each one.
2. **No happy-path-only code.** The simulator deliberately emits malformed, duplicate, late, and
   out-of-order records. Handling them is the point, not an edge case.
3. **Nothing silently dropped.** Every bad record is deduplicated, quarantined, flagged, or counted,
   and the count is reportable.
4. **Ground truth is sacred.** See the hard constraints below.

## Stack

- **Python 3.11+**, `uv` for dependency management
- **Redpanda** — Kafka-API broker, single container, no ZooKeeper
- **confluent-kafka** — Python client (librdkafka). Not `kafka-python`.
- **pyarrow** — Parquet writing
- **DuckDB** — warehouse, reads Parquet with Hive partitioning
- **dbt-core + dbt-duckdb** — transformations, single target
- **Dagster** (`dagster`, `dagster-dbt`, `dagster-duckdb`) — orchestration
- **Groq** — LLM inference. `GROQ_API_KEY` optional; without it the rule-based baseline runs.
- **FastAPI + uvicorn** — the honeypot's HTTP server (Phase 1). Not in the original spec; added
  because Pydantic (already a dependency) is what FastAPI is built on, so request validation and
  event validation share one schema layer, and because `http.server`'s synchronous model would
  measure the server rather than the villain during the burst pathology's 10x rate spike
  (docs/03).
- **httpx** — HTTP *client* library, used by the simulator (Phase 2+) to drive the honeypot. Not
  to be confused with the server framework above.
- **PyYAML** — reads `services/simulator/pathologies.yml` (Phase 3 Part 2).
- Dev tools: `ruff`, `pytest`, `sqlfluff` (DuckDB dialect) + `sqlfluff-templater-dbt`,
  `pre-commit`. The templater is version-locked to both sqlfluff and `dbt-core`, and sqlfluff must
  run from `transform/` — see docs/05.

## Repository layout

```
services/
  honeypot/          # HTTP service, fake Batcave endpoints, Kafka producer
  consumer/          # Kafka consumer, batches to partitioned Parquet
  simulator/         # stage machine, villain behavior, pathology injection
  triage/            # LLM triage + evaluation harness
  batbot/            # Track B only
console/             # Track B only
transform/           # dbt project
  models/staging/ intermediate/ marts/
  tests/ macros/
  seeds/villains.csv techniques.csv
orchestration/       # Dagster definitions
data/
  villains/          # vendored akabab subset
  raw/<event_kind>/  # landed Parquet (gitignored except one sample partition)
  quarantine/
  warehouse.duckdb   # gitignored
docs/
docker-compose.yml
Makefile
```

## Conventions

- SQL: lowercase keywords, trailing commas, CTEs over subqueries. One model per file.
- dbt models named `stg_`, `int_`, `fct_`, `dim_`, `mart_`.
- **Every dbt model carries exactly one of the tags `triage_input` or `ground_truth`**, or neither
  if it is a shared dimension. This is not optional — the leakage test depends on it.
- Timestamps stored UTC, suffixed `_at`. Never use a client-supplied timestamp as a watermark.
- Kafka messages keyed by `session_id` so per-session ordering holds within a partition.
- Secrets via environment variables only. `.env.example` committed, `.env` gitignored.
- Commit in small, logical increments. Do not batch a phase into one commit.

## Hard constraints

- **Never let ground truth reach the triage model.** `attack_attempts`, `attack_runs`,
  `int_stage_progression`, and `int_session_features_truth` are ground truth. `attempt_id` is a join
  key for evaluation, stripped from every `triage_input` model. Contaminating the input invalidates
  every number in the repository.
- **Never use `client_ts` for partitioning, ordering, or watermarking.** It is deliberately
  unreliable. Use `received_at`.
- **Never commit `user_text` from bat bot conversations** to the sample partition. Turn metadata and
  intent flags only.
- The honeypot must not proxy, forward, or execute anything from a request body. It logs and returns
  canned responses. It is a fake target, not a real one.
- Track B only: the console never takes over the browser viewport, never requests fullscreen, and
  keeps the `SIMULATION` frame visible at all times including the finale.
- Commit one small sample partition per event kind (a few hundred rows) so a reader can inspect real
  output and `dbt run` works from a clean clone. Gitignore the rest.

## Commands

```bash
make dev-up          # docker compose up: redpanda, console UI, honeypot, consumer
make dev-down        # tear down, preserve data/
make dev-reset       # tear down and wipe generated data/raw + warehouse (spares the committed sample)
make attack VILLAIN=<slug> [TIME_SCALE=<factor>]   # one villain, one run
make attack-all      # all twelve villains sequentially
make landing-check   # verify what the consumer landed (Phase 4 checkpoint)
make transform       # dbt deps && dbt run && dbt test
make triage          # score sessions, call LLM (or baseline), write orders + evaluations
make eval            # attribution + technique reconstruction report
make reconcile       # print mart_reconciliation
make coverage        # print mart_detection_coverage
make dagster         # dagster dev (UI on :3000)
make docs            # dbt docs generate && dbt docs serve
make fmt             # ruff + sqlfluff
make test            # pytest && dbt test
```
