# AI Features

Both features use Groq's free tier. Groq is rate-limited (requests/minute and tokens/minute), not a fixed daily token budget — the design below keeps both features well inside that, and caches results so nothing regenerates on every page view.

## Model migration note (Aug 2026)

Groq deprecated `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` for free/developer-tier usage (announced June 17, 2026, effective per their Aug 17, 2026 notice). This spec now targets their recommended replacements: `openai/gpt-oss-20b` (for the 8B role) and `openai/gpt-oss-120b` (for the 70B role) — `qwen/qwen3.6-27b` was offered by Groq as an alternative to `gpt-oss-120b` and is worth trying if `gpt-oss-120b`'s interview tone doesn't land well in practice.

One thing this migration changes about the reasoning below, not just the model names: the old models had a big free-tier daily-request asymmetry (14,400 requests/day on the 8B model vs. 1,000/day on the 70B), which was part of why the cheaper model made sense for the higher-frequency feature. As of this migration, both `gpt-oss-20b` and `gpt-oss-120b` sit at the same free-tier ceiling — reportedly 30 requests/minute and 1,000 requests/day each, per-model. Confirm the current numbers at `console.groq.com/docs/rate-limits` before implementation, since Groq's free-tier limits have moved before and will again. The model split below is now purely a quality/speed choice per task, not a volume-budget one.

Also worth knowing: the GPT-OSS models support a configurable reasoning effort and can emit internal reasoning tokens as part of a response, separate from the visible output. If left at a high default, that can inflate actual output-token usage well past the estimates below. Set it low on both calls:

```json
{ "reasoning_effort": "low" }
```

`gpt-oss-20b` and `gpt-oss-120b` both accept `"low"` / `"medium"` / `"high"` (default `"medium"`) for this field. If `qwen/qwen3.6-27b` ends up being used instead of `gpt-oss-120b` for the interview feature, the same field takes different values there — Qwen supports `"none"` (off) or `"default"` (on), not the low/medium/high scale, so that's a value swap, not just a model-name swap.

One more constraint tied to this: both features here use structured JSON output (`response_format` / JSON schema). Groq requires `reasoning_format` to be `"parsed"` or `"hidden"` when JSON mode is in use — `"raw"` isn't allowed in that combination. `"hidden"` is the simpler choice, since neither feature needs the reasoning trace surfaced anywhere.

## Feature 1 — AI candidate profile + fit score

