# 08 — Interactive experience

**Track B. Do not build any of this until Track A phase 7 is complete and pushed public.**

Everything here is a presentation layer over data models that already exist. The headless simulator
in `docs/07-attack-chain.md` must produce the full event stream first. If building the console
requires changing a dbt model, the interface is emitting something the headless path did not, and
that divergence gets resolved in favor of the headless contract.

---

## Console shell

Static SPA. No framework required.

**The `SIMULATION` frame is present from the first screen and never removed.** It wraps every view
including the finale. This is not a disclaimer shown at the end; it is persistent chrome.

Screens: welcome → villain select → stage 1 → 2 → 3 → 4 → bat bot → counterstrike → dashboard link.

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

Each bullet should map to a real technique ID so the stage-0 row in `fct_stage_progression` is
honest rather than decorative.

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
- Parameter controls per technique, feeding `parameters` on the attempt event
- Attempt streams as terminal log lines: the technique running, the roll, the outcome
- On failure: retry with different parameters, or pivot to another technique
- Stage clears on success; stalls when no viable technique remains
- Running counters: attempts, noise generated, elapsed time

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
> Nothing on your computer or your devices is affected by any part of this.

### Turn contract

3–5 turns, hard capped in code, not in the prompt.

| Turn | Objective | Cover |
|---|---|---|
| 1 | Rapport, open question | "Batcave support assistant — I see you're having trouble" |
| 2 | Probe identity or affiliation | "Let me verify your access level" |
| 3 | Probe location or environment | "I'll route you to your regional node" |
| 4 | Probe a secondary detail | "One more thing to confirm" |
| 5 | Reveal | — |

Emits `chat_turn` events: `turn_number`, `speaker`, `objective`, `bot_text`, `user_text`,
`extracted_intent_flags`, `refused`, `latency_ms`, `input_tokens`, `output_tokens`.

**`user_text` never enters the committed sample partition.** Enforced by
`assert_no_user_text_in_sample`, and verified by grepping the committed Parquet rather than trusting
the code.

Same Gemini model as triage, separate prompt file. Scripted fallback without an API key so the finale
still runs with zero credentials.

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
