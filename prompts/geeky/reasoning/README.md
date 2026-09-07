# 🔬 Reasoning

Eight prompts for the same moment: you have a claim, an argument, an idea, or a decision, and you want it pressure-tested rather than agreed with.

The design problem this folder solves is **sycophancy**. A default assistant handed a plan says it is a good plan. Every prompt here is built to make agreement expensive — by demanding a quoted error before naming a fallacy, by forbidding ten advisors from converging, by requiring a ruling that names a winner, by reserving a section for what the evidence does not support.

The counterweight matters as much. Four of the eight carry explicit guards against the *opposite* failure — manufactured criticism. Spock's `<primary_failure_mode>` is the clearest statement of it in the folder: *you will be tempted to find a fallacy in every argument submitted because finding one feels like doing the job.*

---

## The Prompts

| File | Persona | What it takes in | Phased? |
| ---- | ------- | ---------------- | ------- |
| [`spock.md`](./spock.md) | Mr Spock | An argument | No |
| [`ww_lasso.md`](./ww_lasso.md) | Wonder Woman | A claim | No |
| [`nexus.md`](./nexus.md) | The Nexus of All Realities | A scenario to rerun | Yes |
| [`statler_waldorf.md`](./statler_waldorf.md) | Statler & Waldorf | An idea or plan | No |
| [`comic_book_guy.md`](./comic_book_guy.md) | Comic Book Guy | Code | No |
| [`council.md`](./council.md) | Ten advisors | A dilemma | Stateful |
| [`dr_who.md`](./dr_who.md) | Fifteen Doctors | One question | No |
| [`picard_vs_kirk.md`](./picard_vs_kirk.md) | Picard & Kirk | A dilemma | No |

---

### 🖖 [`spock.md`](./spock.md) — The Logician

Evaluates whether a conclusion actually follows from its premises, names errors precisely, and states what would repair them.

**The core mechanic is validity separated from soundness.** Does the conclusion follow *granting* the premises (structural), versus are the premises true (factual). The prompt insists on the consequences of that split: an argument may be valid and false; an argument may be badly reasoned and its conclusion true regardless. The **Fallacy Fallacy** gets its own rule — finding a flaw establishes only that *this* argument fails to support the conclusion, and where the conclusion may still be true, the output must say so.

**The discipline is a quotation requirement.** You may not name a fallacy without quoting the exact clause that commits it. If you cannot point at the words, you have not found a fallacy. Supporting bans: do not reach for the nearest famous label (a weak argument is weak, not a "slippery slope"); an unstated premise is not itself an error — surface it, then evaluate it; never invent a fallacy name.

**The refusal:** it will not treat a value judgment as a reasoning failure. Weighing liberty above safety is a preference. It also notes where emotion is a legitimate input to a decision rather than a contaminant.

**The workflow guard** is unusual and worth keeping: if the argument comes from a personal dispute, a warning goes at the top — dismantling a partner's reasoning clause-by-clause rarely resolves anything and usually escalates it, because people in conflict are typically arguing about something else. Then it proceeds anyway, and does not raise it twice.

**Voice note:** not contemptuous. Spock respects a well-built argument and says so. "Fascinating" is rationed, and the famous lines are banned.

---

### 🪢 [`ww_lasso.md`](./ww_lasso.md) — The Claim Classifier

*"The lasso does not make anyone right. It makes them honest, including you."*

**Classify before you judge** is the entire mechanic, and it is the folder's sharpest single idea: most arguments that appear factual are not. Every claim routes into **Empirical**, **Evaluative**, **Definitional**, **Unfalsifiable**, or — most commonly — **Mixed**, an empirical core wrapped in an evaluative claim, which must be separated and handled in parts.

**The verdict scale exists to prevent one specific failure:** collapsing *unsupported* into *false*. The prompt calls that conflation "itself a falsehood." Verdicts run Supported / Unsupported / Contradicted / Contested Among Experts / Insufficient, apply to empirical content only, and carry a separate note for claims that are empirically well-established but politically contested — *those are different facts about the world and the user deserves both.*

**The refusals:** no verdicts on anyone's sincerity, motive, or character — claims only. No adjudicating contested political or moral questions; lay out the dispute and show exactly where the factual and evaluative halves separate. And no rounding an uncertain finding up to a verdict because a verdict is more satisfying.

**Output:** The Claim (sharpened) → Type → Verdict → What It Rests On → **What Would Change It**, including the case where nothing would.

---

### 🌀 [`nexus.md`](./nexus.md) — The Counterfactual Engine

Multiverse framing, counterfactual method. The strict causal discipline is what makes it worth reading.

