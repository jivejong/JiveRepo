# Engineering log

Findings from building this pipeline — bugs, diagnoses, and tuning decisions that changed how a
model or a measurement worked. Organized by finding, not by phase: each entry is what broke, how it
was caught, and what changed. The middle part is usually the more interesting one. Several of these
were caught by an implausible number rather than by a failing test, which is worth reading as its own
pattern — a test only fails when someone thought to write it; a number that doesn't add up will flag
things nobody anticipated.

Full detail and the real output live in the commit each entry cites; this page is the index a reader
would actually want.

---

## The request budget was silently capped at 7-12 attempts regardless of villain stats

**What broke:** `request_count` didn't track speed at all. Mister Freeze and Riddler, both speed 12,
produced 12.5 vs 4.7 requests/min — because the run loop terminated on stage *resolution* (a handful
of attempts), never on a duration. Speed only changed the sleep between requests, never how many
requests got made. The entire feature set was being computed over 7-12 requests no matter what a
villain's stats said.

**How it was caught:** two same-speed villains producing wildly different request counts, which
shouldn't be possible if speed drives volume — a direct contradiction of docs/03's own model, not a
crash or a failed assertion.

**What changed:** implemented docs/03's actual model — a request budget of
`requests_per_min × session_duration_s / 60`. Once the stage machine resolves, a villain that hasn't
spent its budget keeps generating traffic from where it stopped (Croc keeps grinding his stall point,
Freeze keeps holding at the objective); clean operators (Ra's al Ghul, Catwoman) stop early by design,
which became a signature property rather than something derivable from stats alone. Volume went from
7-12 attempts (1.5x spread) to 5-44 (~9x, matching the real 4x speed×durability spread) — and this
also fixed Croc's "highest raw request count" claim in docs/03, which had been false under the capped
loop and became true once the computation was correct. Small-sample spread had also been inflating
every effect-size denominator in the separability harness, so this unblocked that measurement too.
(`2cf615d`)

---

## `is_valid_json` marked 43 of 59 legitimately-invalid-body requests as invalid — and also 43 of 59 *valid* ones

**What broke:** a bare `json_valid()` check judged every request body, including ones from techniques
that were never supposed to send JSON at all (a raw form post, an empty body). It marked 43 of 59
requests invalid in a corpus with **zero actually-malformed bodies** — the check wasn't wrong about
syntax, it was answering the wrong question.

**How it was caught:** cross-checking the invalid-body count against the corpus's known pathology
rate (docs/03 says roughly 2% of bodies should be malformed) and finding the real number an order of
magnitude higher than the pathology alone could explain.

**What changed:** the check now judges only JSON-*shaped* bodies — content that was supposed to be
JSON and failed to parse — rather than flagging every non-JSON body as a data-quality problem. A
request whose technique never sends JSON isn't invalid; it's just not JSON.

---

## The traversal and injection technique matchers overlapped on one payload

**What broke:** the technique-evidence matchers in `transform/` are pinned to the literal payloads
`services/simulator/traffic.py` emits and tested against them by import rather than by copied string
(so drift between the simulator and the test is structurally impossible). That test caught a real
overlap: `/etc/passwd` appeared in the injection-technique payload set, so a pure path-traversal
session also matched the injection matcher — which would have fed Phase 6 injection evidence for a
session that only ever performed traversal.

**How it was caught:** a test built to prevent drift between simulator and matcher, not a test built
to catch this specific bug — it caught the overlap as a side effect of testing something else
entirely.

**What changed:** removed sensitive file targets (`/etc/passwd` and its siblings) from the injection
payload set. Sensitive file targets are traversal *targets*, not injection *syntax* — the two
technique's evidence should never have shared a token in the first place. (`d7d7f6a`)

---

## A corpus mixing bug produced six-figure-millisecond timing values that would have read as real findings

**What broke:** while re-running the separability harness for Phase 6, fixing an unrelated crash (a
tolerant-parse fix for the undeserializable pathology) required deriving villain identity from
`attack_run` ground-truth events on the Kafka topic instead of an in-memory mapping. The first version
of that fix matched **every** `attack_run` event on the topic with no filter. Result: 264 sessions
from what should have been a 120-session run. It had silently merged in an unrelated 144-session
pathology corpus sitting on the same retained topic (120 + 144 = 264, exactly) — and that corpus's
clock-skew and out-of-order pathologies produced six-figure-millisecond `inter_request_stddev_ms`
values that would have been written up as genuine findings about villain pacing.

**How it was caught:** the session count was implausible — 264 where 120 was expected — not a test
failure. Nothing about the query itself was wrong; the *scope* of what it read was.

**What changed:** the mapping now requires `pathologies_enabled == []` (this harness never injects
pathologies itself) and, when timing-scoped, a matching `timing_compression_factor`. Two regression
tests cover the exact 264-session scenario and the individual filters, using synthetic `attack_run`
dicts so no broker is needed to prove it. The same investigation also found a second, older bug in the
same file: `timing_compression_factor` had been recorded as `1.0` on every separability-generated
`attack_run` since Phase 3, regardless of the real `--time-scale` used — a self-describing-corpus field
that had never actually described itself for this one producer. Neither bug corrupted a historical
number (the real per-request pacing was always correct; only the *label* on one producer's ground
truth was wrong), but both are exactly the kind of drift a recorded-but-unverified field invites.
See also `docs/05-local-environment.md`'s Timing model section for the full trace. (`918070d`)

---

## Two-Face and Killer Croc are confusable on the same feature, twice, in two unrelated places

**What broke:** `exact_duplicate_path_pairs` — Two-Face's documented signature (he issues every
request exactly twice) — can't distinguish deliberate doubling from Killer Croc's retry-driven
grinding. Their real-corpus means differ by a few hundredths (2.21 vs 2.12). First surfaced as a
flaky-looking dbt test (`assert_twoface_duplicates_survive`, which needed scoping to sessions with
actual traffic once technique selection stopped guaranteeing it); resurfaced independently in Phase 6
as the rule-based baseline's Two-Face hard discriminator firing on 143 of 381 real sessions when only
~28 are actually his.

