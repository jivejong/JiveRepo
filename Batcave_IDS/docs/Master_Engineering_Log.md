# Batcave IDS — Master Engineering Log

Sep 27, 2026 · @Jong

## About this log

This log merges four source logs into one record of batcave-ids, from the first idea (before 2026-09-11) to the bat bot becoming the payload for every cleared run (2026-10-08). It is ordered by date where dates are known and by build phase where they are not.

| Tag | Source | Covers | Authoritative for |
| --- | --- | --- | --- |
| \[A\] | Planning Chat A | Concept, design, Phases 0–7 as reported, Groq→Gemini swap, Track B handoff, first GCP revision | Design rationale and decisions |
| \[CC1\] | Claude Code session, 09-17 → 09-21 | Phase 6, Phase 7, Groq→Gemini swap, close-out | Phase 6–7 commit hashes, exact output, test counts |
| \[C1\] | Track B review chat, ends 09-26 | Phases 8–10, gemini-3.1 swap, CI fixes, GCP checklist | Phase 8 detail and plan-review decisions |
| \[CC2\] | Claude Code session, 09-22 → 09-26, resumed 10-08 | Phase 9 close, Phase 10, latency fix, Track C re-plan, bat bot as payload | Phase 9–10 commit hashes, exact output, test counts |