Generates a narrative bio and a 0–100 fit score for a specific `JobApplication` (candidate × requisition pairing — fit is requisition-specific, so this isn't computed once per candidate globally).

- **Model:** `openai/gpt-oss-20b` — this step doesn't need the heaviest model; it's a short, fairly mechanical summarization + scoring task, and gpt-oss-20b is Groq's fastest model on the platform (reportedly ~1,000 tokens/sec), which is the more relevant advantage now that both models share the same daily request ceiling (see the migration note above) — lower latency per call matters more here than a bigger request budget.
- **Input to the prompt:** candidate `name`, `occupation`, `age`, `phrases` (a few, for personality flavor); requisition `title`, `department`, `targetKeywords`.
- **Output:** structured JSON — `{bio: string, fitScore: int, fitRationale: string}` — parsed directly into `AiCandidateProfile`.
- **Token cost estimate:** ~300–600 input tokens, ~200–400 output tokens per call. Trivial even scoring a few dozen candidates in one sitting.
- **Measured (2026-09-09, 11 real generations):** ~690 input, ~140 output, ~830 total. Input runs above the estimate because the scoring rubric below lives in the system prompt; output runs below it because the reply is three short fields. Being in the system prompt, the rubric is also the part eligible for prompt caching.
- **Caching:** stored on `AiCandidateProfile`, regenerated only via an explicit "Refresh" action or if the requisition's `targetKeywords` changed since the last generation — see `DATA_MODEL.md`.

### The scoring prompt needs an anchored scale (learned in implementation)

Asking for "a 0–100 fit score" without saying what the numbers mean produces an unusable spread:
the same candidate against the same role scored 20, 40, 55, 60 and 70 across repeated runs.
Temperature is not the lever — dropping it from 0.4 to 0.1 still left a 20–65 range. The model was
re-inventing the scale on every call.

Explicit bands in the system prompt fix it, and the same case then returns 55 five times running:

| Band | Meaning |
|---|---|
| 90–100 | the candidate's occupation **is** this role, or names its primary keyword directly |
| 70–89 | closely adjacent — the core skill transfers, little ramp-up needed |
| 40–69 | partial overlap — right field or one significant keyword, missing the specific domain |
| 10–39 | unrelated occupation, but a trainable working adult |
| 0–9 | no relevant background at all |

Instructing the model to pick the lower band when a candidate sits between two matters as much as
the bands themselves.

### Temperature differs per feature

`groq.temperature.profile` is 0.1 and `groq.temperature.interview` is 0.8. Scoring is a judgement
that gets cached and read back, so it wants repeatability; the interview's whole value is character
voice, which wants room to vary.

### Rate limits will be hit, and are worth handling in the client

Generating profiles for a batch of applications runs into the per-minute free-tier limit almost
immediately. The Groq client retries up to four times on 429, honouring `Retry-After` when Groq
sends one — note it arrives as fractional seconds, so parsing it as an integer fails — and
otherwise backing off 2s/4s/8s. Any other status fails immediately, since retrying a malformed
request only spends budget.

## Feature 2 — Mock interview (single structured generation call)

Per the fixed-question-set scope decision, this is **one Groq call that produces the entire interview** — questions, in-character answers, and a closing assessment — rather than a multi-turn chat that resends growing history on every exchange. This is both simpler to implement and meaningfully cheaper: a 5–8 turn live chat could cost tens of thousands of cumulative tokens by the end (each turn resending the full history so far); a single structured call covering the same ground costs roughly one turn's worth.

- **Model:** `openai/gpt-oss-120b` — this is the feature where response quality/personality actually matters for the portfolio demo to land, so it's worth spending the larger model here (120B vs. 20B parameters). `qwen/qwen3.6-27b` is a reasonable fallback to A/B against if gpt-oss-120b's character voice feels off — both sit at the same free-tier request ceiling as gpt-oss-20b now, so there's no volume penalty either way (see the migration note above).
- **Input to the prompt:** candidate `name`, `occupation`, `age`, `phrases`; requisition `title`/`department`; an instruction to generate 5–8 interview questions appropriate to the role, then answer each one in character, using the candidate's known personality/catchphrases as flavor without simply repeating phrases verbatim as answers.
- **Output:** structured JSON — an array of `{questionNumber, question, answer}` (5–8 items) plus `{overallAssessment: string, overallRating: int}`. Parsed directly into one `MockInterviewSession` + N `MockInterviewTurn` rows.
- **Token cost estimate:** ~400–800 input tokens (persona + role context + instructions), ~1,000–1,800 output tokens (8 Q&A pairs + summary) → roughly 1.5–2.5K tokens for an entire interview session. Comfortably inside any free-tier limit even run repeatedly in a demo session.
- **Measured (2026-09-09, 9 real interviews):** 1,436–1,845 tokens per session — input 769–806, output roughly 650–1,050. The estimate held. Sessions came back with 5–6 turns rather than the full 8, which is why output lands under the projected ceiling.
- **Caching:** one `MockInterviewSession` per application by default; regenerating creates a new session rather than overwriting, so a recruiter can compare interview attempts if that's ever useful, without extra design work now — just don't build any UI that assumes there's only ever one.

### What actually produces distinct character voices

The instruction that does the work is telling the model to reveal unsuitability *through the
answers* rather than describing it. Asked about managing a police budget, Bart talks about the
fourth-grade cafeteria line and trading a comic book for a snack; Mr. Burns allocates to "the most
profitable divisions" and accepts body cameras "only after a thorough review to ensure the footage
does not reveal any… inconvenient truths"; Clancy Wiggum makes sure the patrol cars have gas and
keeps a little aside for handing out candy on Halloween. None of those answers would transfer to
another candidate.

Two supporting rules matter as much:

- **Catchphrases are evidence of temperament, not answers.** Cap them at one or two across an entire
  interview and require them to fall inside a sentence. Without this the transcript degenerates into
  a list of quotes, which reads as parody rather than as an interview.
- **The 1–5 rating needs bands**, for the same reason the fit score did (see Feature 1). It was
  banded from the start here rather than waiting to rediscover the problem.

### Failed generations are worth storing

`MockInterviewSession.status` has a `Failed` value, and it earns its place: a failed attempt is
recorded rather than discarded, so a run of failures is visible instead of looking like nobody ever
tried. The write has to commit in its own transaction, since it happens on the way to throwing —
joining the caller's transaction would roll the record back along with the request.

## Optional efficiency lever: prompt caching

Groq's prompt caching means cached tokens don't count against rate limits. If the system-prompt portion of these calls (the general "you are simulating a job interview with a fictional character" instructions, as opposed to the per-candidate specifics) stays identical across calls, structuring the prompt so that shared instruction block is cacheable is a real, not-hard-to-wire-up saving if usage ever scales beyond casual demo use. Not necessary for v1, worth knowing it's there.

## Explicitly separate: AI self-assessment vs. recruiter feedback

`MockInterviewSession.overallAssessment`/`overallRating` is the model's own read on how the fictional candidate performed in the interview it just generated. `RecruiterFeedback` (see `DATA_MODEL.md`) is your own judgment after reading the transcript. Keep these as two distinct fields/entities rather than merging them — they answer different questions ("how did the AI think the answers went" vs. "what do you actually think of this candidate"), same separation-of-concerns principle used for the dual-output pattern elsewhere in this portfolio.