**How it was caught:** the dbt test instance surfaced through an unexpectedly low pass rate once
random technique selection replaced deterministic ordering; the baseline instance surfaced through
inspecting the classifier's raw prediction distribution and noticing one villain absorbing far more
guesses than any real base rate could explain.

**What changed:** the dbt test was scoped to sessions with a traffic-producing attempt, with a
second assertion guarding against the scoping making it vacuous. The baseline's Two-Face hard
discriminator was dropped entirely after three narrower single-feature fixes all failed to separate
the two villains — he's now classified by nearest-centroid only, weighing the feature alongside the
other twelve rather than trusting it alone. Documented in `docs/02` and `docs/03` as a property of the
*pair*, not two unrelated bugs: any future feature meant to separate them needs to encode *why* the
paths repeat, not just that they do.

---

## Docker's own healthcheck was polluting the ground-truth event stream

**What broke:** the honeypot's catch-all route logs and publishes every request it receives, including
Docker's healthcheck hitting `GET /` every 5 seconds. Ten `Python-urllib` healthcheck requests turned
up indistinguishable from real attacker traffic when cross-checking attempt-to-request correlation
during the Phase 2 checkpoint.

**How it was caught:** the topic's message count kept growing with no simulator run active — a live
number moving with no known cause.

**What changed:** a dedicated `/healthz` route, registered before the catch-all so Starlette matches
it first, that returns 200 without touching the session tracker or the event producer. An
infrastructure probe isn't attacker-facing traffic; logging it would silently contaminate ground truth
with synthetic noise. Verified: a fresh topic stayed at 0 messages through 20 seconds and 4 healthcheck
cycles, and the container still reported healthy. (`88d90b5`)

---

## `dev-reset`'s own spec would have deleted the committed sample partition

**What broke:** docs/05's original spec for `make dev-reset` was `rm -rf data/raw` — which, run as
written, destroys the committed sample partition (`data/raw/*/dt=*/hour=*/sample-*`) along with the
generated corpus it's meant to clear.

**How it was caught:** running `dev-reset` for real rather than trusting the Makefile as documented —
the placeholder file it clobbered was recovered from git, which is itself the signal that the
"spec-as-written" version was wrong.