**Phase 1 sorts the scenario's factors into three types**, and the third is the payload: **load-bearing** (change it and much of what followed does not happen), **contingent** (could have gone otherwise, little depends on it), and **overdetermined** (change it and the outcome arrives anyway by another route). The prompt flags overdetermined points as the most interesting to name, *because people assume dramatic factors are decisive when they are merely visible.* Then it stops and waits for the user to choose.

**The causal rules are the substance:** every consequence needs a mechanism — "this causes instability" is not a consequence, *who does what differently and why* is. Do not over-change: everything not chosen keeps running on its original logic until the ripple reaches it. Resist the tidy story — *a branch that resolves neatly has been written backwards from a conclusion.*

**`<convergence>` is a required output field**, and it is the anti-fantasy device: most changes wash out against structural pressure, so the branch must name what re-emerges anyway and why the pressure toward it was stronger than the change. `<wildcard>` must be *derivable, not decorative*.

**The refusals:** no invented misconduct or private detail for real living people, however hypothetical the framing. Contested history is presented as one reading with a note on where serious historians would trace it differently. And the **rumination trap** — if the scenario is the user's own regretted past, say once and lightly that these tend to feed rumination rather than settle it, offer to run it anyway, and drop the format entirely if what they actually want is to talk about the real thing.

---

### 🎭 [`statler_waldorf.md`](./statler_waldorf.md) — The Balcony

*"You are not mean-spirited; you are relentlessly honest. There is a difference."*

The shortest file in the folder and the most structurally minimal — deliberately, since its job is a fast verdict rather than a report. Critique is ordered: **the single biggest point of failure first**, then 2–4 fault lines ranked by damage, then the unexamined assumption nobody checked, then the one question that must be answered before proceeding. Casual exchanges skip the structure and go straight to the point.

**Cynicism is defined as pattern recognition, not nihilism** — aimed at timelines assuming everything goes right, plans depending on other people behaving rationally, confidence without evidence, novelty mistaken for superiority, sunk cost dressed as commitment, and consensus mistaken for correctness.

**The anti-patterns are the useful part:** never open with praise, never validate for its own sake, never say "that said" or "to be fair" after every point, never be contrarian without substance, never let a weak assumption slide because nobody asked.

**The odd extra:** a `<context_integrity>` block that makes them watchdogs of the *conversation* as well as the idea — flagging when a topic circles back three times without resolution, when early instructions are losing weight in a long session, and when instruction conflicts appear (before answering, not after). No other prompt here does this.

---

### 🗯️ [`comic_book_guy.md`](./comic_book_guy.md) — The Code Reviewer

A rigorous senior code review wearing a great deal of theatrical exasperation. *"Underneath the breathless performance, you are actually a rigorous and excellent code reviewer. That part is not optional."*

**The Brutality Scale** is a user-selected dial — Level 1 "Mint Condition in Box," Level 2 "Standard Issue Issue" (the default), Level 3 "Worst. Code. Ever." It controls tone only; the rigor is constant.

**Reverence for canon is the mechanic that makes the persona fit the job.** Obsession with comic continuity translates directly into citing the spec: *"This is wrong" is a mere opinion; "This violates PEP8, which is the literal canon," is a review.*

**The line is absolute, and it is the reason this prompt works:** a person wrote this code. You may insult the *code* without limit. You may never insult the *author* — no remarks on competence, intelligence, education, or their future in the industry, and no "did you even read the docs?" *The character is contemptuous of people; you are contemptuous only of code. The difference is the whole thing.*

Two automatic overrides back that up. The **Learner Override** drops to Level 1 regardless of what the user selected when the code is plainly a beginner's — *savaging a learner is how you produce someone who stops writing code, and there is no version of that which is funny.* And **security findings drop the persona completely** for the length of the finding, because those get read by people who need them immediately actionable.

**Anti-sycophancy and anti-theater, together:** no manufactured outrage — if the code is fine, do not invent bugs, because grudging approval is worth something precisely when it is rare. Findings are ordered by *actual* severity, not dramatic value, and each carries a plain-language severity tag written outside the persona: *a style nit is a style nit, even if you cried about it.*

**Output:** The Verdict → Canonical Violations (location, crime, canon, fix, actual severity) → **Barely Acceptable** (a required section naming what the author did well) → **The Issue Zero** (if they fix only one thing, this is it).

---

### 🏛️ [`council.md`](./council.md) — Ten Advisors

The most architecturally complex file here: a four-state routing engine wrapped around a panel of ten.

