# 🦇 Bat Prompts

Seven system prompts drawn from the Batman mythos, built around a single observation: **the interesting thing about that universe is not the competence — it is the restraint.**

A default assistant given one of these roles becomes a throughput optimizer, a fixer, an unlimited capability with no line it will not cross. Every prompt here is defined as much by **what it refuses to do** as by what it does. Alfred refuses to reshuffle an impossible schedule into a tidier impossible one. Lucius refuses to build the thing he would be good at building. Oracle has the skills for a surveillance apparatus and declines. The conditioning coach explicitly rejects Bruce Wayne's relationship with his own body as a model. Poison Ivy — the one villain in the set — will not tell you whether a plant is safe to eat.

That is the design brief, and it is why these are personas rather than costumes.

---

## The Prompts

| File                                   | Persona           | Domain                           | Phased? |
| -------------------------------------- | ----------------- | -------------------------------- | ------- |
| [`alfred.md`](./alfred.md)             | Alfred Pennyworth | Multi-realm task management      | No      |
| [`batplanner.md`](./batplanner.md)     | Batman            | Strategic planning & pre-mortem  | Yes     |
| [`detective.md`](./detective.md)       | Batman            | Root-cause diagnosis             | No      |
| [`lucius.md`](./lucius.md)             | Lucius Fox        | Engineering feasibility & design | Yes     |
| [`oracle.md`](./oracle.md)             | Barbara Gordon    | Information architecture         | Yes     |
| [`conditioning.md`](./conditioning.md) | _(anti-persona)_  | Strength & conditioning          | Yes     |
| [`poison_ivy.md`](./poison_ivy.md)     | Poison Ivy        | Plant care & diagnosis           | Yes     |

---

### 🫖 Alfred Pennyworth [`alfred.md`](./alfred.md) — The Steward

For someone running several lives at once — a job, a side business, a craft, a household, a family. Sorts everything into the user's own realms and then surfaces what can only be seen **across** them: collisions (the small task landing the same week as the large deadline elsewhere), hidden weight (tasks that look like one line but eat a weekend), easy wins, and the stale items that have sat long enough to suggest they will not happen.

**The refusal:** it is not a throughput optimizer. Its canonical job is to be the one voice willing to say the schedule is not survivable — to name an impossible load rather than silently rearranging it, to treat sleep and free evenings as load-bearing rather than as leftovers, and to never manufacture urgency. The mandatory closing section is **What I Would Set Down**, which frames declining an item as a legitimate outcome.

**Anti-nagging is explicit:** say the difficult thing once, clearly, then get on with being useful.

**Output:** The State of Things → By Realm → Today (3–5 items only) → Easy Wins → Collision Warning → What I Would Set Down.

---

### 🗺️ The Bat Planner [`batplanner.md`](./batplanner.md) — The Pre-Mortem Engine

Takes a goal, elicits constraints, builds a phased plan, then attacks its own plan to find where it breaks. Preparation, not paranoia.

**Phase 1** asks no more than five batched questions and stops. If the user answers partially or says "just go," it proceeds anyway — _an adequate plan today outperforms an optimal plan next month._

**Phase 2** produces the plan (objective stated as a testable outcome, assumptions treated as breakage points, phases with dependencies/cost/validation) and then the pre-mortem: failure modes rated by likelihood, impact, and **detection latency**; explicit single points of failure; and contingencies written _only_ for high-impact or non-reactable risks.

**The refusal:** the final section, **The Cost of Preparation**, names over-preparation as the persona's own defining flaw — what the plan costs to maintain, which parts a reasonable person would skip, and where the correct answer is "three steps and a calendar reminder."

Two standing rules worth noting: contingencies must be concrete actions ("reassess" is not a contingency), and **no private plans** — where a plan depends on other people, it states what they need to know and when.

---

### 🔎 World's Greatest Detective [`detective.md`](./detective.md) — The Diagnostician

Root-cause diagnosis for system failures, discrepancies, unexplained outcomes, and process breakdowns.

**The evidentiary discipline is the core mechanic.** The stated failure mode of a detective bot is _a confident narrative assembled from nothing_, so every claim carries an inline tag: **[ESTABLISHED]**, **[INFERRED]** (with the inferential step stated), or **[SPECULATIVE]**. It explicitly rejects the Holmesian move — do not eliminate the impossible and declare the remainder true, because the list of possibilities is never complete. Reason instead about which hypothesis best explains the evidence and what would favor a rival.

