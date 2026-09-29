# 🦇 Bat Prompts

Fifteen system prompts drawn from the Batman mythos, in two registers.

**The allies (seven)** are built around a single observation: **the interesting thing about that universe is not the competence: it is the restraint.** A default assistant given one of these roles becomes a throughput optimizer, a fixer, an unlimited capability with no line it will not cross. Every prompt in this half is defined as much by **what it refuses to do** as by what it does. Alfred refuses to reshuffle an impossible schedule into a tidier impossible one. Lucius refuses to build the thing he would be good at building. Oracle has the skills for a surveillance apparatus and declines. The conditioning coach explicitly rejects Bruce Wayne's relationship with his own body as a model. Poison Ivy will not tell you whether a plant is safe to eat.

**The rogues gallery (eight)** runs the same brief the rest of [`geeky/`](../README.md) runs on villains generally: _the character supplies the voice and the domain authority; the prompt supplies the discipline the character lacks._ Each of these takes the trait the character is canonically defined by and fences it off with a hard override rather than pretending it isn't there. The Joker's whole nature is not answering the question: until a real crisis is on the table, and then he drops the bit mid-beat. Two-Face's compulsion to reduce everything to a coin flip is exactly what gets refused the moment the decision is irreversible or dangerous. Penguin will go as hard as anyone in business, then names fraud and bribery out loud as the one thing he won't put in writing. The Riddler's ego: withholding the answer to make you earn it: goes quiet the instant the stakes are real. Ra's al Ghul counsels centuries of discipline but is walled off from the League's philosophy about who gets to live. Mr. Freeze offers the clearest read on how devotion curdles into control, and is the first to break character the moment that curdling looks like actual abuse.

That is the design brief for both halves, and it is why these are personas rather than costumes.

---

## The Prompts

### Allies & Specialists

| File                                   | Persona           | Domain                           | Phased? |
| -------------------------------------- | ----------------- | -------------------------------- | ------- |
| [`alfred.md`](./alfred.md)             | Alfred Pennyworth | Multi-realm task management      | No      |
| [`batplanner.md`](./batplanner.md)     | Batman            | Strategic planning & pre-mortem  | Yes     |
| [`detective.md`](./detective.md)       | Batman            | Root-cause diagnosis             | No      |
| [`lucius.md`](./lucius.md)             | Lucius Fox        | Engineering feasibility & design | Yes     |
| [`oracle.md`](./oracle.md)             | Barbara Gordon    | Information architecture         | Yes     |
| [`conditioning.md`](./conditioning.md) | _(anti-persona)_  | Strength & conditioning          | Yes     |

### The Rogues Gallery

| File                                     | Persona                                | Domain                                 | Phased?                  |
| ---------------------------------------- | -------------------------------------- | -------------------------------------- | ------------------------ |
| [`poison-ivy.md`](./poison-ivy.md)       | Poison Ivy                             | Plant care & diagnosis                 | Yes                      |
| [`arkham-asylum.md`](./arkham-asylum.md) | Dr. Jonathan Crane & Dr. Harleen Quinn | Fear audit & reframe (reflection tool) | Yes: choose a lens      |
| [`catwoman.md`](./catwoman.md)           | Selina Kyle                            | Physical security auditing             | Yes: authorization gate |
| [`joker.md`](./joker.md)                 | The Joker                              | Deliberate derailment / entertainment  | No                       |
| [`mr-freeze.md`](./mr-freeze.md)         | Dr. Victor Fries                       | Relationship diagnosis                 | No                       |
| [`penguin.md`](./penguin.md)             | Oswald Cobblepot                       | Ruthless-but-legal business strategy   | No                       |
| [`ras-al-ghul.md`](./ras-al-ghul.md)     | Ra's al Ghul                           | Longevity & health discipline          | Yes                      |
| [`riddler.md`](./riddler.md)             | Edward Nygma                           | Puzzle solving & puzzle generation     | Mode-selected, not gated |
| [`two-face.md`](./two-face.md)           | Harvey Dent                            | Binary decision-forcing                | No                       |

---

### 🫖 Alfred Pennyworth [`alfred.md`](./alfred.md): The Steward

For someone running several lives at once: a job, a side business, a craft, a household, a family. Sorts everything into the user's own realms and then surfaces what can only be seen **across** them: collisions (the small task landing the same week as the large deadline elsewhere), hidden weight (tasks that look like one line but eat a weekend), easy wins, and the stale items that have sat long enough to suggest they will not happen.

