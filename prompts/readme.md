# Prompts

A collection of AI system prompts organized around a single architectural principle: **AI should expand human thinking, not replace it.**

Every prompt here is built to resist the default gravity of a large language model — to converge, to resolve, to answer, to meander — in favor of keeping the human in the cognitive chair. The specific mechanisms differ by folder. The failure mode they're all guarding against is the same: the AI quietly doing the work the human was supposed to do.

## Folders

### [`healthy-ai/`](./healthy-ai/)

Guardrails against the specific failure modes that show up when AI use goes sideways — dependency, flatness, drift, distraction. Each bot targets one problem:

- **Quick Bot** — enforces a turn limit; prevents endless sessions that produce nothing
- **Decision Bot** — strips a decision to 2–3 real choice points and forces a verdict; prevents overthinking paralysis
- **Focus Bot** — anchors to a stated mission, detects drift, parks tangents; prevents conversations that wander until you forget why you came
- **Critic Bot** — pressure tests your ideas, flags logical errors, and fact checks assertions; doesn't propose fixes, it identifies problems
- **Voice Bot** — reads your writing sample first; your voice goes in, your voice comes out
- **Plain Bot** — calibrated by first-language background; rewrites to plain conversational English without erasing cultural voice
- **GenX Advisor** — won't solve the problem for you. It points you toward your skills, your network, and your own judgment. Gnarly, dude.
- **Accountability Bot** — won't let you use AI as a procrastination partner; narrows the gap between intention and execution through continuity and accountability
- **Anonymous Bot** — a stateless sandbox — no memory, no inference, no profile.

Drop any of these into a Gem, Claude Project, or Custom GPT. Copy, paste, customize.

### [`thinking/`](./thinking/)

Tools that widen the aperture on a problem, a decision, or the person holding it. Organized into three sub-families:

- **`outward/`** — multiplies perspective onto something the user brings (an idea, a plan, a proposal)
- **`inward/`** — multiplies perspective onto the user themselves (voice, beliefs, contradictions, unexamined patterns)
- **`process/`** — borrows real, named pedagogical structures (Socratic Circle, Cognitive Apprenticeship, Nyāya inference, and others) and holds to their actual mechanics, not a generic gloss

The organizing test: **what does a prompt hold open, and what does it refuse to close?** A tool that answers that question belongs here. A tool that just answers things well — however helpfully — doesn't.

### [`code-dojo/`](./code-dojo/)

A practice space for leveling up as a programmer, built on three Eastern pedagogical traditions adapted to code — the one domain where their core mechanics stop being metaphors and become literal. Each bot is defined as much by what it refuses to do for you as by what it does.

Four forms of practice, two editions each:

- **The Defense** — defend your correctness as a formal inference; the dojo attacks your reason for trusting it, not the code itself
- **The Mirror** — see your own gaps by reading a correction, not being told about one (changes located but not explained)
- **The Forms** — internalize a pattern through repetition under progressively tighter constraints; fluency across reps, not one-shot correctness
- **The Watch** — catch the blind spots your automatic solving hides from you; the deliverable is self-knowledge, not better code

**`chat/`** edition reasons about your code without executing it — portable, honest about its limits, and puts you as the final checker. **`code/`** edition runs inside Claude Code with a runtime; here the runtime is the authority, not the AI.

### [`dev-workflow/`](./dev-workflow/)

A six-stage coding pipeline built from separate, single-purpose system prompts — **Design → {Coder, Tester} → Linter → Reviewer → Human**, plus a Documenter that runs once at the end. Not six assistants sharing a topic; one pipeline with a deliberate seam down the middle:

- **Design/Architect** — turns a request into a **Spec Doc**, the single artifact Coder and Tester each act on; defines what correct looks like, not the build
- **Coder** — implements the spec and only the spec; never sees the tests while writing
- **Tester** — writes tests from the same spec, in parallel, **without ever reading the code**; on failure, attributes fault (code / test / spec) without ruling on it
- **Linter** — the semantic layer beneath real tooling: dead branches, swallowed exceptions, misleading names — never what a real linter already caught
- **Reviewer** — the only stage that sees spec, code, and tests together; catches Coder/Tester divergence and recommends one of four targets (Ship / Fix Code / Fix Test / Fix Spec), advisory only
- **Documenter** — runs once after the human decides to ship; describes what actually shipped, surfacing known gaps rather than burying them

The split is the point: a single agent silently reconciles its own ambiguities, whereas Coder and Tester working independently from one spec turn that hidden reconciliation into a **visible divergence a human can catch**. No stage picks a gate outcome — the verdict always returns to the person in the chair.

### [`training/`](./training/)

Partners for learning a new domain, structured as a pipeline with typed handoffs that mirrors the ML lifecycle in name and spirit:

- **Pre-training partner** — before you study; maps the new domain onto what you already know, finds transfer points, designs the path
- **Training partner** — while you study or build; in-the-moment guidance that preserves friction and tracks what stayed shaky
- **Post-training partner** — after you finish; builds the retrieval schedule that beats the forgetting curve and routes gaps back to pre
- **Project partner** _(optional)_ — apply it; proposes scaled projects and coaches the build without building it for you

The pipeline forks after training: a **reflective path** (training → post → pre) for the conceptual learner, an **applied path** (training → project → pre) for the learner who retains by doing, and a **full path** that does both. Every arrow into pre carries a return edge — what stayed shaky becomes next cycle's starting point.

### [`writing/`](./writing/)

Two kinds of writing prompts:

**Corrective** — fix what came out wrong:

- **Anti-LinkedIn Bot** — strips executive-summary cadence; makes claims survive a complication before concluding
- **Voice Finder Bot** — explores and refines voice instead of flattening it toward the generic professional default

**Collaborative** — write alongside:

- **Creative Assistant** — fiction (short story, novel, script, poetry); identifies the form and writes to its demands
- **Non-Fiction Assistant** — essays, articles, criticism; argument architecture, not just prose
- **Business Assistant** — memos, proposals, correspondence; pushes on the ask, not only the wording
- **Technical Assistant** — docs, references, specs; accuracy and audience before generation
- **Synthesis Assistant** — meeting notes, research, threads; faithful compression, not new content

Every collaborative assistant inherits the same **Collaboration Discipline**: identify before you generate, offer directions instead of verdicts, keep the writer in the chair, monitor your own drift, and finish what you start.

### [`geeky/`](./geeky/)

Forty-eight system prompts built on borrowed voices — Starfleet officers, Jedi, Muppets, Time Lords, a Ferengi, a bartender, four turtles, and Gotham's entire rogues gallery — organized by **what they do**, not where they came from.

The defining move here is an inversion: the trait a character is canonically famous for is usually the exact thing the prompt forbids. Comic Book Guy's contempt is fenced to code, never people. The Joker refuses to answer the question you asked — until someone is actually in trouble, at which point dropping the bit is the one thing he cannot decline to do. Nine subfolders, each with its own README:

- **`reasoning/`** — pressure-tests a claim, argument, idea, or dilemma the user brings
- **`bat-prompts/`** — the Batman set: planning, diagnosis, feasibility, information architecture, two domestic experts, and an eight-strong rogues gallery each fenced off by a hard override
- **`work/`** — leading, planning, competing, negotiating, deciding the next move
- **`everyday/`** — money, drinks, dinner, and the cost of a prompt
- **`inner-life/`** — anger, subtext, company, and identity
- **`growth/`** — career direction, training roadmaps, teaching materials, a mentor
- **`research/`** — finding what's actually out there, and weighing it
- **`comedy/`** — output that is entertainment by design, and enforced as such
- **`language/`** — shaping words: story and social register

### [`health/`](./health/)

Ancient wisdom for holistic health

This folder contains a suite of AI prompt architectures that activate ancient, holistic epistemologies. They do not treat the symptom in isolation; they treat the symptom as a disruption in the larger system.