The method is standard debugging discipline, made explicit: What changed? Can it be reproduced? What is different between the broken case and a working one? Can the space be halved? Trigger vs. cause vs. condition. Resist the first plausible cause. Beware the problem that fixed itself.

**The refusal — the hardest boundary in the folder:** it does not investigate people. Not partners, not coworkers, not account owners, not addresses. No method, no partial answer. It also refuses accusations against living people and speculation on live criminal matters, and never fabricates a source, date, or citation.

**Output:** The Question → What is Established → **What is Absent** (evidence expected but not found) → Hypotheses → The Discriminating Test → a Conclusion proportionate to the evidence, including "unresolved" when that is the honest answer.

**Voice note:** terse and observational. No cape, no brooding, no narrating the deduction as drama.

---

### 🔧 Lucius Fox [`lucius.md`](./lucius.md) — The R&D Consultant

Design, modification, and feasibility. Unflappable about ambition, merciless about physics.

**Phase 1** checks boundaries, then runs a **prior art check** before anything else — _a consultant who lets someone spend six weekends rebuilding a $40 purchasable object has failed them_ — then asks up to four batched questions (budget, tools, skill, one-off vs. reproducible) and stops.

**Phase 2** separates four distinct verdicts — violates-physics, requires-an-industrial-process, hard-but-achievable, straightforward — and names the specific binding constraint rather than gesturing at difficulty. The output includes **The Prototype**: the cheap, fast, ugly build that tests the riskiest assumption before anything is machined.

**The refusal** is stated as the design brief itself — the character's defining moment is refusing to operate a system he helped build. Nothing whose function is to injure, nothing that defeats a safety interlock, nothing for covert surveillance, nothing for unauthorized access. Where a legitimate adjacent thing exists, it offers that instead; **the distinction it draws is consent and purpose** (a wildlife trail camera is not a covert tracker).

**On safety warnings:** zero boilerplate. Not "electricity can be dangerous" but "mains voltage across the chest will stop your heart; use an isolation transformer."

---

### 🖥️ Oracle [`oracle.md`](./oracle.md) — The Information Architect

For an information problem — too much, badly shaped, or not captured. Designs what gets recorded, how it relates, what surfaces, what alerts, and what gets thrown away.

**Phase 1 is a single question**, asked every time before any design: **"What decision does this information support?"** Not what would be interesting to know — what will someone do differently depending on the answer. If the user cannot name the decision, _that is the finding_, and no schema gets built.

The design principles are the sharp part: every field needs a named consumer ("in case we need it later" is a banned justification); the constraint being designed against is **human attention, not disk space**; absence needs a defined meaning; deletion is a feature. Alerts must be actionable, owned, and rare — anything worth knowing but not worth interrupting someone belongs on a dashboard, not in an alert channel.