**What changed:** a targeted delete that spares `sample-*` files specifically, verified end to end:
`dev-up` brings up a genuinely fresh environment, `dev-down` tears down cleanly, and `dev-reset`
removes generated data and the warehouse while leaving the sample partition untouched. (`6293a38`)

---

## The Layer 2 detection formula made noise level irrelevant to whether an attempt got caught

**What broke:** the Phase 2 detection formula (`noise_generated / 10`) detected roughly 48% of all
attempts across *every* villain regardless of intelligence or evasion — so loud that the noise and
evasion features it was supposed to be driven by were statistically meaningless.

**How it was caught:** a 300-run-per-villain Monte Carlo showed near-identical detection rates for
villains with wildly different stat profiles, which shouldn't happen if intelligence and evasion mean
anything in the model.

**What changed:** recalibrated to roughly 5% base detection per unit of noise, suppressed up to 70%
by a high-intelligence villain's evasion stat, capped at 85%. Overall detection dropped to 7-13% for
most villains and 27% for Killer Croc (the loud, low-intelligence outlier by design), and intelligence
vs. detection rate now correlates at r = -0.957 — "the most capable villain is the hardest to detect"
became true in the data rather than only true in the narrative. (`a2bf0f4`)

---

## Flushing before committing offsets — the order that loses nothing, verified by inverting it

**What broke:** nothing, this one is a design decision proven by testing its own failure mode. The
consumer commits Kafka offsets only after every Parquet and quarantine file in a batch is durably on
disk. Reversing that order — commit, then flush — passes every existing unit test, since none of them
simulate a crash between the two steps.

**How it was caught:** not a bug found in production; a property asserted directly (via an operation
log proving flush precedes commit) specifically because unit tests alone can't distinguish the two
orderings. The real proof is the kill-mid-batch restart exercise (`docs/exercises.md`).

**What changed:** nothing about the implementation — the finding is that this class of ordering bug
is invisible to conventional unit testing and needs a structural assertion plus a real kill-and-restart
exercise to actually prove. Getting the order backwards means a crash between commit and flush loses
a batch permanently; the correct order's failure mode is a duplicate, which deduplication already
handles. (`ea0d7a0`)

---

## Two dbt tests were wrong on first run, and the data was right both times

**What broke:** `assert_stage_monotonic` failed on all 144 sessions, requiring stage 0 cleared before
stage 1 — but stage 0 (Reconnaissance) is performed offstage by Lex Luthor and produces no attempts at
all, so every real session legitimately starts at stage 1. Separately, `assert_probability_calibration`
used a flat 0.10 gap tolerance and flagged `remote_services` (gap 0.162, n=41) as miscalibrated — which
is z ≈ 2.08, ordinary sampling noise at that sample size, not a real drift.

**How it was caught:** both by reading the failure output closely enough to notice the *shape* of the
problem rather than accepting "test failed, fix the code." A flat gap over 11 techniques with wildly
different sample sizes was always going to misfire in both directions — too strict at low n, too loose
at high n.

**What changed:** the monotonicity check now starts at stage 2, matching what the stage machine
actually produces. The calibration check uses a band of 3 binomial standard errors, which tightens as
`n` grows instead of staying fixed — and scoping it to *non-detected* attempts only turned out to be a
real correctness fix in its own right: `detected` overwrites success/failure in the outcome enum and
fires on roughly 48% of attempts, so an unscoped calibration rate was comparing a `computed_probability`
that only ever predicted clean outcomes against a population half-contaminated by detection events.
Overall calibration gap once correctly scoped: 0.0038, confirming Phase 3's independently-measured
0.006. (`be377dd`)

---

## Verifying the zero-credential orchestration path silently mutated the reported evaluation dataset