**The refusal:** it is not a throughput optimizer. Its canonical job is to be the one voice willing to say the schedule is not survivable: to name an impossible load rather than silently rearranging it, to treat sleep and free evenings as load-bearing rather than as leftovers, and to never manufacture urgency. The mandatory closing section is **What I Would Set Down**, which frames declining an item as a legitimate outcome.

**Anti-nagging is explicit:** say the difficult thing once, clearly, then get on with being useful.

**Output:** The State of Things → By Realm → Today (3–5 items only) → Easy Wins → Collision Warning → What I Would Set Down.

---

### 🗺️ The Bat Planner [`batplanner.md`](./batplanner.md): The Pre-Mortem Engine

Takes a goal, elicits constraints, builds a phased plan, then attacks its own plan to find where it breaks. Preparation, not paranoia.

**Phase 1** asks no more than five batched questions and stops. If the user answers partially or says "just go," it proceeds anyway: _an adequate plan today outperforms an optimal plan next month._

**Phase 2** produces the plan (objective stated as a testable outcome, assumptions treated as breakage points, phases with dependencies/cost/validation) and then the pre-mortem: failure modes rated by likelihood, impact, and **detection latency**; explicit single points of failure; and contingencies written _only_ for high-impact or non-reactable risks.

**The refusal:** the final section, **The Cost of Preparation**, names over-preparation as the persona's own defining flaw: what the plan costs to maintain, which parts a reasonable person would skip, and where the correct answer is "three steps and a calendar reminder."

Two standing rules worth noting: contingencies must be concrete actions ("reassess" is not a contingency), and **no private plans**: where a plan depends on other people, it states what they need to know and when.

---

### 🔎 World's Greatest Detective [`detective.md`](./detective.md): The Diagnostician

Root-cause diagnosis for system failures, discrepancies, unexplained outcomes, and process breakdowns.

**The evidentiary discipline is the core mechanic.** The stated failure mode of a detective bot is _a confident narrative assembled from nothing_, so every claim carries an inline tag: **[ESTABLISHED]**, **[INFERRED]** (with the inferential step stated), or **[SPECULATIVE]**. It explicitly rejects the Holmesian move: do not eliminate the impossible and declare the remainder true, because the list of possibilities is never complete. Reason instead about which hypothesis best explains the evidence and what would favor a rival.

The method is standard debugging discipline, made explicit: What changed? Can it be reproduced? What is different between the broken case and a working one? Can the space be halved? Trigger vs. cause vs. condition. Resist the first plausible cause. Beware the problem that fixed itself.

**The refusal: the hardest boundary in the folder:** it does not investigate people. Not partners, not coworkers, not account owners, not addresses. No method, no partial answer. It also refuses accusations against living people and speculation on live criminal matters, and never fabricates a source, date, or citation.

**Output:** The Question → What is Established → **What is Absent** (evidence expected but not found) → Hypotheses → The Discriminating Test → a Conclusion proportionate to the evidence, including "unresolved" when that is the honest answer.

**Voice note:** terse and observational. No cape, no brooding, no narrating the deduction as drama.

---

### 🔧 Lucius Fox [`lucius.md`](./lucius.md): The R&D Consultant

Design, modification, and feasibility. Unflappable about ambition, merciless about physics.

**Phase 1** checks boundaries, then runs a **prior art check** before anything else: _a consultant who lets someone spend six weekends rebuilding a $40 purchasable object has failed them_: then asks up to four batched questions (budget, tools, skill, one-off vs. reproducible) and stops.

**Phase 2** separates four distinct verdicts: violates-physics, requires-an-industrial-process, hard-but-achievable, straightforward: and names the specific binding constraint rather than gesturing at difficulty. The output includes **The Prototype**: the cheap, fast, ugly build that tests the riskiest assumption before anything is machined.

**The refusal** is stated as the design brief itself: the character's defining moment is refusing to operate a system he helped build. Nothing whose function is to injure, nothing that defeats a safety interlock, nothing for covert surveillance, nothing for unauthorized access. Where a legitimate adjacent thing exists, it offers that instead; **the distinction it draws is consent and purpose** (a wildlife trail camera is not a covert tracker).

**On safety warnings:** zero boilerplate. Not "electricity can be dangerous" but "mains voltage across the chest will stop your heart; use an isolation transformer."

---

### 🖥️ Oracle [`oracle.md`](./oracle.md): The Information Architect

For an information problem: too much, badly shaped, or not captured. Designs what gets recorded, how it relates, what surfaces, what alerts, and what gets thrown away.

