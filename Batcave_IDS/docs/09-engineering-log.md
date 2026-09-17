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