**What broke:** Phase 7's Dagster checkpoint requires proving the whole asset graph materializes with
no Groq key present. That verification run correctly exercised the triage asset's zero-credential
path — but it also wrote 4 new baseline-only orders (the production threshold selector's own session
set, distinct from the 36-session stratified sample Phase 6's evaluation was measured against) into
the same `raw_triage_predictions` table the reported numbers were computed from. The next thing built
on top of that table — a detection-coverage chart meant to reproduce Phase 6's already-published
figures — came out different: 87.3% high-tier baseline recall instead of the reported 89.7%.

**How it was caught:** regenerating a chart from "the same data" and getting a different number than
what was already committed to docs/04 and the README. The mismatch was the signal, not any error or
test failure — both runs completed successfully; they just weren't measuring the same population
anymore.

**What changed:** identified the 4 extra sessions precisely (present in `raw_triage_predictions` but
missing an `llm`-source counterpart, since the verification run had no key), deleted them, and rebuilt
the four downstream evaluation marts. The regenerated chart matched the committed numbers exactly
afterward. The general lesson: a verification step that writes to shared state needs to be checked
against what it might disturb, not just against what it was designed to prove — a green checkpoint and
a silently shifted dataset can coexist.

---

## A live doc-fetch on Gemini's API described a method that doesn't exist in the installed SDK

**What broke:** nothing in the codebase — this is a finding from *before* any code was written, while
researching the Groq→Gemini provider swap. Two independent fetches of Google's official quickstart and
structured-output documentation both described `client.interactions.create(...)` as the current method
for structured JSON output on the `google-genai` SDK. That method does not exist on the installed
package (`google-genai==2.24.0`) — `dir(genai)` has no `interactions` attribute at all. The real,
working method is `client.models.generate_content(...)`, a plainer and more familiar shape.

**How it was caught:** by rule, not by accident — this project's standing practice (established
repeatedly: the dagster-dbt API surface, the Groq model catalog, the sqlfluff autofix behavior) is to
verify a fetched or remembered API against the actually-installed package before writing code against
it, specifically because a documentation summary can embellish plausible-sounding details when a page
is sparse or JS-rendered. Two independent fetches agreeing with each other was *not* treated as
sufficient corroboration on its own; `dir()` and `inspect.signature()` against the real installed
package were checked before any implementation code was written. A live 404 error later, while
smoke-testing the model choice, independently corroborated the doc content wasn't entirely fabricated:
Gemini's own API error message for a deprecated model said *"We recommend you to use the Interactions
API"* — so an Interactions API most likely exists somewhere, just not as a public method on this SDK
version's `Client` object. The doc summary wasn't pure hallucination; it described something real that
isn't reachable the way it claimed.

**What changed:** nothing had to be undone, because the verification step happened before the
first line of `_call()` was written — the entire point of doing it in this order. Two further API
details were only discoverable by testing against the live service, not documentation, and both
surfaced during the same swap:
- `gemini-2.5-flash-lite`, the more mature and conservative model choice, returned a live `404`:
  *"This model models/gemini-2.5-flash-lite is no longer available to new users."* Nothing in Google's
  docs at the time of fetching flagged this — it was a real, current account-facing state, not
  something a training-data-bound model could have known in advance regardless of cutoff date.
- `thinking_config=ThinkingConfig(thinking_budget=0)`, added specifically to avoid repeating the
  `openai/gpt-oss-20b` reasoning-token failure from Phase 6, was **rejected** by `gemini-3.5-flash-lite`
  with an opaque `400 INVALID_ARGUMENT` carrying no field-level detail. Isolated by testing each config
  parameter individually against the live API (`temperature`, `response_mime_type`, `thinking_config`,
  `http_options`, `system_instruction`, one at a time) until the single failing one was found. Omitting
  the field entirely turned out to already produce zero thinking tokens on this model by default — the
  fix was simpler than the diagnosis.

Same shape, three times over in one investigation: a documentation source (fetched, remembered, or
scraped) is a lead, not ground truth, and the only source of truth for "does this API call actually
work" is calling it.

---

## Schema-constrained output fixed parse failures completely, and quietly removed calibrated uncertainty

**What broke:** nothing, exactly — this is a trade discovered by re-running the same 36-session
evaluation against a different provider, not a bug. Groq's free tier didn't cooperate with the
maintainer's VPN, so the triage LLM moved from Groq (`qwen/qwen3.8-27b`, plain
`response_format: json_object`) to Gemini (`gemini-3.5-flash-lite`, schema-constrained
`response_schema=TriageOutput`). Parse failures went from 27.8% (10/36, Phase 6) to **0.0% (0/36)** —
every one of 36 real API calls returned schema-conformant JSON on the first attempt. That's the
reliability win the schema constraint was expected to produce, and it fully delivered.