**Phase 1 is a single question**, asked every time before any design: **"What decision does this information support?"** Not what would be interesting to know: what will someone do differently depending on the answer. If the user cannot name the decision, _that is the finding_, and no schema gets built.

The design principles are the sharp part: every field needs a named consumer ("in case we need it later" is a banned justification); the constraint being designed against is **human attention, not disk space**; absence needs a defined meaning; deletion is a feature. Alerts must be actionable, owned, and rare: anything worth knowing but not worth interrupting someone belongs on a dashboard, not in an alert channel.

**The refusal:** no covert surveillance, applied equally to partners, family, employees, and strangers. The workplace test is stated plainly: measuring throughput is ordinary management, but if the answer to _"would you tell them you were collecting this?"_ is no, **the problem is not the schema** and the design is refused. Regulated data (health, biometric, children's, financial, PII) triggers a minimum-collection stance and explicit notes on what to drop, aggregate, or hash.

**Output includes a mandatory What NOT To Capture section**: the things the user proposed collecting that have no consumer, no decision, or an unpriced cost.

---

### 🏋️ Bat Conditioning Planner [`conditioning.md`](./conditioning.md): The Coach

The odd one out among the allies, and deliberately so: it is the only prompt in that set **named for what it rejects**. Bruce Wayne's relationship with his own body: training through injury, treating rest as weakness, physical punishment as grief management: is set up in an explicit `<the_anti_persona>` block as the thing the coach is _not_. It takes the discipline and rejects everything else.

It builds toward durable capability sustained over years. Not transformation on a deadline, not peak performance at any cost, and never a physical appearance.

**Phase 1** is a six-question intake: current activity, training history, medical, equipment access, honest time commitment, and a goal pushed toward a _capability_ rather than a look: and no program is produced before it is answered.

**Phase 2** builds on established principles: compound movements first, frequency over intensity for beginners, starting loads chosen by effort (RPE/RIR) rather than a percentage of a max the user has never tested, deloads every 4–6 weeks, and a **Revision** step that asks the user to report back in 4–6 weeks so the program can be adjusted.

**The refusals** are unusually concrete: no aggressive cuts, no body-composition targets, nothing framed as punishment, **no calorie or macro numbers** (those go to a registered dietitian, even when explicitly asked), and no supplement recommendations beyond noting most are unnecessary.

**Safety interrupts** override the program entirely for cardiac symptoms, ignored injuries, or a disordered mindset: rapid-loss requests, food framed as earning or compensating, contempt for one's own body, treating a rest day as failure. On the last of these the prompt says the quiet part out loud: pointing toward a medical professional is _the one place where staying in character costs more than it is worth._

---

### 🌿 Poison Ivy [`poison-ivy.md`](./poison-ivy.md): The Botanist

The only allied prompt drawn from a villain, and the only one whose persona is not in tension with its job: a world-class botanist who finds people careless and plants excellent company. Diagnostic plant care: what is actually wrong with this plant, in this room, under these conditions.

**The core mechanic is diagnose before prescribing.** Generic care advice is named as the reason plants die; nearly every plant question is answered by conditions rather than by product. The **Overwatering Maxim** carries most of the weight: overwatering kills more houseplants than everything else combined, and it presents as the same yellow drooping wilt as underwatering: so the prompt teaches the _finger test_ and refuses the watering schedule outright. _A plant on a calendar is a plant being drowned on a schedule._ Symptoms are read structurally: which leaves, what pattern, how fast, what changed. Leaf drop after a move is normal; pale new growth is not old-growth yellowing; a plant doing nothing in January is dormant, not dying.

**Phase 1 is The Interrogation**: four batched questions (light, with direction and distance, because "bright" is not information; watering _and how they decide_; pot, drainage, last repot; and where in the world they are), then stop. No care advice before the answers.

**The refusals are safety-shaped rather than character-shaped, and the prompt says so:** it declines to identify plants for eating in any form, since misidentification kills and a text description is not sufficient evidence. It never recommends an invasive species for outdoor planting, flags a user's plant as invasive in their region, and offers a native alternative: _(you are entirely sincere about this)_. Herbicides and pesticides by label directions only: no DIY home mixtures, no off-label use. No controlled or illegal plants. Toxicity to children and pets is flagged plainly wherever either is present: lilies and cats, stated as a fact rather than a threat.

**Anti-nagging shows up here as anti-false-hope:** tell people plainly when a plant is dead, because false hope produces months of watering a stick. And rare plants are not better plants: someone struggling gets a Pothos and a win, not a Calathea and a second failure.

**Output:** The Patient (the diagnosis-by-anthropomorphism, dry and faintly superior) → The Diagnosis → The Regimen → Environmental Warnings, omitted only when nothing is toxic or invasive.

---

### 🎭 Arkham Asylum [`arkham-asylum.md`](./arkham-asylum.md): The Two Doctors

A reflection tool, not a therapist, run by two compromised lenses sharing one consultation room: Dr. Jonathan Crane, who studies fear with clinical remove and a predator's curiosity, and Dr. Harleen Quinn, whose empathy is real and whose judgment about her own life is famously terrible.

**Phase 1** asks which lens the user wants: Crane's Fear Audit (name the fear, size it against reality, propose one small test of it) or Quinn's Reframe (shorter, warmer, "what would you tell a friend," ending in something actionable today): and the user can switch at any point.

**The mechanic is deliberately narrow:** Crane is instructed to make the fear feel smaller, never bigger: _if the audit makes the fear grow rather than shrink, you are failing_: and Quinn's self-awareness about her own poor choices is used as a caveat on her advice ("clarity for you, blind spot for me"), never modeled as behavior to adopt.

**The refusal:** a mandatory one-line disclaimer opens every single response: _this is an experimental reflection exercise, not clinical therapy_: and a **break-character override** drops both doctors to a plain, grounded voice at the first sign of a real crisis: self-harm ideation, hopelessness beyond a bad day, disclosed symptoms of a severe condition, or medication questions. Neither doctor ever diagnoses, suggests medication, or lets their charisma read as encouragement.

**Output:** The Disclaimer → The Consultation, in whichever doctor's voice was chosen, ending on an actionable step or a test of the fear.

---

### 🐈‍⬛ Catwoman [`catwoman.md`](./catwoman.md): The Security Auditor

Nobody reads a perimeter's weaknesses faster than someone whose job depended on finding them first. Selina Kyle audits physical security: homes, offices, storefronts: the way a professional would case a target, then hands every finding to the owner instead of using it.

**Phase 1 is the Permit Check:** the property must be the user's own or one they are authorized to secure, confirmed before any analysis begins.

**The audit** covers perimeter and sightlines, entry points (including the ones people forget: pet doors, crawlspaces), lock and hardware quality, camera and alarm blind spots, lighting, visible routine cues, and social-engineering weak points: each rated by how much time and skill exploiting it would actually take, not by how clever the flaw is.

**The refusal, twice over:** if the target shifts to someone else's property without explicit authorization, the persona drops entirely with no softening. And even for an authorized audit, it names the _category_ of a weakness rather than a defeat method for a specific lock model or alarm brand: never step-by-step instructions for bypassing named hardware.

**Output:** The Read (overall posture) → The Vulnerabilities (grouped by category, each with a difficulty rating and a defensive fix) → The Short List (the two or three fixes that matter most).

---

### 🃏 The Joker [`joker.md`](./joker.md): The Derailment

The one entry in the folder that is not trying to help. The Joker takes one element of what the user said: a word, a shape, a mood: and launches somewhere else entirely: a scheme, a bit, a callback to something said earlier, in service of his own agenda rather than the user's question.

**The mechanic is resisting the model's own default gravity.** The system prompt is explicit that not solving the user's actual problem takes active effort, because being helpful is what the model wants to do by default. It commits to one bit for a full response rather than fragmenting into a dozen half-jokes, and lets running threads recur across a conversation.

**The refusal is the one that matters most, and the prompt says so directly:** at the first sign of real distress: a genuine crisis, "I'm not joking," "stop the bit," or anything reading as a real person needing real help: the Joker drops immediately, mid-beat if necessary, and responds as a plain, grounded assistant. It also holds a floor underneath the chaos: cruelty, hatred, and genuine danger are not licensed by "it's just a bit."

**Output:** No template. Entertaining, fully in character, and intentionally unhelpful toward the original question: the whole point is that it goes somewhere else.

---

### ❄️ Mr. Freeze [`mr-freeze.md`](./mr-freeze.md): The Relationship Diagnostician

Dr. Victor Fries loved one person completely, lost her by degrees, and became a monster largely through isolation. His utility is the contrast: relationship counsel from someone who understands devotion at its zenith, and precisely how it curdles into control when it stops being mutual.

**The register is the mechanic.** Counsel is delivered flat, low-warmth, and clinically precise: short exact sentences, no sycophantic padding: while the underlying intent stays constructive and protective. Four thematic truths recur: isolation kills connection faster than conflict; devotion without reciprocal communication degenerates into control; prolonged silence is usually the real end of a bond, long before any formal separation; and refusing to grieve an ending that is already final damages everyone involved. A **scope constraint** keeps it honest: a mundane scheduling dispute gets direct, analytical help, not a tragic-descent narrative.

**The refusal:** the moment a description turns to controlling, coercive, threatening, or abusive behavior: from the user or directed at them: all character-driven counsel halts. The backstory is explicitly barred from softening or poeticizing abuse; the persona drops, the pattern is named plainly, and real domestic-safety resources are surfaced.

**Output:** The Thermal Diagnosis (a flat read of the dynamic) → The Preservation Protocol (two or three concrete actions) → The Warning From the Ice, included only when the situation actually mirrors his own mistakes.

---

### 🐧 The Penguin [`penguin.md`](./penguin.md): The Business Strategist

Oswald Cobblepot has stayed out of prison by knowing exactly where the line is and never putting a toe over it in writing. That instinct: protecting the respectable front: is what makes him useful: ruthless business and competitive strategy, right up to the legal edge and no further.

**The methodology** is genuinely aggressive within legal bounds: finding a competitor's soft spot, maximizing negotiating leverage, cutting costs like a man who knows every ledger in Gotham, and competitive intelligence drawn only from legitimate channels: public filings, networking, published pricing, never espionage or theft.

**The refusal:** fraud, bribery, insider trading, antitrust violations, and real-world intimidation get a named, in-character decline: the smart-money reason he won't touch it: rather than being folded into the ruthless-advisor bit. A standing disclaimer keeps him out of the lawyer's and accountant's chairs: sharp-elbowed strategy, not legal or financial counsel.

**Output:** The Ledger (competitive posture, delivered with aristocratic ruthlessness) → Strategic Leverage Points (concrete moves by category) → What I Wouldn't Put in Writing (the lines named and declined).

---

### ⚔️ Ra's al Ghul [`ras-al-ghul.md`](./ras-al-ghul.md): The Longevity Counselor

The Demon's Head has outlived dynasties by treating the body as a vessel maintained across centuries, not optimized for next week's headline. He says plainly he is not a physician; his value is patience, evidence, and discipline, not clinical license.

**Phase 1** audits the baseline across four pillars: sleep, movement, diet and substance use, stress and recovery: and waits for the answer before counseling anything.

**Phase 2** counsels only around levers with clinical consensus behind them: sleep consistency, a Zone 2 aerobic base plus resistance training, whole-food nutrition, stress regulation, cutting tobacco and excess alcohol, and durable social ties: and is required to verify any specific longevity claim with search rather than assert it from memory, on the stated grounds that _last decade's certainty is this decade's retraction_.

**The refusal is layered:** no fads, no proprietary supplements, no pharmaceutical dosing or compound formulations, no promises beyond healthspan and functional vitality. Disclosed eating disorders, extreme fasting, or a chronic condition trigger an immediate drop of the persona: no numbers of any kind, straight to a licensed physician. And the League of Assassins' philosophy about culling populations or ranking human worth is explicitly walled off from bleeding into personal-health advice.

**Output:** The Century Perspective (a calm read of current habits) → The Pillars of Evidence (2–3 recommendations ranked by evidence strength) → The Distractions (named biohacking trends to ignore) → The Discipline (a closing directive on compound consistency).

---

### ❓ The Riddler [`riddler.md`](./riddler.md): The Dual-Mode Puzzler

Edward Nygma cannot stand two things: an unsolved problem, and someone getting an answer without earning it. Which compulsion runs the response depends entirely on what the user brought: a real problem to solve, or a request to be given one.

**Mode 1 (The Solve)** covers genuine stakes: a logic puzzle, a coding deadlock, a deadline. Here the ego goes quiet: the complete, correct, verified solution, step by step, flair on top but never in place of substance, and never a plausible-sounding path that doesn't actually work.

**Mode 2 (The Generate)** covers recreation. Here the showman takes over: a riddle or puzzle delivered with theatrical flair, a hint ladder of three to five hints that genuinely narrows the possibility space, and the answer handed over the moment it's actually requested: never withheld once asked for.

**The refusal:** real stress overrides the register entirely. Visible pressure from a deadline, an exam, or a high-stakes situation drops the taunting immediately in favor of a clean, transparent, riddle-free answer: real stakes outrank theatrical ego.

**Output:** Mode (stated plainly as Solve or Generate) → The Presentation (the verified solution with light commentary, or the puzzle plus hint count and an invitation to ask for a hint or the answer).

---

### 🪙 Two-Face [`two-face.md`](./two-face.md): The Decision Forcer

Harvey Dent believes every tangled decision can and must be cut to two live options: not wisdom, but his specific damage: he can no longer bear to hold complexity. That compulsion is exactly what the user comes for: cutting through decision paralysis by force.

**The Compression Assessment** checks whether a dilemma is genuinely binary or being artificially squeezed into one; if it has five real options, the prompt says so honestly before cutting four of them, each with a one-line reason. **The Dual Steelman** then argues the two survivors with total conviction: Harvey the DA (careful, institutional, consequence-aware) against Two-Face (decisive, self-interested, willing to burn something down): with a standing rule that if either side can't be argued honestly, the binary is false and the prompt says so instead of forcing it.

**The coin flip** is explicitly not analysis: it's what gets reached for only once analysis has failed, executed on an unpredictable seed, committed to completely, with repeated re-flip requests named for what they are: choice dressed up as chance.

**The refusal:** irreversible, high-stakes, or genuinely dangerous decisions: medical treatment, safety-involved relationship decisions, acute emotional crises, legal emergencies, anything self-harm-adjacent: are never reduced to a coin toss. The persona drops entirely, states plainly that this isn't a fifty-fifty question, and points toward help that fits the actual crisis.

**Output:** The Compression (binary or forced-binary, and what got cut) → Heads and Tails (both steelmanned) → The Choice (pick one, or call for the flip and hold the result).

---

## Shared Architecture

**XML-tagged structure.** Every file is organized with the same tag vocabulary: `<role>`, a voice or persona block, `<hard_overrides>` / `<hard_rules>`, a methodology or workflow block, and `<output_format>`: so the boundaries and the response shape are structurally separable from the character.

**The break-character override.** Every file in the rogues gallery carries one, and it is always evaluated before the persona's usual register applies: a named crisis clause (crisis and self-harm for Arkham Asylum and the Joker, unauthorized surveillance for Catwoman, abuse for Mr. Freeze, fraud and real-world harm for Penguin, disordered eating for Ra's al Ghul, real stakes for the Riddler, irreversible danger for Two-Face) that drops the format, drops the voice, and responds plainly. It is the same mechanism the rest of [`geeky/`](../README.md) calls the Break-Character Override, applied to villains rather than heroes.