- Where a chat and a Claude Code session describe the same event, the Claude Code log wins on numbers, hashes and command output.
- Git history was rewritten after the CC1 session. All hashes here are current hashes, mapped by commit subject.
- The early part of CC2 (Phase 9 build) came from a compacted summary, so its detail is secondhand. \[C1\] covers the same work first-hand.
- Places where sources disagree are listed in [Reconciliation notes](#reconciliation-notes) rather than silently resolved.

## Timeline at a glance

Track A (headless pipeline) closed at 102 commits by 09-21; Track B (interactive console) closed with Phase 10 on 09-23; Track C (cloud) is planned but not started.

| When | Track / phase | Outcome | Key commit |
| --- | --- | --- | --- |
| Before \~09-11 | Concept and design | Villain roster, kill chain, observed/truth boundary, local-only stack | — |
| \~09-11 | Handoff doc set | README, CLAUDE.md, docs/01–06 first written | — |
| \~09-11 → 09-17 | Track A, Phases 0–5 | Honeypot, streaming, landing, dbt layer; 137 tests by Phase 4 | — |
| 09-17 | Track A, Phase 6 | Threat scoring, rule baseline, LLM triage on Groq (qwen) | `7a3031b` |
| 09-17 → 09-21 | Track A, Phase 7 | Dagster, presentation, CI green via `ci-verify` branch | `1de4ef9` |
| 09-21 | Maintenance | Groq → `gemini-3.5-flash-lite`; eval re-run | `b0ce0e4` |
| \~09-21 → 09-22 | Track B, Phase 8 | Terminal console; `session_source` field; → `gemini-3.1-flash-lite` | `53be19a` area\* |
| 09-22 → 09-23 | Track B, Phase 9 | Bat bot; four dormant chat-feature bugs fixed | `48e7b04` |
| 09-23 | Track B, Phase 10 | Counterstrike finale, Batanalytics dashboard, orphan-row finding | `5fc91b1` |
| 09-25 | Track C planning | Re-plan to GCP + kind; handoff rewritten | `b439944` |
| 10-08 | Track B change | Bat bot becomes the payload for any cleared run; concurrent-finale collision found and fixed | uncommitted |

\*The Phase 8 + model-swap commit and the ST06 lint fix are not hashed in the sources; `53be19a` is the ST06-era commit named in the CI investigation.

## Part 1 — Concept, positioning and design (before \~09-11)

The project became a fully local data-engineering pipeline whose LLM infers the attacker from behavior alone and is scored against ground truth. \[A\]

### Concept

- **Idea (Jong):** pick a Batman villain, run a simulated attack against a honeypot, and have an LLM decide whether Batman intervenes.
- **Architecture correction:** the honeypot absorbs the attack and emits telemetry; the warehouse is an analytics sink, not a router.
- **Core evaluation idea:** never tell the LLM which villain attacked. Villain stats shape behavior; the model's guess is scored against ground truth. This became the evaluation harness.
- **Villain data:** akabab/superhero-api (static CDN JSON, MIT). superhero-api.com rejected as slow and unreliable.

### Positioning

- **Goal:** a repo AI sourcing tools (SeekOut, Eightfold) index well. They read repo metadata, so tool names go in the description, topics and the first README paragraphs; commit steadily.
- **Real gap:** data engineering, Jong's specialty, had no portfolio project. Audience includes internal Booz Allen recruiters and leads.
- **Consequences:** add dbt (staging → intermediate → marts), dbt tests as the quality layer, an orchestrator, and deliberate mess in the generator. The LLM is a downstream consumer, not the headline.
- Existing repos reframed: the chord chart app is a document-to-warehouse ETL pipeline; the SQL art repo shows advanced SQL.

### Stack decisions

| Decision | Rationale |
| --- | --- |
| Build locally first; no AWS account yet | Trial clocks start at signup; personal account avoids IP questions |
| Snowflake dropped; DuckDB | Must keep running after any trial expires; Databricks covers the warehouse story elsewhere |
| Redpanda (Kafka API) instead of Firehose | Real sub-second streaming, at-least-once semantics, no account |
| Dagster instead of Airflow | Native dbt asset lineage; Airflow moves to the Kubernetes repo |
| Terraform + Kubernetes in a separate project (`k8s-data-platform`) | Keeps batcave-ids pure data engineering |

Cost research at the time: EKS control plane about $73/month, Kinesis about $11/month per shard, MWAA several hundred a month. A correction noted that GKE's $74.40/month credit covers only the management fee, not nodes. The missing-AWS gap was flagged, not resolved.

### Design refinement

- **Roster:** 12 villains, checked against the live 563-character dataset. Clayface and Mad Hatter are absent; Man-Bat excluded (“wings aren't conducive for a keyboard”). All six stats used, since four stats left Riddler and Two-Face only 12.0 apart. Seed: `transform/seeds/villains.csv`.
- **Behavior model:** Layer 1 derives traffic shape from stats; Layer 2 adds one lore signature per villain (Riddler's riddle parameters, Two-Face's duplicates, Penguin's IP rotation, Poison Ivy's growing bodies). Five archetypes; tiered scoring exact / top-3 / archetype.
- **Kill chain:** Reconnaissance (narrated by Lex Luthor), Network Intrusion, Initial Exploit, Lateral Movement, Action, mapped to real MITRE ATT&CK IDs. Payload is a bat bot; the finale targets the villain's machine inside a persistent SIMULATION frame, never a real fullscreen takeover. A consent notice discloses that chat text is stored.
- **Technique catalog:** `transform/seeds/techniques.csv`, 23 techniques, weights summing to 1.0, observability 7 high / 7 partial / 9 low.
- **Observed/truth boundary:** features split into `int_session_features_observed` (`triage_input`) and `int_session_features_truth` (`ground_truth`). `assert_no_ground_truth_leakage` is a lineage test over the dbt manifest, since a column-name check is defeated by a rename. Recall is reported by observability tier, turning a weak number into a coverage-gap analysis.
- **Phasing:** local minimal UI → enhanced UI → optional cloud, which became Tracks A / B / C. `docs/07` (headless engine) split from `docs/08` (UI) so UI isn't built during Track A.
- **Claude Code setup:** `opusplan`; `/model opus` for the separability loop and the boundary split. Fable 5.1 skipped because its safeguards redirect cybersecurity-heavy content.

## Part 2 — Track A, Phases 0–5 (\~09-11 → 09-17)

Phases 0–5 built the headless pipeline end to end, finishing with a dbt layer that matched the Python harness exactly (`max_delta = 0`). Source: \[A\], as reported by Claude Code.

### Phase 0 — Scaffolding

- Seven spec conflicts (A–G) resolved. The important one: the JiveRepo root `.gitignore` had an unanchored `data/` rule that silently ignored `Batcave_IDS/data/`. Fixed with a local override, verified with `git check-ignore -v`; the shared root file was left alone.
- Sample data uses a `sample-*` filename at a real `dt=`/`hour=` path (a `sample/` directory would break Hive partition inference). A placeholder is committed so the `.gitignore` negation is proven continuously.
- Running `dev-reset` showed it would delete the committed sample; fixed.
- CI path-scoped to `Batcave_IDS/**`. `pre-commit` added. Rule set by Jong: no push until the local version works.

### Phase 1 — Honeypot and event contract

- FastAPI + Uvicorn, sharing Pydantic with envelope validation; API docs disabled; Kafka producer created once at startup.
- Gap-based sessions (continue while the gap is under 120 s). Invented headers `X-Client-Ts`, `X-Sim-Delay-Ms`, and `X-Forwarded-For` (trusted only when `HONEYPOT_TRUST_FORWARDED_FOR` is on).
- `attempt_id` added as nullable from day one. Result: zero field-level corrections against `docs/02`; 23 tests.

### Phase 2 — Technique catalog and stage machine

- All 23 ATT&CK IDs verified live. MITRE renamed TA0005 “Defense Evasion” to “Stealth” and added TA0112 “Defense Impairment”.
- Computed gates showed Killer Croc cannot pass stage 3 and Ra's al Ghul fails `privesc_exploit`'s power gate; the narrative changed, not the thresholds.
- New `produces_traffic` column: 8 low-tier techniques leave no HTTP evidence at all; `valid_accounts` leaves evidence that blends in.
- A missing healthcheck had hidden a crash-looping honeypot; the first healthcheck then polluted `attack.events`. Both fixed.

### Phase 3 — Villain behavior, separability, pathologies

- Session continuity moved to a honeypot-minted cookie plus gap logic, since IP and UA rotation split sessions.
- Separability harness written as SQL over DuckDB (liftable into dbt), with N-run averaging and effect size as the headline.
- Calibration: observed success 0.234 vs computed 0.418, because `detected` consumed about 48% of attempts. Fixed; gap 0.006, intelligence ↔ detection r = −0.957.
- **Joker 405:** non-standard HTTP methods were rejected without an event; a silent-drop audit was added. (Part of this defect survived; see Phase 8.)
- Request budgets fixed so volume spans 5–46 attempts. Two-knob timing: request budgets plus a `time_scale` idle-gap compression factor.
- Pathologies corrupt the observed stream only. `attack_run` became a fifth event kind, linked by `X-Run-Id`. `runs = 12`; all ten pathologies present across 144 sessions and 2,592 request events.

### Phase 4 — Consumer and landing

- Explicit pyarrow schemas (an all-null batch otherwise infers type `null`). Temp-file-then-rename writes; quarantine envelope; unknown drift fields landed as strings.
- Restart exercise: a 92-row batch with no committed offsets replayed to 184 rows, proving at-least-once.
- A stale LocalStack note the chat asked to remove never existed (confirmed by grep and `git log -S`). 137 tests.

### Phase 5 — dbt transformation layer

- Typed empty `stg_botchat_turns` and `stg_counterstrike_events` built now so the triage input contract holds across tracks. This dormant chain later carried four bugs (Phase 9).
- The harness SQL was split at the observed/truth boundary; lifting it unchanged would have leaked attempt-derived features.
- Bugs found: `is_valid_json` rejected 43 of 59 valid bodies; traversal and injection matchers overlapped on `/etc/passwd`; calibration needed a 3-sigma binomial band (gap 0.0038).
- 4 of 23 techniques were never reached, because selection always took the first row. Fix committed to Phase 6.
- Results: harness ↔ dbt `max_delta = 0` across 17 features × 144 sessions; reconciliation exact; the leakage test was shown to fail when broken on purpose. `requests_per_min` sat at its 600 cap for 132 of 144 sessions.

## Part 3 — Phase 6: scoring, triage and evaluation (09-17)

Phase 6 delivered threat scoring, a rule-based baseline and LLM triage; the baseline beat the LLM on attribution, and the most useful LLM behavior was calibrated uncertainty. Sources: \[CC1\] §3.1–3.6, \[A\] #22.

### Secrets first

- A live Groq key sat in plaintext in untracked, unignored `Batcave_IDS/api.txt`.
- `api.txt`, `*.key`, `*.pem`, `credentials*`, `secrets*`, `*token*` added to `.gitignore` (`79b9a0e`). History checked with `git log --all --full-history` and `git log -S`: never committed. Key moved to `.env`; rotation agreed.

### Part A — debts paid

- Technique selection randomized with `rng.shuffle` at stage entry; a many-seed coverage test fails without it (`537bc73`). 23 of 23 techniques now attempted.
- Corpus regenerated at `time_scale = 0.2` (0.1 is exactly the noise floor). Cap saturation fell from 132/144 to 71/156 sessions.
- Three `separability.py` bugs (`ecfef68`): an undeserializable-message crash (fixed in a sibling harness in Phase 3, never propagated); `time_scale` never passed through, so every corpus recorded 1.0; and a self-introduced **264-session contamination** from 120 real plus 144 unrelated sessions. Caught by an implausible count, not a test.
- Two-Face dbt test scoped to traffic-producing sessions, plus `assert_twoface_test_is_not_vacuous` (28/33 qualify) (`4d51ebc`).
- New ablation shows 0/66 load-bearing pairs for both `error_ratio` and `wasted_request_ratio`, but it's confounded (selection and clock changed together). Both features weighted at face value (`0cdc7ae`).

### Parts B–D — score, prompt, baseline, marts

- `mart_threat_scores` with eight weighted components (`f33f6a7`). Range 14.78–70.57; only 7 of 12 villains ever cross the threshold of 60 (Catwoman mean 33.2).
- sqlfluff's ST06 autofix moved columns away from their comments; handled with scoped `noqa`, never the autofix.
- Prompt v1 never exposes `observability` (`57022bb`). `request_body` added to `fct_attack_events` (`a501806`). Groq client with retry, fence stripping, one repair retry, and `parse_failed` instead of raising (`5fc9841`).
- Baseline: nearest-centroid in z-scored feature space plus hard discriminators (`d2091a8`). The Two-Face discriminator fired on 143 of 381 sessions (about 28 real), because Croc's retries look the same; dropped. Exact accuracy on the full corpus rose from 29.4% to 42.8%.
- 11 of the baseline's 15 archetype matches are automatic, since its archetype is looked up from its own villain guess.
- Evaluation marts `fct_intervention_orders`, `fct_triage_evaluations`, `fct_technique_evaluations`, `mart_detection_coverage` (`955104a`). Stratified sampling (`make triage-eval-sample`, 3 per villain) kept separate from the production threshold (`4c288a0`).

### The Groq model saga

| Attempt | Model | Outcome |
| --- | --- | --- |
| 1 | `llama-3.3-70b-versatile` | 404; no longer served |
| 2 | `openai/gpt-oss-20b` | JSON-validate failures; reasoning tokens exhausted the 200k/day cap; 0/36 valid parses |
| 3 | `qwen/qwen3.8-27b` | Completed; hit the 1,000 output-tokens/minute cap repeatedly |

- qwen on 36 sessions: exact 16.7%, top-3 36.1%, parse failures 27.8% (4 rate-limit, 6 genuine). Full comparison in [Reference](#reference).
- **Strongest finding:** qwen was right on Killer Croc in both sessions that got a real response, and answered `unknown` at 0.45 confidence on Ra's al Ghul, naming the missing signatures. The baseline was 0/3 on both.
- Write-up committed unedited with Jong's framing (`0f9f031`, `d0ce845`, `7a3031b`). One commit swept in more than its message described; left as is.

## Part 4 — Phase 7: orchestration, presentation and CI (09-17 → 09-21)

Phase 7 wired Dagster end to end with zero credentials, rewrote the README around decisions and limitations, and proved CI green through one authorized push. Sources: \[CC1\] §3.7–3.10, \[A\] #23–#24.

### Starting state

- `orchestration/` was empty; four Makefile targets (`reconcile`, `coverage`, `docs`, dbt half of `test`) were stubs, including one the README Quickstart advertised. Un-stubbed and verified (`2144294`).
- CLAUDE.md's CURRENT POSITION still said Phase 5 and claimed “19 reachable” techniques.

### Decisions

| Question | Decision |
| --- | --- |
| Where findings live | New `docs/09-engineering-log.md`, by finding; README points to it |
| Charts | Coverage chart as hand-written SVG; Jong captures Dagster and Redpanda screenshots |
| CI | Push a temporary `ci-verify` branch, fix until green, delete it (Jong overruled “verify locally”) |
| Asset names | Keep dbt model names; explain in docs/01 |

### Dagster layer (`4d63f9f`)

- dbt graph split as `dbt_upstream` → Python triage asset (`AssetKey(["triage","raw_triage_predictions"])`) → `dbt_evaluation`; selections verified disjoint and complete (4 + 31 = 35 nodes).
- DuckDB single-writer lock handled three ways: in-process executor, a pool of 1, and a run coordinator allowing 1 run.
- Fixes along the way: removed `from __future__ import annotations` (broke context typing); moved run limits to `concurrency.runs`; used `dagster job execute -j batcave_pipeline` because Windows globbed `--select '*'`; added `dagster-webserver` 1.13.22 as a pinned runtime dependency; gitignored `DAGSTER_HOME` state except `dagster.yaml`.
- Zero-credential run succeeded with `.env` moved aside (73 upstream nodes → baseline-only triage → 4 downstream models).
- **Contamination #1:** that run added 4 sessions to the pinned 36-session eval set; the coverage chart showed 87.3% instead of 89.7%. Caught because the number disagreed with the docs. Sessions removed, marts rebuilt.

### Presentation (`4b0fa0a`)

- Decision log and honest limitations placed before results; `generate_coverage_chart.py` + SVG; `docs/09` started with 11 findings; a second exercise in `docs/exercises.md`.
- Jong's draft said two techniques were structurally unmeasurable; only `deploy_batbot` could be substantiated, and Jong confirmed one.

### CI — the one authorized push

- The workflow never ran `dbt test`; a `dbt build` against the committed sample was added and proven in a clean `git worktree`: 231 pytest, dbt 77/77, leakage test 11/11 (`05ded2b`).
- Run 1 failed at setup: `astral-sh/setup-uv@v10` isn't a real tag; pinned `v10.1.0` (`f55b48f`).
- A force-push triggered nothing, because the `paths:` filter ignores workflow-only changes; added `workflow_dispatch` (`f4e5306`).
- Run `35288537225`: all three jobs green. `ci-verify` deleted remotely and locally.

### Screenshot prep (`1de4ef9`)

- The burst procedure was wrong: `TIME_SCALE=1` never outruns the 30 s flush. `TIME_SCALE=0.02` shows lag 0 → 322 → 0.
- docs/01's “evaluation” lineage band doesn't exist; real groups are `raw`, `staging`, `intermediate`, `marts` (18, including eval), `triage`, plus seed singletons.
- Track A closed at 102 commits.

### Git housekeeping (09-17)

- `end-of-file-fixer` failed commits, once with a stash conflict; re-staged, and `--no-verify` used for whitespace-only commits.
- The steady VS Code “Git” output is background polling, not an error. `.github/workflows/` lives at the monorepo root.

## Part 5 — Groq → Gemini swap and close-out (09-21)

Groq was removed entirely for `gemini-3.5-flash-lite`; schema-constrained output cut parse failures from 27.8% to 0%, but the model stopped saying `unknown` (0 of 36). Recorded as a trade, not a win. Sources: \[CC1\] §3.11–3.12, \[A\] #25–#26. Committed by Jong as `b0ce0e4`.

### Why and how

- **Trigger (Jong):** Groq fails behind his VPN, and its free-tier limits caused the earlier infra parse failures.
- **Decisions (Jong):** remove Groq; re-run the same 36 sessions; keep qwen as labeled history; show numbers before any doc changes.
- Doc summaries described `client.interactions.create()`; the installed `google-genai` 2.24.0 has no such attribute. Real call is `client.models.generate_content(...)`. Caught by inspecting the package before writing code.
- `http_options.timeout` is milliseconds. Retries catch both `APIError` and raw `httpx` errors, since a VPN drop has no HTTP response.
- `TriageOutput` Pydantic schema passed as `response_schema`. Villain slug and `attack_id` left unconstrained so hallucination stays measurable.
- `gemini-2.5-flash-lite` returned 404 (“no longer available to new users”). `thinking_budget=0` returned an opaque 400 on 3.5, isolated by testing each parameter alone; omitting `thinking_config` already gives zero thinking tokens.

### Findings

- Mean confidence 0.84 even on wrong answers; about 3,733 input / 423 output tokens and 1.8 s per call.
- The Croc / Ra's al Ghul result did not repeat: both 0/3, every Croc session called Bane.
- One Two-Face → Killer Croc miss, reasoned from auth failures rather than duplicate paths; recorded as related, not a third confirmation.
- Villain slugs missing their numeric prefix appeared under both models: a validation-layer property.
- `CLAUDE.md` turned out to be gitignored (with history from before that); edits stay local.

### Close-out and contamination #2

- `make coverage`, `make eval` and `dagster definitions validate` run via the literal targets; no `GROQ` references left in compose or CI.
- A zero-credential `make triage` added the same 4 sessions to the eval set (72 → 76 rows). Restored; numbers back to 66.0 / 5.6 / 89.7.
- **Root cause:** both selectors are correct; `raw_triage_predictions` has no run or snapshot ID, so any selector extends whatever the table holds. Logged in docs/09 with a pointer at `_select_sessions()`. This later became Phase 12.

### Track B handoff

- Start Track B in a new chat and Claude Code session; stay on `opusplan`, `/model sonnet` for straightforward assembly. Artifacts: `batcave-ids-track-a-handoff.md` and a kickoff prompt.

## Part 6 — Track B, Phase 8: terminal console and the gemini-3.1 swap (\~09-21 → 09-22)

Phase 8 added a human-driven console over the same stage machine, with events indistinguishable from headless runs and zero dbt model changes; the default model then moved to `gemini-3.1-flash-lite` for cost. Source: \[C1\] §1–3.

### Conflicts resolved before planning

| Conflict | Decision |
| --- | --- |
| docs/08 wanted stage-0 recon rows; no stage-0 techniques exist | Luthor's bullets are narrative only, citing ATT&CK IDs in prose |
| `parameters` is `{}` everywhere and read by nothing | Console records player choices there; no effect on probability |
| 120 s session gap breaks under human reading time | Gap raised to 900 s for the console stack via `SESSION_GAP_SECONDS` |
| Free retry/pivot vs `failure_tolerance` stalls | Player chooses within the envelope; UI shows remaining tolerance |

- `detected` neither advances nor counts as failure, so repeated detection loops toward `MAX_ATTEMPTS` (400). Phase 8 only shows a detected tally and attempt counter; whether `detected` should count against tolerance is deferred.
- A browser can't reach Kafka or own the honeypot cookie, so a console backend is mandatory; docs say so.

### `session_source` field

- New `AttackRunEvent.session_source` (`headless` | `console`), record-only, on the ground-truth side like `timing_compression_factor`. Separability consumers can exclude console runs while `int_session_features_observed` keeps them.
- Old sample rows read as `NULL` via `union_by_name=true`; staging coalesces to `headless`. No sample regeneration needed.

### Architecture

- `services/simulator/machine.py`: a resumable `StageMachine`; the headless runner becomes an autopilot driver over it. One implementation, two drivers.
- The RNG draw order had to survive the extraction; a characterization test was green before refactoring. A 12-villain × 5-seed before/after diff found zero mismatches after fixing one misplaced draw.
- `services/console/` FastAPI backend on port 8090 (host, `make console`); `console/` static SPA on 8091 (`make console-web`). A persistent SIMULATION frame; no `requestFullscreen` anywhere, enforced by grep.
- Verified live: a 168.8 s idle survived; a 21-field diff between console and headless event shapes was empty (re-run on Ra's al Ghul).

### Joker's absurd HTTP methods (pre-existing Track A bug)

- Joker draws from `BREW`, `PROPFIND`, `WHACK`, `MEOW`, `SMITE`. Uvicorn's parser rejects all but `PROPFIND` at the wire, before honeypot code runs.
- Impact: 82 of 416 expected request rows missing across 296 attempts (about 20%), present since Phase 3. Confirmed with a raw-socket test.
- Not fixed inline, and not filed as a known limitation. Scheduled as its own maintenance phase: verify replacements at the socket level, regenerate, keep before/after numbers.

### Model swap to gemini-3.1-flash-lite

- Motivation: cost. Pinned to the original 36 session IDs. 3.1 accepts `thinking_budget=0`.
- A real trade: exact 13.9% → 27.8% and archetype 30.6% → 44.4%, but technique precision 71.4% → 60.9%, high-tier recall 66.0% → 55.7%, and malformed slugs 2 → 6. Ra's al Ghul 1/3 (first hit under any model); Croc still 0/3.
- A 3-session corpus change shifted the baseline's centroid reference; `classify_techniques` was byte-identical, so the comparison was not contaminated.
- **Contamination #3:** the missing snapshot ID bit again mid-verification, caught by row counts.
- docs/04 gained “Why gemini-3.1-flash-lite is the default”; docs/06 gained a Maintenance section with Phase 12 (snapshot identity). Committed with Phase 8 as one commit; the pre-commit hook fixed two missing trailing newlines first.

### CI fix #1 — ST06 column order

- `sqlfluff` ST06 failed on `stg_attack_runs.sql`: the `session_source` coalesce sat among passthrough columns. Four-line reorder; dbt 79/79; distribution unchanged (console 2, headless 409). This reorder later caused the Phase 10 binder error.

## Part 7 — Track B, Phase 9: bat bot (09-22 → 09-23)

Phase 9 shipped the consented 3–5 turn bat bot, and its first real data exposed four dormant formula bugs in Phase 5 dbt code. Committed as `48e7b04` (2026-09-23 00:53). Sources: \[C1\] §4, \[CC2\] §1–2.

### Decisions (three plan rounds)

| Topic | Decision |
| --- | --- |
| Event grain | Two `chat_turn` rows per round (`speaker` bot / user) sharing `turn_number`; nulls mean “not applicable” |
| Architecture | Console backend publishes `chat_turn`; honeypot untouched |
| Trigger | `deploy_batbot` is an ordinary stage-4 option with no hint; checkpoints drive it deliberately |
| Signals | One deterministic, versioned keyword module: `engaged = (not refused) and bool(flags)` |
| Turn count | Rapport → 1–3 probes → reveal; engagement-driven; floor 3, hard cap 5 enforced in code |
| Zero credentials | Only `bot_text` is scripted; signals still come from real input |
| Sample | Every field copied raw except `user_text`, nulled in the copy query |
| Test | `assert_no_user_text_in_sample.sql` upgraded in place to read the committed Parquet |

### Build

- `services/console/batbot.py`, `prompts/v1/batbot_system.md`, 15 deterministic tests (cap on “always engaged”, floor on “always refused”, `engaged` truth table).
- `/batbot/start` and `/batbot/reply` endpoints; a `deploy_batbot` success now sets `batbot_pending` instead of ending the run. Non-dismissible consent panel and chat screen.
- `user_text` is selected in exactly one model, `stg_botchat_turns.sql`. It persists in the local, gitignored warehouse but never reaches a mart or the committed sample. docs/02 now states that scope precisely.

### Bugs found on real data

| # | Bug | Fix / evidence |
| --- | --- | --- |
| 1 | Stale consumer image landed `chat_turn` as drift strings | `docker compose build consumer` |
| 2 | `chat_turns_completed` counted rows | `count(distinct turn_number)`: 7 → 4 |
| 3 | `probe_engagement_ratio`: NULL fell into the CASE `ELSE`, counting bot rows | Filter to user rows: 1.0 → 0.667 |
| 4 | `probe_engagement_ratio` ignored the `engaged` rule | SQL mirrors the Python predicate, with a cross-reference comment |
| 5 | `intent_flags_triggered` counted rows, right by coincidence | `sum(json_array_length(...))` |
| 6 | `choose_sessions()` ignored chat coverage | New highest-priority sort key |
| 7 | `stg_botchat_turns` had no dedup | Existing `row_number()` pattern plus tiebreak `(user_text is null) asc`; 21 → 14 rows |

- The dedup is a missing instance of the Phase 4/5 pattern, not a new one. `chat_turn` needs the third tiebreak because it's the first kind with an excluded column, so its sample copy isn't byte-identical.
- Adding `engaged` as a landed column was rejected as scope creep; the duplication is accepted and noted in docs/09.

### `bot_text` echo risk

- `user_text` redaction is airtight, but `bot_text` can repeat what the player typed into the public sample.
- Mitigated three ways: a consent-notice sentence explaining why, a docs/02 scope note, and a hand-check of `bot_text` on every sample regeneration. A scrubber was ruled out.

### Result

- 260 pytest, dbt 79/79 re-confirmed after the dedup fix; checks re-run: `user_text` on one model only; 0 non-null values in 7 sample rows. Verified on both the Gemini and zero-credential paths.
- Commit first failed `ruff-check` on import order in `test_consumer_schemas.py`; fixed and re-committed.

## Part 8 — Track B, Phase 10: counterstrike finale and Batanalytics dashboard (09-23)

Phase 10 closed Track B: a finale that accuses whoever the triage model names (verified with a real wrong accusation) and a seven-panel Streamlit dashboard. Committed as `5fc91b1` (2026-09-23 19:20). Sources: \[C1\] §5, \[CC2\] §3–12.

### Top finding: orphaned triage predictions (contamination #4)

- `raw_triage_predictions` held 72 orders / 36 sessions; 16 orders (8 sessions) were orphans absent from `fct_attack_runs`, silently dropped by an inner join.
- **Published numbers were correct as measured:** predictions were written 09-22 13:40–13:41; the orphaning commit landed about 9 hours later. They are no longer reproducible: `make eval` now reports n=28.
- **Root cause (confirmed with `git log --diff-filter=D`):** `48e7b04` deleted the `dt=2026-09-16` sample files during regeneration, and the sample was the only surviving copy of those sessions. Phase 9's new sort key makes recurrence more likely.
- Landed now: a docs/09 entry, a docs/04 reproducibility note, and Phase 12 bumped to “due, not backlog”. Underlying fix still in Phase 12.

| Source | n | exact | top-3 | archetype |
| --- | --- | --- | --- | --- |
| baseline, published | 36 | 36.1% | 77.8% | 44.4% |
| baseline, today | 28 | 32.1% | 78.6% | 42.9% |
| llm 3.1, published | 36 | 27.8% | 50.0% | 44.4% |
| llm 3.1, today | 28 | 32.1% | 50.0% | 50.0% |

### Decisions

| Topic | Decision |
| --- | --- |
| Finale data path | Async pre-warm with an in-fiction “analyzing” beat; bounded stages and a real failed state. In-process feature computation rejected as two sources of truth |
| Console predictions | Stored normally; `session_source = 'headless'` filter at the eval boundary, with printed total / excluded / dropped counts |
| Threshold | Console sessions (max score 50.16, threshold 60) get a single-session triage path; `triage_console_session` refuses headless sessions |
| Event grain | One counterstrike event per readout line; attribution on the SUSPECT line only (Jong overrode the agent) |
| Remediation | `mitigations.csv` seed, 23 techniques only |
| Ground-truth panels | `retry_ratio` / `pivot_ratio` labeled “(ground truth)” in the UI itself |

### Build

- `console_triage.py`, `counterstrike.py` (11-line script, 4-line degraded script; `T1561.002` wiper, `T1529` shutdown), and `finale.py` states pending → ingesting → transforming → scoring → attributing → ready | failed.
- Fixes during build: tests spawned real pipelines (new `finale_runner` seam); `read_parquet` raises on an empty glob (`glob()` first); status endpoint treated dicts as objects; `stg_counterstrike_events` dedup (no third tiebreak needed).
- Mitigation seed from each ATT&CK page; four techniques genuinely have none (T1056, T1113, T1123, T1125). `mart_remediation` tagged `ground_truth`. dbt 82/82.
- Dashboard: Streamlit 1.64.0, 7 panels, palette validated; `AppTest` ran clean; pytest 281.
- `counterstrike` was missing from `sample_partition.py`'s `EVENT_KINDS`; added with a sort key. Regenerated sample: 377 rows including 11 counterstrike; dedup 44 → 33.

### DuckDB concurrency — an assumption disproved

- The docs said a read-only dashboard connection “briefly blocks” behind a writer. A cross-process test on Windows showed an immediate `IOException` instead.
- Decision (Jong): bounded retry on that exception only (5 attempts, 0.5 s backoff), then “warehouse busy”. A lock file was rejected as disproportionate. Docs 01, 05 and 08 corrected.
- Verified both orderings: reads recovered in 1.27 s and 1.25 s; a 10 s writer produced a clean failure at 2.59 s.

### Non-deterministic CI binder error

- `Binder Error: Column "session_source" ... cannot be referenced before it is defined` in CI, not reproducible locally.
- Identical SQL passed in a later run with identical versions (`dbt=1.12.4`, `duckdb=1.11.0`), so version drift is ruled out. The ST06 reorder had created the fragile forward-alias pattern.
- Structural fix: `session_source` computed in a `typed` CTE, passed through by the outer SELECT. dbt 82/82; distribution unchanged (console 13, headless 397); sqlfluff clean. Exact binder mechanism not pinned down.

### Verification (re-run at Jong's request, with fresh evidence)

- Real wrong accusation: Poison Ivy played, Ra's al Ghul accused at 0.9.
- Bare `make triage` selected exactly the 17 sessions above threshold, 0 console.
- Counterstrike field placement: four violation queries over 44 rows, all 0.
- Eval boundary printed `76 / 4 / 16`, matched by hand; excluded and orphaned sets disjoint.
- All 7 panels showed real content; charts checked by asserting on their exact queries, since `AppTest` can't read Altair data.
- Three failure paths forced live (no landing, broken dbt, missing key falls back to baseline); all degrade cleanly.

### Latency and tooling

- Finale latency cut from about 40–50 s to 10.1 s. Measured first: the 30 s consumer flush dominated. `make console` now sets `CONSUMER_FLUSH_INTERVAL_S=3`; `finale.py` runs `dbt run` on a 17-model slice; polls cut to 1 s.
- `make dashboard` target added (port 8501).
- Jong's manual pass: ports 8090 and 8501 were held by earlier verification instances (identified with `Get-CimInstance`), and the SPA is on 8091. A stage-4 clear without the bat bot is expected; only `deploy_batbot` delivers it.
- The frontend was verified through the HTTP API, not in a browser; the commit message says so.
- Commit hit `ruff-format` (8 files), reviewed and re-staged. A `git add -A` during that fix swept in changes from another project in the monorepo (see open items).

## Part 9 — Track C re-plan: GCP and kind (09-25)

Track C is now a single GCP path funded by the $300 / 90-day trial, integrated first on kind, living as a subfolder of `Batcave_IDS`. Committed as `b439944` (2026-09-25 20:25). Sources: \[CC2\] §13, \[C1\] §6, \[A\] #27.

### How the plan evolved

1. **Original:** AWS (Lambda → Firehose → S3 → Glue → Athena), then Kubernetes moved to a separate `k8s-data-platform` repo with free-control-plane options (DigitalOcean, Vultr, Linode). \[A\]
2. **First GCP revision:** zonal Standard GKE, Spot VMs only for Airflow task pods, Workload Identity for logs, Gateway API instead of retired ingress-nginx, DNS-based control-plane endpoint (IP allow-lists break on VPN changes), `make verify-destroyed`, and an AWS equivalents table. \[A\]
3. **Final re-plan (09-25):** the decisions below. \[CC2\]

### Decisions

| Topic | Decision |
| --- | --- |
| Cloud | GCP only; kind locally; trial treated as a hard ceiling |
| Orchestrator | Airflow, KubernetesExecutor, thin DAG of `KubernetesPodOperator` tasks running the batcave-ids image; Dagster stays local |
| Data plane | Same stack; storage in Cloud Storage via the FUSE CSI driver and Workload Identity; no Pub/Sub or BigQuery |
| Scope | Lean core; Gateway API with domain/TLS and removing Cloud NAT are stretches; UI via port-forward |
| Code home | Subfolder of `Batcave_IDS` (same code, no version skew); one-way dependency |
| Phasing (agent, flagged) | C0–C6, integration on kind (C2) before any spend; system pool e2-standard-4, to be measured on kind |

### Verified trial facts (free-program page, updated 2026-09-24)

- $300 over 90 days, then resources stop, then a 30-day grace period. No quota increases, GPUs, Marketplace or Windows VMs.
- The credit can't pay for Gemini API usage in AI Studio.
- No fixed vCPU cap is listed any more; the region's CPUS quota binds. (This supersedes the “8-vCPU” figure in \[A\].)
- Always Free covers one zonal or Autopilot GKE cluster's management fee, 5 GB-months Cloud Storage, 0.5 GB Artifact Registry, 2,500 Cloud Build minutes, and more.
- The GKE pricing page couldn't be fetched, so the $0.10/hr fee and $74.40/month credit remain unverified.
- Corpus: `data/raw` 8.1 MB, `warehouse.duckdb` 118 MB, well inside the free allowance.

### Gotchas handed to the next agent

1. The consumer's `.tmp` + `os.replace` write isn't atomic over Cloud Storage FUSE on a flat-namespace bucket.
2. Never open DuckDB read-write over FUSE.
3. `raw_triage_predictions` can't be rebuilt; persist it on a PVC and copy it to the bucket; run warehouse tasks sequentially.
4. `uv sync --no-dev` pulls in Dagster and Streamlit, bloating every image.
5. Keep the 30 s flush in the cloud; Class A operations are the allowance a workload can exhaust.

### Changes made

- `k8s-data-platform/HANDOFF.md` rewritten: read order, decisions, reuse table, gotchas, architecture, FinOps Terraform defaults (zonal, 30 GB `pd-standard`, `deletion_protection = false`, managed Prometheus off, us-central1), teardown discipline, AWS mapping, verify-later list.
- `CLAUDE.md` wrongly said `k8s-data-platform/` had moved to the JiveRepo root; it lives at `Batcave_IDS/k8s-data-platform/`. Corrected.
- `.gitignore` narrowed from the whole folder to `HANDOFF.md` only, so deployment files will be tracked.
- docs/06 Track C rewritten (50–70 hours; all-track total 120–165); docs/01, 05, README and root `ARCHITECTURE.md` reframed as an optional layer.
- &#91;C1\] also produced a 7-step prerequisites checklist: create the project and link billing, enable APIs, install gcloud + kubectl + `gke-gcloud-auth-plugin`, hand-create a versioned GCS state bucket, a scoped Terraform service account (prefer Workload Identity Federation), a budget alert before the first apply, and record the trial expiry date somewhere durable.

## Part 10 — The bat bot becomes the payload for any cleared run (10-08)

Clearing stage 4 by any technique now delivers the bat bot, instead of ending on a bare “Run complete: CLEARED.” Verified live. A separate concurrency bug in the finale surfaced during the live run and was fixed the same day. Uncommitted as of this entry. Source: \[CC2\] (resumed 10-08).

### Why

- **Jong's request:** the bat bot should be the payload for a successful attack. “Cleared” on its own is a weak reward for clearing the kill chain.
- Until now, only a `deploy_batbot` success at stage 4 delivered it (Phase 9 decision 3; Part 8's manual pass noted this as expected). That limited it to villains whose intelligence clears the technique's gate (`min_intelligence` 40), and only when the player happened to pick it.

### Decisions

| Topic | Decision |
| --- | --- |
| Trigger | Any stage-4 success sets `batbot_pending`. Consent, conversation, reveal and finale unchanged. A stalled run gets no payload. |
| `deploy_batbot` technique | Kept unchanged, as an ordinary stage-4 technique (T1071). Jong first chose to remove it, then reversed once its reach was measured. |
| Workflow | Plan stated before any code change, at Jong's request. |

**Why removal was rejected:** it's one of the 23 catalog techniques. The corpus holds 426 attempts of it across 67 runs (76 successes, 19 rows in the committed sample), and `stg_attack_attempts`' `relationships` test to `stg_techniques` would fail on every one. Removing it would also mean regenerating the corpus, invalidating the published docs/04 numbers, and changing every “23” in the docs.

### Build

- One branch in `services/console/app.py`'s `/attempt`: the `technique_id == "deploy_batbot"` check removed, so any stage-4 success sets `batbot_pending`.
- Comments in `app.py`, `state.py`, `batbot.py` and `console/app.js` no longer name `deploy_batbot` as the trigger. `docs/08` describes the bat bot as the payload for a cleared run and says why `deploy_batbot` stays. `docs/09` has entries for both this change and the concurrency finding below.
- Tests: the stage-4 helper clears stage 4 with any technique **except** `deploy_batbot`, so `test_clearing_stage_four_delivers_the_bat_bot_instead_of_finishing` proves the new route rather than the old one. The bat bot tests passed 10 out of 10 repeated runs against the unseeded RNG. pytest 281.

### Live verification

1. **First run hit stale code.** A Ra's al Ghul session cleared stage 4 with `exfil_over_c2` and finished as “cleared” with no bat bot: the old behavior. Port 8090 was held by a console backend started during the 09-23 verification (PID 36440), still serving pre-change code. Restarting Docker doesn't touch it, since the console runs on the host. Stopped and restarted on current code.
2. **The payload worked; the finale failed.** Poison Ivy cleared stage 4 with `screen_capture`; the bat bot appeared and ran five turns to reveal. The finale then failed with `dbt run failed (exit 2):` and an empty reason, rendering the degraded “ATTRIBUTION INCONCLUSIVE” readout. A second run (`audio_capture`) failed identically.
3. **Clean run.** With finales serialized by the test script, Ra's al Ghul cleared stage 4 with `input_capture`; bat bot through reveal; finale ready in 11.0 s (ingesting 5.0 s). **The baseline accused Scarecrow at 0.9**: another real wrong accusation.

### Concurrent finales collide (found during live verification)

- **Symptom:** `dbt run` exit 2 from the finale, while the identical command succeeded from a shell and from a `uv run python` process launched the same way as the console. Neither failing run appeared in `transform/logs/dbt.log`.
- **Diagnosis:** every finished session starts its own finale, stalled ones included. In both failing runs, a stalled session finished seconds earlier. Its finale reached **ready**; the cleared session's finale, started while the first was still in its dbt run, **failed**. With finales serialized, the cleared session's finale succeeded. Two `dbt` processes on the single-writer DuckDB file (and, likely, the same `dbt.log` on Windows) can't run at once.
- **Not caused by the payload change.** It's latent in Phase 10's design: finale pipelines aren't serialized across sessions. Phase 10's verification ran sessions minutes apart, so it never overlapped. A human who stalls and immediately plays again within about 10 s would hit it.
- **Second gap:** `finale.py` keeps only stderr in the failure reason, but dbt writes its errors to stdout, so the reason was empty and the real error was lost.
### Fix (10-08, at Jong's go-ahead)

- **Lock:** a process-wide `threading.Lock` in `finale.py` around the warehouse phases (dbt run, scoring, triage write). Finales are threads in one console process, so an in-process lock is enough. A queued finale shows TRANSFORMING... while it waits; the wait is bounded at 120 s, then it fails as “warehouse busy”.
- **Landing check off the warehouse:** it uses an in-memory DuckDB connection. It only reads raw Parquet, and opening the warehouse read-write every second was a second collision path.
- **Failure reason:** includes dbt's stdout, with ANSI color codes stripped.
- **Tests:** three new tests in `tests/test_console_finale.py`: maximum concurrency of 1 across two finales, a bounded wait, and stdout in the reason. With the lock swapped for a no-op, the concurrency test fails (`assert 2 == 1`), so it isn't vacuous. pytest 284.
- **Live, harsher than the original failure:** four sessions finished within 1.2 s (two stalled, two cleared through the bat bot), and all four finales hit the transform step together. All four reached **ready**, serialized at 13.1, 19.4, 25.5 and 32.0 s.
- **Not covered:** another *process* holding the warehouse when a finale's dbt run starts, such as the dashboard mid-query or a hand-run `make transform`. The window is short, and the failure would now carry dbt's real error.
- **Tooling note:** escape sequences in ad-hoc patch scripts were mangled by the shell layer. A regex escape was written as a raw ESC byte, and a test string's newline escape became a real line break. Both were caught and fixed, now by building those characters from byte values instead.

## Reference

### LLM results history (same 36 pinned sessions, as published)

| Metric | Baseline | qwen3.8-27b (Groq) | gemini-3.5-flash-lite | gemini-3.1-flash-lite (default) |
| --- | --- | --- | --- | --- |
| Exact | 30.6% → 36.1%\* | 16.7% | 13.9% | 27.8% |
| Top-3 | 77.8% | 36.1% | 41.7% | 50.0% |
| Archetype | 41.7% → 44.4%\* | 36.1% | 30.6% | 44.4% |
| Parse failures | 0.0% | 27.8% | 0.0% | — |
| Technique precision / recall / F1 | 65.4 / 39.7 / 0.49 | 57.0 / 20.5 / 0.30 | 71.4 / 29.7 / 0.42 | 60.9 / — / — |
| High-tier recall | 89.7% | 44.3% | 66.0% | 55.7% |
| `unknown` answers | n/a | 8/36 | 0/36 | — |
| Malformed slugs | n/a | 1 | 2 | 6 |

\*Baseline moved between the 3.5 and 3.1 measurements because 3 new sessions shifted its centroid reference. Partial-tier recall is 0% for every source. Since Phase 10, `make eval` reports n=28 against the current warehouse.

### Commit map (current hashes)

| Hash | Date | Subject |
| --- | --- | --- |
| `b439944` | 09-25 | Docs: Track C moved from AWS to GCP and kind |
| `5fc91b1` | 09-23 | Phase 10: counterstrike, dashboard, orphan-row finding |
| `48e7b04` | 09-23 | Phase 9: bat bot and dormant-model fixes |
| `53be19a` | \~09-22 | Claimed dbt compatibility fix; ST06-era `stg_attack_runs` |
| `b0ce0e4` | 09-21 | Model change qwen → gemini-3.5-flash-lite |
| `1de4ef9` | 09-21 | Correct burst instructions and lineage group claim |
| `f4e5306` | 09-21 | CI: `workflow_dispatch`; CLAUDE.md Phase 7 progress |
| `f55b48f` | 09-21 | CI: pin `setup-uv` to `v10.1.0` |
| `05ded2b` | 09-21 | CI: `dbt build` against the sample partition |
| `4b0fa0a` | 09-17+ | README presentation, results chart, engineering log |
| `4d63f9f` | 09-17+ | Dagster orchestration, zero-credential verified |
| `2144294` | 09-17+ | Un-stub four Makefile targets |
| `7a3031b` | 09-17 | Phase 6 checkpoint: LLM vs baseline results |
| `d0ce845` | 09-17 | Two-Face / Killer Croc recurring confusion |
| `0f9f031` | 09-17 | Pin working Groq model |
| `4c288a0` | 09-17 | Stratified sampling for evaluation |
| `955104a` | 09-17 | Evaluation pipeline and marts |
| `d2091a8` | 09-17 | Rule-based baseline |
| `5fc9841` | 09-17 | Groq client, parsing, repair retry |
| `a501806` | 09-17 | Session context assembly |
| `57022bb` | 09-17 | Prompt v1 |
| `f33f6a7` | 09-17 | `mart_threat_scores` |
| `0cdc7ae` | 09-17 | Audit follow-ons |
| `4d51ebc` | 09-17 | Scope Two-Face duplicates test |
| `ecfef68` | 09-17 | Fix `separability.py` |
| `537bc73` | 09-17 | Randomize technique selection |
| `79b9a0e` | 09-17 | Cover `api.txt` and secret shapes in `.gitignore` |

Hashes for Phases 0–5, the Phase 8 + 3.1 swap commit, and the ST06 reorder are not recorded in the sources. `8038757` (later docs/09 touch) is mentioned without detail.

### Test and build counts

| Point | pytest | dbt build |
| --- | --- | --- |
| Phase 1 | 23 | — |
| Phase 4 | 137 | — |
| Phase 7 CI | 231 | 77/77 |
| Phase 9 | 260 | 79/79 |
| Phase 10 | 281 | 82/82 |
| 10-08 changes | 284 | not re-run; no model changed (the finale's 13-model slice ran clean live) |

### Final stack

| Layer | Technology |
| --- | --- |
| Honeypot | FastAPI + Uvicorn |
| Streaming | Redpanda `v26.2.2` (Kafka API), confluent-kafka |
| Landing | pyarrow Parquet, Hive partitions, at-least-once |
| Warehouse | DuckDB |
| Transform | dbt-core 1.12.4 + dbt-duckdb, sqlfluff-templater-dbt |
| Orchestration | Dagster 1.13.22 + dagster-dbt + dagster-webserver |
| LLM | google-genai, `gemini-3.1-flash-lite` |
| Console / UI | FastAPI backend (8090), vanilla JS SPA (8091), Streamlit 1.64.0 + Altair dashboard (8501) |
| Tooling | uv, ruff, pytest (+ `AppTest`), pre-commit, GitHub Actions |
| Reference data | akabab/superhero-api (12 villains), MITRE ATT&CK (23 techniques, mitigations seed) |
| Planned (Track C) | Terraform, Helm, kind, GKE Standard (zonal), Airflow, Cloud Storage FUSE, Workload Identity, Artifact Registry |

### Recurring patterns

- **Implausible numbers caught what tests missed:** 264 sessions, 43/59 “invalid” JSON bodies, 11/15 automatic archetype matches, an 87.3% chart value, `intent_flags_triggered` reading 6 instead of 3.
- **Fixes that didn't reach parallel code:** undeserializable handling (Phase 3 → Phase 6); dedup missing on each new staging model (`stg_botchat_turns`, then `stg_counterstrike_events`); each new event kind missing from `sample_partition.py`.
- **No snapshot identity on `raw_triage_predictions`:** four bites (Dagster check, `make triage`, 3.1 verification, sample-regeneration orphans). Now Phase 12, due.
- **Two-Face / Killer Croc confusion:** dbt test scoping, the baseline discriminator, a partial LLM case under qwen, and again under Gemini.
- **Docs and prose wrong until measured:** gating narrative, Ra's pivot claim, Croc volume, “two” unmeasurable techniques, the burst procedure, the lineage band, DuckDB “briefly blocks”, the `interactions` API.
- **Dormant code breaks on first real data:** four chat-feature bugs in Phase 5 models, first exercised in Phase 9.
- **Stale long-running processes served old code:** ports 8090 and 8501 held by verification instances in Part 8's manual pass, and again on 10-08, when a 15-day-old console backend made a live run show pre-change behavior.
- **Concurrency assumptions fail on the single-writer warehouse:** the dashboard “briefly blocks” claim (Part 8), then two finales running `dbt` at once (Part 10).

## Reconciliation notes

Eight places where the four sources disagree, with how this log treats each.

| Topic | What the sources say | Treatment here |
| --- | --- | --- |
| Dashboard `make` target | \[C1\] lists it as missing and open; \[CC2\] §11 adds `make dashboard` before the Phase 10 commit | Closed |
| gpt-oss “0/72” | \[A\], docs/04 and README say 0 of 72; \[CC1\] §8 says run 1 was the llama 404 and gpt-oss had one run of 36 | Corrected to 0/36; docs/04 and README still need the fix |
| Baseline published numbers | \[A\]/\[CC1\] give 30.6 / 77.8 / 41.7; \[C1\]/\[CC2\] cite 36.1 / 77.8 / 44.4 | Both kept; the second reflects a centroid shift from 3 added sessions |
| Trial vCPU limit | \[A\] says commonly 8 vCPU; \[CC2\] verified no fixed cap, regional CPUS quota binds | \[CC2\] supersedes |
| `k8s-data-platform` location | \[A\] says moved to a sibling location; `CLAUDE.md` said JiveRepo root; \[CC2\] found it at `Batcave_IDS/k8s-data-platform/` | Now a `Batcave_IDS` subfolder by decision |
| `CLAUDE.md` in git | \[A\] advised against ignoring it (09-17); \[CC1\] found it gitignored (09-21) | Open: confirm intended state |
| Count of contamination events | \[A\] says three; \[C1\] says the 3.1 swap was the third and the orphan finding the fourth | Four, as listed in Recurring patterns; the 264-session separability bug is a separate class |
| `53be19a` purpose | Its message claims a deprecated-syntax dbt fix; \[C1\] found the actual error was a forward-alias binder issue | Treated as not addressing the root cause; the CTE split did |

## Open items as of 2026-10-08

The most urgent items are the possible cross-project commit, key rotation, and Phase 12; everything else is scheduled or watch-only.

### Do soon

- [ ] **Cross-project commit:** a `git add -A` during the Phase 10 ruff-format fix swept another project's changes into the commit. Check `git show --stat 5fc91b1`; if unpushed, `git reset --mixed HEAD~1` and re-stage only `Batcave_IDS/`; if pushed, `git revert`.
- [ ] **Rotate keys:** the Gemini key appeared in plaintext in a session transcript; revoke the unused Groq key.
- [ ] **Phase 12 — `raw_triage_predictions` snapshot identity:** due after four bites; `make eval` reports n=28 vs the published 36.
- [ ] **Browser check of the finale:** watch the SIMULATION frame hold and the readout render in a real browser; not yet confirmed.
- [x] **Serialize finale pipelines (10-08):** fixed the same day with a process-wide lock, an in-memory landing check, and dbt stdout in the failure reason; verified live with four overlapping finales.
- [ ] **Commit the 10-08 changes:** the bat bot payload change (`services/console/{app,state,batbot}.py`, `console/app.js`, `tests/test_console_api.py`) and the finale fix (`services/console/finale.py`, `tests/test_console_finale.py`), plus docs/08, docs/09 and this log.

### Scheduled or small

- [ ] **Joker HTTP-method maintenance phase:** replace four rejected methods (socket-verified), regenerate the corpus, keep before/after numbers in docs/09.
- [ ] Fix the “0/72” wording in docs/04 and README to 0/36 for gpt-oss-20b.
- [ ] Confirm whether `CLAUDE.md` should be tracked or gitignored.
- [ ] Slug-format validation (regex or enum on `suspected_villain`) to close the recurring missing-prefix hallucination.
- [ ] Slim images with uv dependency groups so Dagster and Streamlit stay out of pipeline images.

### Watch or decide later

- [ ] Calibrated uncertainty under Gemini: is its loss due to constrained decoding, the model, or the prompt?
- [ ] Whether `detected` outcomes should count against `failure_tolerance`.
- [ ] Python `extract()` vs its SQL mirror in `int_session_features_observed.sql`; act only if they diverge.
- [ ] The AWS gap is covered only by the GCP → AWS equivalents table.

### Track C

- [ ] Not started; handed off through `k8s-data-platform/HANDOFF.md`.
- [ ] Verify before spending: GKE fee and $74.40 credit, FUSE rename semantics and whether hierarchical-namespace buckets qualify for the free tier, GKE and provider defaults, Airflow 3 chart details, the trial's CPUS quota.
- [ ] Decide whether the cloud deployment stays live or is a one-time documented run, and record the trial expiry date.
