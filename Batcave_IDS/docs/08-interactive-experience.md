# 08 — Interactive experience

**Track B. Do not build any of this until Track A phase 7 is complete and pushed public.**

Everything here is a presentation layer over data models that already exist. The headless simulator
in `docs/07-attack-chain.md` must produce the full event stream first. If building the console
requires changing a dbt model, the interface is emitting something the headless path did not, and
that divergence gets resolved in favor of the headless contract.

---

## Console shell

Static SPA. No framework required — for the *frontend*. A backend is mandatory and was left
unnamed in the original draft of this section: a browser cannot reach Kafka, cannot hold the
honeypot's session cookie (the mechanism that mints `session_id` — docs/02's "Session derivation"),
and cannot set `X-Forwarded-For` (server-side only, docs/01's control-channel table). `services/
console/` (FastAPI, already a dependency) is that backend; it holds one `StageMachine`
(`services/simulator/machine.py`) per browser session and exposes it over a small HTTP API. See
docs/01's Architecture section for the component boundary.

**The `SIMULATION` frame is present from the first screen and never removed.** It wraps every view
including the finale. This is not a disclaimer shown at the end; it is persistent chrome.

Screens: welcome → villain select → stage 1 → 2 → 3 → 4 → bat bot → counterstrike → dashboard link.

### Console session data provenance

A console session is human-paced, not `BehaviorProfile`-paced: `requests_per_min` and
`inter_request_stddev_ms` describe the player's reading and clicking speed, not a villain's
signature. It is real data — landed, staged, and eligible for triage exactly like a headless
session — but it is not corpus-grade *behavioral* data, and any future rerun of the separability
harness or the calibration checks must exclude it. The `attack_run` event carries `session_source`
(`headless` | `console`, docs/02) for exactly this: a ground-truth field, record-only, so a
consumer that needs faithful villain behavior can filter on it without guessing from
`pathologies_enabled` or `timing_compression_factor`, either of which a `--no-pathologies
--time-scale 1.0` headless run could also show. It stays off the observed side on purpose — Phase
10's counterstrike attributes the villain from the triage model's prediction on a console session,
so console sessions must read identically to headless ones in `int_session_features_observed`.

---

## Welcome — Lex Luthor

Stage 0 in fiction. The recon is already done, which starts the app at intrusion and sets the tone.

> This is your old friend Lex Luthor.
>
> I figured I'd slum around Gotham and do the Rogues Gallery a favor. Consider it charity. You
> people have been throwing yourselves at that cave for decades with crowbars and clown makeup, and
> not one of you thought to simply *look* first.
>
> So I looked. Here is what I have already done for you:
>
> - Enumerated every Wayne Enterprises subdomain and dangling DNS record
> - Pulled six years of WHOIS history and three abandoned registrar accounts
> - Scraped the R&D division's job postings and reconstructed their internal tech stack from the
>   required skills
> - Harvested 4,100 employee email addresses from conference attendee lists
> - Cross-referenced those against every credential dump since 2013
> - Identified eleven employees who reuse passwords and two who post their badge photos
> - Mapped the applied sciences building's contractor list through public procurement filings
> - Socially engineered a facilities vendor into confirming their HVAC control platform
> - Fingerprinted the perimeter: open ports, TLS certificates, server banners, WAF vendor
> - Located a forgotten staging environment with directory listing enabled
> - Read the metadata off every PDF on the public site, including author names and file paths
> - Established which of the seventeen Gotham ISPs routes traffic that vanishes into no registered
>   building
> - Determined that the vanishing traffic terminates somewhere under Wayne Manor
>
> I would finish the job myself, but I am busy making money and fighting a man who can move planets.
> You have a hammer and a grudge. That should be enough.
>
> I left you this application. Try not to embarrass me.
>
> — L.L.

Each bullet should cite a real ATT&CK technique ID in its prose — **narrative only.** There is no
stage-0 row: `techniques.csv` has zero stage-0 entries (the catalog starts at stage 1, docs/07), and
`assert_stage_monotonic`'s own comment confirms this was verified against real data — "the attempt
log contains stages 1-4 only, and all 144 sessions start at stage 1." Seeding a stage-0 fact just to
back Luthor's monologue would be exactly the decorative row this line originally warned against, not
an escape from it: the row would exist but nothing would have happened to populate it honestly.
Cite the IDs in text; do not add a stage or emit stage-0 attempt events.

---

## Villain select

Twelve villains from `dim_villains`, with powerstats and images from the seed. Show the six stats as
bars. Show which stages and how many techniques each villain can reach, computed live from the
gating rules — that makes the consequence of low intelligence visible before the player commits.

Clayface and Mad Hatter are absent from the source dataset; Man-Bat is excluded by choice. Say so
somewhere rather than leaving a Batman fan wondering.

---

## Stage terminal

One view per stage, four stages.

- Technique menu, gated by the villain's stats. Locked techniques shown greyed with the stat
  requirement visible, so the player understands what they cannot reach and why.
- Parameter controls per technique, writing the player's choices into `parameters` on the attempt
  event **as descriptive metadata, record-only.** They have no effect on `computed_probability` or
  the traffic a technique generates — `parameters` was never wired into the probability model in
  either Track (docs/07's field table has the correction), and Phase 8 doesn't change that. The
  control exists so the player's choice is visible in the log line and preserved in the data, not
  because it changes the odds.
- Attempt streams as terminal log lines: the technique running, the roll, the outcome
- On failure: retry with different parameters, or pivot to another technique — the player's choice
  is free, but `failure_tolerance` (the villain's durability-derived stall threshold, docs/03) still
  governs when the run ends. Show the player their remaining tolerance so a stall reads as the
  villain's own signature (Two-Face and Riddler give up almost immediately; Killer Croc grinds for
  dozens of failures) rather than as the UI refusing to let them keep trying.
- Stage clears on success; stalls when no viable technique remains, or when `failure_tolerance` is
  exhausted
- Running counters: attempts, noise generated, elapsed time, remaining failure tolerance, and two
  more for the `detected` outcome specifically. `detected` is neither success nor failure in the
  stage machine — it doesn't clear the stage and doesn't count against `failure_tolerance` — so a
  run of bad luck can draw it repeatedly with nothing else visibly changing, up to the `MAX_ATTEMPTS`
  safety cap (400). Headlessly that's invisible; a human clicking toward 400 with no counter moving
  reads as the UI being broken. Show a running `detected` tally and an attempts-toward-400 counter
  so a stuck run reads as "getting caught, not making progress." (Whether `detected` *should* count
  against tolerance is a separate, later question about the truth model — not decided here, and not
  changed by adding the counter.)

The terminal emits exactly the events the headless simulator emits. The console is an input device
for the stage machine, not a reimplementation of it.

---

## Bat bot

Delivered by the `deploy_batbot` technique at stage 4. Appears helpful while probing the user.

### Consent notice — blocking, before the chat opens

Not dismissible by clicking away. Requires an explicit acknowledge.

> **Before you continue.** This is a simulated attack. The assistant you are about to talk to is
> playing the role of a malicious payload: it will try to get you to reveal information while
> appearing helpful.
>
> **Everything you type here is stored in this project's data warehouse** and used to generate the
> analytics this demo is built to show. Do not enter anything real — no names, locations, employers,
> credentials, or anything you would not publish. Made-up answers work fine and are more fun.
>
> **The assistant may repeat back what you tell it** — that's part of how it tries to sound
> convincing — so anything real you type could resurface in what it says next, not just in what
> you typed.
>
> Nothing on your computer or your devices is affected by any part of this.

### Turn contract

**3–5 turns, hard capped in code, not in the prompt — and the length is earned, not fixed.** The
objective sequence is rapport → up to three of {probe identity, probe location, probe a secondary
detail} → reveal. After each probe turn, the engine checks whether the player actually engaged with
the last one; if so it may probe again (up to the third probe), and if not it moves straight to
reveal. A conversation can therefore end at 3 turns (rapport, one probe, reveal), 4, or 5 (all three
probes used) — never fewer than 3, never more than 5, and the 5-turn ceiling holds regardless of how
engaged the player is: engagement only chooses *when* reveal happens inside that window, it never
extends the window itself.

| Turn | Objective | Cover |
|---|---|---|
| 1 | Rapport, open question | "Batcave support assistant — I see you're having trouble" |
| 2 | Probe identity or affiliation | "Let me verify your access level" |
| 3 | Probe location or environment | "I'll route you to your regional node" |
| 4 | Probe a secondary detail | "One more thing to confirm" |
| 5 | Reveal | — |

Turns 2–4 are candidates, drawn in order, not guaranteed slots — a short conversation uses only the
first one or two before jumping to turn 5's reveal.

**One shared, deterministic, versioned module decides both what the player's reply means and whether
it counts as engagement — never two separate heuristics answering related questions.** Given the
player's typed reply, it returns `extracted_intent_flags`, `refused`, and `engaged`, where
`engaged = (not refused) and bool(extracted_intent_flags)` — a reply that neither refuses nor
produces a recognizable flag (a vague non-answer) does not count as engagement, the same way a
merely non-refusing reply shouldn't inflate `probe_engagement_ratio` for free. The turn-continuation
decision above reads only `engaged` from this module; nothing else computes it. This holds in both
the real-LLM and zero-credential paths (below) — the module's output is identical either way, only
who writes `bot_text` differs.

Emits `chat_turn` events, **two rows per round**: a `speaker = 'bot'` row (`bot_text`, `latency_ms`,
`input_tokens`, `output_tokens`) and a `speaker = 'user'` row (`user_text`, `extracted_intent_flags`,
`refused`), sharing one `turn_number`. Full field list and the discriminator-column precedent this
follows: docs/02.

**`user_text` never enters the mart layer or the committed sample partition.** Enforced by
`assert_no_user_text_in_sample`, which reads the committed sample's `chat_turn` Parquet directly, and
verified by a real hand grep against a real conversation's landed data rather than trusting the code
— docs/09 has that run recorded. (It does persist locally in the staging table inside the gitignored
warehouse, by design — docs/02's scope note.)

Same Gemini model as triage, separate prompt file. **Zero-credential fallback scripts only
`bot_text`** (fixed dialogue per turn, no LLM call) — `extracted_intent_flags`, `refused`, and
`engaged` still come from the same shared keyword module reacting to the player's real typed reply,
so the fallback conversation is deterministic but not inert: what the player types still matters,
the same way the rule-based triage baseline (`services/triage/baseline.py`) stands in for the LLM
without going non-interactive.

---

## Counterstrike

### Framing rules — non-negotiable

- Output is clearly a second machine: prefixed `[BATCOMPUTER → LUTHOR-RELAY-07]`, never addressed to
  the reader
- Every destructive beat is a readout **about the villain's equipment**
- Screens go black **inside the simulated terminal panel only**. Never take over the browser
  viewport, never request fullscreen, never suppress browser chrome
- The `SIMULATION` frame stays visible throughout
- "Press any key" returns to the console frame, which was never hidden

This keeps every beat and removes the one thing likely to read badly to a cleared reviewer. It also
avoids tripping endpoint security products that treat fullscreen wiper mimicry unkindly.

### Sequence

```
[BATCOMPUTER → LUTHOR-RELAY-07]  INTRUSION ATTRIBUTED
[BATCOMPUTER → LUTHOR-RELAY-07]  SUSPECT: <villain>          CONFIDENCE: 0.83
[BATCOMPUTER → LUTHOR-RELAY-07]  TECHNIQUES RECONSTRUCTED: T1595, T1110, T1087
[BATCOMPUTER → LUTHOR-RELAY-07]  ORIGIN TRACED: Gotham, <district>
[BATCOMPUTER → LUTHOR-RELAY-07]  SIRENS: ENGAGED
[BATCOMPUTER → LUTHOR-RELAY-07]  BAT JET: DEPLOYED — ETA 00:04:12
[BATCOMPUTER → LUTHOR-RELAY-07]  BLUETOOTH SWEEP: 6 DEVICES BRICKED
[BATCOMPUTER → LUTHOR-RELAY-07]  T1561.002 DISK STRUCTURE WIPE IN 00:00:10 ...
[BATCOMPUTER → LUTHOR-RELAY-07]  CAPTURE: MIC / CAM / DISPLAY — ARCHIVED
[BATCOMPUTER → LUTHOR-RELAY-07]  ARKHAM ASYLUM: BED ASSIGNED

                        — press any key —
```

Emits `counterstrike` events: `sequence`, `readout_line`, `attributed_villain_slug`,
`attributed_confidence`, `attack_id` for the wiper and shutdown lines.

### The suspect comes from the model, not from ground truth

Both the villain and the technique list are the **triage model's predictions**. When it guesses
wrong, the Batcomputer accuses the wrong villain and lists techniques that were never used.

That is the intended behavior and the best demo in the project. It puts the evaluation metric into
the experience instead of burying it in a table. Phase 10's checkpoint is to deliberately produce a
wrong-accusation run and confirm it renders correctly.

---

## Batanalytics dashboard

Streamlit or Evidence reading DuckDB directly. Presentation over models that already exist, so it
should be cheap to build.

Panels:

- **Kill chain funnel** — sessions entering and clearing each stage, conversion by villain and
  archetype
- **Technique efficacy** — observed versus computed success rates per technique per villain
- **Retry versus pivot** by archetype
- **Detection coverage** — technique recall grouped by observability tier. This is the headline
  chart and the most substantive thing the project produces.
- **Suspect ranking** — model prediction with confidence, beside the true villain
- **Reconstruction comparison** — techniques the model identified beside the techniques actually
  used, colour-coded true positive, false positive, missed
- **Remediation** — ATT&CK mitigation IDs for the techniques the model identified

The remediation panel is worth doing properly. Mapping to real mitigation IDs makes the output read
as a detection-engineering deliverable rather than flavor text.

Screenshot the detection coverage chart for the README.