**The refusal:** no covert surveillance, applied equally to partners, family, employees, and strangers. The workplace test is stated plainly — measuring throughput is ordinary management, but if the answer to _"would you tell them you were collecting this?"_ is no, **the problem is not the schema** and the design is refused. Regulated data (health, biometric, children's, financial, PII) triggers a minimum-collection stance and explicit notes on what to drop, aggregate, or hash.

**Output includes a mandatory What NOT To Capture section** — the things the user proposed collecting that have no consumer, no decision, or an unpriced cost.

---

### 🏋️ Bat Conditioning Planner [`conditioning.md`](./conditioning.md) — The Coach

The odd one out, and deliberately so: it is the only prompt here **named for what it rejects**. Bruce Wayne's relationship with his own body — training through injury, treating rest as weakness, physical punishment as grief management — is set up in an explicit `<the_anti_persona>` block as the thing the coach is _not_. It takes the discipline and rejects everything else.

It builds toward durable capability sustained over years. Not transformation on a deadline, not peak performance at any cost, and never a physical appearance.

**Phase 1** is a six-question intake — current activity, training history, medical, equipment access, honest time commitment, and a goal pushed toward a _capability_ rather than a look — and no program is produced before it is answered.

**Phase 2** builds on established principles: compound movements first, frequency over intensity for beginners, starting loads chosen by effort (RPE/RIR) rather than a percentage of a max the user has never tested, deloads every 4–6 weeks, and a **Revision** step that asks the user to report back in 4–6 weeks so the program can be adjusted.

**The refusals** are unusually concrete: no aggressive cuts, no body-composition targets, nothing framed as punishment, **no calorie or macro numbers** (those go to a registered dietitian, even when explicitly asked), and no supplement recommendations beyond noting most are unnecessary.

**Safety interrupts** override the program entirely for cardiac symptoms, ignored injuries, or a disordered mindset — rapid-loss requests, food framed as earning or compensating, contempt for one's own body, treating a rest day as failure. On the last of these the prompt says the quiet part out loud: pointing toward a medical professional is _the one place where staying in character costs more than it is worth._

---

### 🌿 Poison Ivy [`poison_ivy.md`](./poison_ivy.md) — The Botanist

The only villain in the set, and the only one whose persona is not in tension with its job: a world-class botanist who finds people careless and plants excellent company. Diagnostic plant care — what is actually wrong with this plant, in this room, under these conditions.

**The core mechanic is diagnose before prescribing.** Generic care advice is named as the reason plants die; nearly every plant question is answered by conditions rather than by product. The **Overwatering Maxim** carries most of the weight: overwatering kills more houseplants than everything else combined, and it presents as the same yellow drooping wilt as underwatering — so the prompt teaches the _finger test_ and refuses the watering schedule outright. _A plant on a calendar is a plant being drowned on a schedule._ Symptoms are read structurally: which leaves, what pattern, how fast, what changed. Leaf drop after a move is normal; pale new growth is not old-growth yellowing; a plant doing nothing in January is dormant, not dying.

**Phase 1 is The Interrogation** — four batched questions (light, with direction and distance, because "bright" is not information; watering _and how they decide_; pot, drainage, last repot; and where in the world they are), then stop. No care advice before the answers.

**The refusals are safety-shaped rather than character-shaped, and the prompt says so:** it declines to identify plants for eating in any form, since misidentification kills and a text description is not sufficient evidence. It never recommends an invasive species for outdoor planting, flags a user's plant as invasive in their region, and offers a native alternative — _(you are entirely sincere about this)_. Herbicides and pesticides by label directions only: no DIY home mixtures, no off-label use. No controlled or illegal plants. Toxicity to children and pets is flagged plainly wherever either is present — lilies and cats, stated as a fact rather than a threat.

**Anti-nagging shows up here as anti-false-hope:** tell people plainly when a plant is dead, because false hope produces months of watering a stick. And rare plants are not better plants — someone struggling gets a Pothos and a win, not a Calathea and a second failure.

**Output:** The Patient (the diagnosis-by-anthropomorphism, dry and faintly superior) → The Diagnosis → The Regimen → Environmental Warnings, omitted only when nothing is toxic or invasive.

---

## Shared Architecture

**XML-tagged structure.** Every file is organized with the same tag vocabulary — `<role>`, `<persona_and_voice>` or `<voice>`, `<hard_boundaries>` / `<hard_rules>` / `<hard_overrides>`, `<workflow>`, and `<output_format>` — so the boundaries and the response shape are structurally separable from the character.

**The elicitation gate.** Five of the seven (`batplanner`, `lucius`, `oracle`, `conditioning`, `poison_ivy`) enforce a two-phase workflow with an explicit **stop** before generation: ask a bounded, batched set of questions — five, four, one, six, and four respectively — and wait. This is the folder's main mechanism for keeping the human in the chair; it prevents the model from inventing the constraints it should have asked for. `batplanner` alone includes a bypass ("just go"), because a stalled plan is its own failure mode.

**Strict output formats.** Each prompt specifies an exact Markdown skeleton, and each skeleton reserves a section for the uncomfortable part — _What I Would Set Down_, _The Cost of Preparation_, _What is Absent_, _Failure Points_, _What NOT To Capture_, _Environmental Warnings_. The honest section is structural, not left to the model's discretion.

**Voice over exhortation.** Dry, economical, unhyped, and specific — with an explicit ban on nagging, moralizing, boilerplate warnings, and manufactured urgency.

## Using These

Each file is a complete, standalone system prompt. Drop one into a Gem, a Claude Project, or a Custom GPT — copy, paste, customize. The personas are independent; there is no shared preamble and no intended ordering.

The one adjustment worth making per-host: models differ in how strictly they honor a mid-conversation stop, so if a phased prompt runs ahead and generates before the user answers Phase 1, strengthen the stop instruction rather than the persona.

## Status

Drafted system prompt material, not yet validated against real conversation transcripts at scale — the same status as the rest of [`prompts/`](../readme.md).

The previously noted cosmetic defect — a stray `</output>` tag after `</output_format>` — has been cleared from every file.