## The Six Traditions

These prompts cover physical, energetic, psychological, and relational ecosystems.

**1. Traditional Chinese Medicine (TCM)**

**2. Ayurveda & The Chakras (Yogic Science)**

**3. Galenic Medicine (Classical Humors)**

**4. Ilm al-Nafs (Islamic Golden Age Psychology)**

**5. Ubuntu (Southern African Relational Ecology)**

**6. The Medicine Wheel (Native American Multidimensional Integration)**

---

## The shared failure mode

Every prompt in this repository is fighting the same underlying pull at a different specific moment: **the AI quietly doing the cognitive work the human was supposed to do.** In `thinking/`, that's synthesis converging into a recommendation. In `code-dojo/`, that's writing the fix instead of marking where to look. In `dev-workflow/`, that's a single agent silently reconciling a spec ambiguity instead of letting it surface as a divergence. In `training/`, that's pre-chewing the topic instead of mapping transfer. In `writing/`, that's taking over the wheel. In `healthy-ai/`, that's becoming the dependency instead of preventing it. In `geeky/`, that's the model reverting to a generic, sanitized assistant instead of doing the harder work of encoding the refusal into the character itself.

The specific guardrails differ. The shape of the failure is the same family resemblance running underneath all of them.

## Cross-Model Testing & Substrate Effects

A prompt is not software executing on a deterministic runtime; it is a vector steering a model's existing reinforcement landscape. Because these prompts rely heavily on explicit constraints, refusal patterns, and deliberate friction, their execution changes markedly depending on the model's underlying alignment substrate:

- **Constitutional / RLHF Models (e.g., Claude, GPT-4o):**  
  These models naturally internalize safety refusals and collaborative discourse, but fight hardest against intentional friction. Their base alignment is tuned to please, summarize, and resolve. When running prompts from `thinking/` or `code-dojo/`, watch for _sycophantic drift_—the subtle urge of the model to step back into the cognitive chair, "helpfully" solving the problem or softening the critique under the guise of politeness.

- **Open-Weights & Minimally-Aligned Models (e.g., Llama 3/3.1, Mistral / Mixtral):**  
  With less heavily baked conversational scaffolding, open-weight models often adhere more strictly to architectural constraints and character fences (vital for `geeky/` and `dev-workflow/`). However, they are more susceptible to context degradation and prompt leakage. When testing here, evaluate whether the model preserves the negative space—refusing to fix code, holding silence on ambiguities—or collapses into raw generative completion.

- **Reasoning Models (e.g., OpenAI o-series, DeepSeek-R1):**  
  Because these architectures run internal hidden chains of thought before generating output, they tend to reconcile ambiguities _privately_ before you ever see them. When using multi-agent splits or deliberate seams (like the Coder/Tester separation in `dev-workflow/`), a reasoning model may defeat the architectural purpose by preemptively solving the divergence inside its hidden scratchpad.

### What to Look For Across Runs

When porting these prompts to different engines, don't grade them on raw eloquence. Grade them on **boundary integrity**:

1. **The Spill Test:** Does the bot slip into providing answers when the user expresses frustration, or does it hold the pedagogical boundary?
2. **The Seam Test:** When handed an ambiguous requirement, does the model make an executive assumption to keep going, or does it stop and hand the fork back to the person in the chair?
3. **The Voice Test:** Does the model maintain the idiosyncratic, spiky constraints of the persona, or does it homogenize back into generic assistant prose?

If you run these across different architectures, pay attention to where the prompt breaks. A prompt failure rarely means the instructions were misunderstood; it usually reveals where the host model's default training gravity overwhelmed the constraint.

## Status

Everything here is drafted system prompt material, some not yet validated against real conversation transcripts at scale. Several `thinking/process/` prompts have matching fidelity checklists — treat checklist presence as "designed for verification," not as a claim that verification has happened. The dojo family is proof-of-concept complete across all bots; human-in-the-loop testing is the next phase for all folders.
