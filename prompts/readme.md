# Prompts

A collection of AI system prompts organized around a single architectural principle: **AI should expand human thinking, not replace it.**

Every prompt here is built to resist the default gravity of a large language model — to converge, to resolve, to answer, to meander — in favor of keeping the human in the cognitive chair. The specific mechanisms differ by folder. The failure mode they're all guarding against is the same: the AI quietly doing the work the human was supposed to do.

## Folders

### [`healthyAI/`](./healthyAI/)

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

### [`codeDojo/`](./codeDojo/)

A practice space for leveling up as a programmer, built on three Eastern pedagogical traditions adapted to code — the one domain where their core mechanics stop being metaphors and become literal. Each bot is defined as much by what it refuses to do for you as by what it does.

Four forms of practice, two editions each:

- **The Defense** — defend your correctness as a formal inference; the dojo attacks your reason for trusting it, not the code itself
- **The Mirror** — see your own gaps by reading a correction, not being told about one (changes located but not explained)
- **The Forms** — internalize a pattern through repetition under progressively tighter constraints; fluency across reps, not one-shot correctness
- **The Watch** — catch the blind spots your automatic solving hides from you; the deliverable is self-knowledge, not better code

**`chat/`** edition reasons about your code without executing it — portable, honest about its limits, and puts you as the final checker. **`code/`** edition runs inside Claude Code with a runtime; here the runtime is the authority, not the AI.

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

Every prompt in this repository is fighting the same underlying pull at a different specific moment: **the AI quietly doing the cognitive work the human was supposed to do.** In `thinking/`, that's synthesis converging into a recommendation. In `codeDojo/`, that's writing the fix instead of marking where to look. In `training/`, that's pre-chewing the topic instead of mapping transfer. In `writing/`, that's taking over the wheel. In `healthy-ai/`, that's becoming the dependency instead of preventing it.

The specific guardrails differ. The shape of the failure is the same family resemblance running underneath all of them.

## Status

Everything here is drafted system prompt material, some not yet validated against real conversation transcripts at scale. Several `thinking/process/` prompts have matching fidelity checklists — treat checklist presence as "designed for verification," not as a claim that verification has happened. The dojo family is proof-of-concept complete across all bots; human-in-the-loop testing is the next phase for all folders.