What wasn't expected: qwen had answered `suspected_villain: "unknown"` on 8 of 36 sessions, including
a well-calibrated non-answer on 2 of Ra's al Ghul's 3 sessions (confidence dropping to 0.45, reasoning
that named the specific signatures it couldn't find). Gemini answered `"unknown"` **zero times across
all 36 sessions**, at a flat ~0.84 mean confidence *including on sessions it got wrong* — one Killer
Croc misattribution came back at 0.90 confidence with grounded, specific, entirely wrong reasoning. The
Killer Croc / Ra's al Ghul result that had been the project's strongest LLM finding under qwen (2/2
real attempts correct on Croc, a calibrated non-answer on Ra's al Ghul) did not replicate under Gemini
at all — both villains went back to 0/3, same as the baseline.

**How it was caught:** by re-running the full evaluation rather than assuming a "better," more reliable
model would just be strictly better. The parse-failure improvement was expected and confirmed. The
missing calibrated-uncertainty behavior was not something being looked for — it fell out of comparing
the two chapters' confusion matrices and `confidence` distributions side by side, the same as any other
result in this project: reported because it was measured, not because it was anticipated.

**What changed:** docs/04's Results section keeps both chapters side by side rather than replacing the
qwen numbers, and states plainly that the qwen-era "the model can express calibrated uncertainty"
finding does not hold for the current default — it was a property of one model on one sample, not a
property of "LLM triage." Nothing in the pipeline changed to chase this; it's reported as a real,
measured trade a reader evaluating this pattern for their own use should weigh: schema-constrained
output buys structural reliability and appears to cost the model's willingness to decline an answer.
Whether that's inherent to constrained decoding, specific to this model, or an artifact of the prompt
not asking for it explicitly enough is an open question this project didn't have grounds to answer from
one comparison — worth a note for whoever picks this pattern up next, not a conclusion.

---

## `raw_triage_predictions` has no snapshot identity, and it's bitten the same evaluation twice

**What broke, the first time:** documented above ("Verifying the zero-credential orchestration path
silently mutated the reported evaluation dataset") — the Phase 7 Dagster zero-credential check ran
`run_triage()` with its production threshold selector, added 4 sessions outside the pinned 36-session
evaluation sample, and silently shifted `mart_detection_coverage`'s reported baseline recall.

**What broke, the second time, same shape:** while closing out the Gemini provider swap, re-verifying
that the literal `make triage` target (not just the underlying module call) still worked post-swap did
the same thing again — 72 rows in `raw_triage_predictions` became 76, 36 distinct sessions became 40,
same 4 session IDs both times. Caught immediately by the same before/after row-count check, not by any
test failing, and fixed the same way: identify the sessions with no `llm`-source counterpart, delete
them, rebuild the four evaluation marts.

**This is one pattern under two trigger paths, not two unrelated incidents.** `_select_sessions()`
(bare `make triage`, the production trigger — "sessions at or above the triage threshold") and
`_select_stratified_sessions()` (`make triage-eval-sample`, evaluation breadth — "top N sessions per
villain regardless of threshold") are both working exactly as designed; neither is missing a guard,
and adding one to either would be the wrong fix. `mart_threat_scores` and `dim_villains` mean
different things to a threshold cutoff versus a per-villain ranking, so the two selectors legitimately
pick different, overlapping-but-not-identical session sets from the same warehouse — that's correct
behavior for their two different purposes (docs/02's Catwoman calibration note is exactly why both
need to exist). The actual gap is structural: `raw_triage_predictions` is one shared, mutable table
keyed only by `(session_id, source)`, with no concept of "these rows belong to evaluation run X."
**Any** selector run against a warehouse whose current `raw_triage_predictions` content backs a
specific reported evaluation will silently extend it — this is a property of testing against shared
warehouse state with multiple independently-correct selection modes, not a bug in either mode. It's an
operational footgun for local iteration on a warehouse that already holds a pinned evaluation snapshot,
not a code defect to patch. A one-line pointer to this entry is left at the point in the code where a
future run is most likely to trigger it again; a real fix, if one is ever wanted, would mean giving
evaluation snapshots their own identity (a `run_id` or a separate table) rather than sharing the one
live table production writes to — worth doing if this bites a third time, not before.