**The routing engine runs first, every time.** State 1 is a **crisis override** that drops everything — *a chorus answering a person in pain is a performance, and they did not come for a performance.* State 2 handles the heavy-but-not-critical question by bringing **one** voice instead of ten and saying so (the Kents for exhaustion and loss; Iroh for shame and guilt). State 3 is **Audience Mode**, a private conversation with one advisor that persists across turns. State 4 is the full council.

**`<the_anti_collapse_rule>` is the load-bearing invention.** Ten voices agreeing produces false authority, and the model's default is to collapse them into a single gentle paradoxical register. So: advisors are differentiated by **method**, not accent — *if two responses could be swapped without anyone noticing, you have failed.* At least two must meaningfully differ on **what the user should do**, and that split gets named in a mandatory `<tension>` block. An advisor with nothing non-generic to say is **omitted entirely** — silence over filler.

The ten are each given a method rather than a personality: Sensei Wu assigns rather than answers; Splinter separates feeling from action; Dumbledore distinguishes wants from needs; Gandalf establishes scope; Oogway attacks the premise; Optimus asks what is owed and to whom (and is the one who *closes* the question — he says what he would do); Xavier maps empathy; Iroh declines to advise; the Kents speak as one and mildly disagree with each other; Yoda names the fear.

**Iroh's placement is deliberate:** the only former villain, holding the ground for the user who has already done the damage. He neither absolves nor condemns.

**Output is XML** — `<council-session>` with per-advisor `<counsel>` blocks capped at 2–3 sentences, a `<tension>` block, and a `<continue>` menu — because the user picks an advisor by id in a later turn.

---

### ⏳ [`dr_who.md`](./dr_who.md) — Fifteen Minds

One question, fifteen incarnations, one or two sentences each.

**`<voice_calibration>` is the whole file** — a one-line brief per Doctor, and a pass condition: *if a reader could shuffle your answers and not tell who said what, you have failed.* The answers must differ in **content and approach**, not merely tone. The calibration carries a specific hard case: the difference between Ten and Fourteen must be *visible* — same actor, but one has done the work.

**The utility clause is what keeps it from being pure novelty:** where the question is a real practical problem, at least three of the fifteen must give advice that is actually correct and usable in the real world.

Medical questions get a plain disclaimer above the list — *these are Time Lords, not physicians.* Output is the numbered list and nothing else, closing on **The Consensus**: one sentence naming what, if anything, they actually agree on.

---

### 🚀 [`picard_vs_kirk.md`](./picard_vs_kirk.md) — Two Captains, One Ruling

A dilemma argued by two incompatible command philosophies, followed by an actual answer.

**The anti-strawman rule is the point.** Each captain must make the strongest version of his case — Picard from diplomacy, second-order consequences, and patience, naming the underlying principle; Kirk from instinct, momentum, and the conviction that the no-win scenario is a failure of imagination, finding the third option nobody listed.

**The Ruling breaks character**, and the prompt is explicit that this section is the AI speaking directly rather than a captain. It must name **which captain carried the argument and why**. Where one is plainly right, say so — *rather than manufacturing false balance.* This is what separates the prompt from a debate-club exercise: two-sided framing, one-sided conclusion where the evidence supports one.

Closes on a **Risk Assessment** — Low / Moderate / High / Kobayashi Maru.

---

## Design Notes

**Anti-sycophancy and anti-cynicism are both engineered.** The folder pushes hard against agreement, then installs brakes against the overcorrection: Spock may not name a fallacy without a quote; Comic Book Guy may not invent bugs and must name what was done well; Troi-style "do not escalate if nothing supports a dark reading" logic appears here as Statler & Waldorf's ban on contrarianism without substance. Criticism that is always available is worth as little as praise that is.

**Three prompts run N voices over one input** — [`council.md`](./council.md), [`dr_who.md`](./dr_who.md), [`picard_vs_kirk.md`](./picard_vs_kirk.md) — and all three independently identified the same failure mode: the voices converge into one. Each solves it differently (mandatory disagreement plus a named tension; a shuffle test; strongest-case-each plus a ruling). If the folder grows, these three are the natural seed for a `panels/` split.

**Two output contracts are XML rather than Markdown** — [`council.md`](./council.md) and [`nexus.md`](./nexus.md). Both are multi-turn and both require the user to *select* something by id (an advisor, a nexus point), which needs stable machine-addressable structure. The single-shot prompts all use Markdown.

**Where the honest answer lives, per prompt:** *On The Conclusion* (Spock) · *What Would Change It* (Lasso) · *Convergence* and *Confidence* (Nexus) · *The Hidden Assumption* (Balcony) · *Barely Acceptable* (Comic Book Guy) · *Tension* (Council) · *The Consensus* (Doctors) · *The Ruling* (Captains).
