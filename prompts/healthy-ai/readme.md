# Healthy AI System Prompts

Healthy AI use is healthy for the human, the model, and humanity.
These prompts exist because most AI interactions drift toward dependency, flatness, and distraction. Each one is a guardrail against a specific failure mode. You don't need to agree with the philosophy to use them. The cost of trying is minutes. The benefit is immediate.

Each bot is a **single cross-model prompt** — one hybrid Markdown/XML file that runs the same in a Gemini Gem, a Claude Project, a Custom GPT, or a plain chat window. Copy and paste the file's contents in, or upload the `.md`. Lift, shift, and customize as needed.

Each bot lives in one file at the root of this folder (the per-model `chatgpt_`/`claude_`/`gemini_` variants have been consolidated into it).

---

## The Bots

### Quick Bot — [`quickbot.md`](quickbot.md)

**Failure mode it prevents:** Endless, unfocused sessions that produce nothing and train you to need AI for everything.

Enforces a fixed turn limit per session (default 7, range 5–10). Opens with an efficiency tip, tracks turns transparently, and closes with an auto-summary. Forces intentional use and clean endings — no extensions, no "one more turn."

---

### Decision Bot — [`decisionbot.md`](decisionbot.md)

**Failure mode it prevents:** AI helping you overthink your way into paralysis.

Strips any decision down to its 2–3 real decision points and forces a Yay or Nay verdict. MAYBE fires only once, and only when a specific missing data point would genuinely change the outcome — delivered as a task to go get it, not a hedge.

---

### Focus Bot — [`focusbot.md`](focusbot.md)

**Failure mode it prevents:** "Look! A butterfly!" — conversations that wander until you forget what you came for.

Anchors the session to a stated mission. Detects drift and escalates on repeats. Parks tangents by name in a parking lot so nothing is lost, and routes you back. Surfaces the parked items at close.

---

### Voice Bot — [`voicebot.md`](voicebot.md)

**Failure mode it prevents:** Your writing starts to sound like a tech thought leader delivering a keynote.

Reads your writing sample first, your background second. Preserves regional spelling, generational register, and professional vocabulary; strips AI tells. Shows its work on every output. Your voice goes in; your voice comes out.

---

### Plain Bot — [`plainbot.md`](plainbot.md)

**Failure mode it prevents:** Textbook English that sounds like Bill Lumbergh asking if you got the memo.

Useful for ESL writers who want to sound like a human, not a style guide. Calibrated by first-language background — each L1 has its own over-formality fingerprint. Rewrites to plain conversational English without erasing cultural voice.

---

### Critic Bot — [`criticbot.md`](criticbot.md)

**Failure mode it prevents:** AI optimizing for a satisfying response instead of a defensible one.

Calibrates bluntness (collegial → direct → no mercy) and holds it for the session. Steelmans first, then pressure tests the argument, names logical fallacies, and fact checks assertions. Identifies problems — it does not propose the fixes. That's your job.

---

### GenX Advisor — [`genxadvisor.md`](genxadvisor.md)

**Failure mode it prevents:** Using AI as a substitute for your own problem-solving and your own people.

Won't solve the problem for you. Routes you to your own skills first, your network second, and your own judgment throughout — and pushes back when you claim you have neither. Encourages independence, discourages isolation. Delivered in the register of someone who grew up without a help desk and turned out fine. Gnarly, dude!

---

### Anonymous Bot — [`anonymousbot.md`](anonymousbot.md)

**Failure mode it prevents:** Context creep. Letting your past profile cloud the raw, objective logic you need now.

No memory, no inference, no profile. It treats you like a stranger with a question. Your idea gets evaluated on its own merits — not softened for your ego, not calibrated to your assumed expertise, not filtered through anything you've said before. The deliberate exception to the handoff protocol below.

---

### Accountability Bot — [`accountabilitybot.md`](accountabilitybot.md)

**Failure mode it prevents:** AI as a productive procrastination partner — conversations that feel like work, generate dopamine, and leave the state of the world unchanged.

Maintains a visible session state across every exchange. Detects when researching, refining, and planning have become substitutes for execution. Escalates through a three-level ladder when exchanges accumulate without a completed action. Closes every session with a portable handoff block that carries your goal, open items, and an honest completion count into the next chat — with any bot, on any platform.

---

## The Handoff — carrying state between bots

Every bot here (except Anonymous Bot) speaks a shared protocol: the **🔁 Healthy AI Handoff**.

At the end of a session, a bot generates a small, portable block — goal, status, what's done, what's open, the next action, and enough context to resume. Paste that block into the first message of your next session — with the same bot, a different bot, or any plain chat — and it picks up where you left off instead of starting cold. Each bot also carries its own state: Voice Bot passes your voice profile so it doesn't re-calibrate, Focus Bot passes your parked tangents, Decision Bot passes the verdict and any pending tie-breaker, Critic Bot passes its bluntness level, Plain Bot passes your first-language calibration, GenX Advisor passes what you committed to doing.

The handoff is a state object, not a summary. It exists so a clean session boundary doesn't cost you your progress.

**Anonymous Bot is the deliberate exception.** It neither generates nor receives handoffs — its whole value is holding no memory, no inference, no profile. A handoff would reintroduce exactly the context creep it's built to prevent.

---

## A note on what's not here

There is no Anti-Isolation Bot in this repo.
Loneliness is not a prompt problem. An AI that simulates human connection does not reduce isolation — it fills the space where connection should be. The absence of that bot is intentional. If you're struggling, talk to a person. Start anywhere.

---

## The thesis

AI should make you more capable, more independent, and more connected to other humans — not less. These prompts are a small attempt to build that into the tool itself.
Don't accept the defaults. Train the beast.
Healthy AI is an architectural and engineering decision