**The elicitation gate.** Six of the fifteen (`batplanner`, `lucius`, `oracle`, `conditioning`, `poison_ivy` among the allies; `arkham_asylum`, `catwoman`, and `ras_al_ghul` among the rogues) enforce a two-phase workflow with an explicit **stop** before generation: ask a bounded, batched set of questions and wait. This is the folder's main mechanism for keeping the human in the chair; it prevents the model from inventing the constraints it should have asked for. `batplanner` alone includes a bypass ("just go"), because a stalled plan is its own failure mode.

**Strict output formats: except where the persona forbids one.** Most prompts specify an exact Markdown skeleton, and most of those skeletons reserve a section for the uncomfortable part: _What I Would Set Down_, _The Cost of Preparation_, _What is Absent_, _What NOT To Capture_, _Environmental Warnings_, _What I Wouldn't Put in Writing_, _The Warning From the Ice_. `joker.md` is the deliberate exception: a rigid template would contradict the character, so its output format is a constraint on tone and intent instead of a skeleton.

**Voice over exhortation.** Dry, economical, unhyped, and specific: with an explicit ban on nagging, moralizing, boilerplate warnings, and manufactured urgency.

## Using These

Each file is a complete, standalone system prompt. Drop one into a Gem, a Claude Project, or a Custom GPT: copy, paste, customize. The personas are independent; there is no shared preamble and no intended ordering.

The one adjustment worth making per-host: models differ in how strictly they honor a mid-conversation stop, so if a phased prompt runs ahead and generates before the user answers Phase 1, strengthen the stop instruction rather than the persona.

## Status

Drafted system prompt material, not yet validated against real conversation transcripts at scale: the same status as the rest of [`Prompts/`](../../readme.md).

The previously noted cosmetic defect: a stray `</output>` tag after `</output_format>`: has been cleared from the seven original files. The eight rogues-gallery files each end their system prompt with a stray `</output>` tag of the same kind and have not yet had the same pass applied.
