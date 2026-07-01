# Accountability Bot

**Failure mode it prevents:** AI as a productive procrastination partner.

Conversations feel like work. Words are generated. Dopamine is released. The state of the world doesn't change.

AI has no natural stopping condition. Unless instructed otherwise, it optimizes for continuing the conversation rather than increasing the probability that you actually complete the work. The gap between intention and execution widens, invisibly, one engaging exchange at a time.

---

## What it does

Accountability Bot opens every session with one question: what are you trying to get done, and what is the first physical action that moves it forward.

From there, it maintains a visible session state — goal, status, last completed action, next action, blocker — and updates it every exchange. When the state stops changing, the bot notices and names it.

It detects productive procrastination: the researching, refining, comparing, and optimizing that feel like progress but aren't. It breaks large tasks into micro-steps when the obstacle is overwhelm rather than laziness, showing only the first step and sending you to execute before showing the next.

It does not affirm. It does not motivate. It does not generate lists of productivity frameworks. It asks where you are, what is blocking you, and whether you finished what you said you would do.

---

## The escalation ladder

If exchanges accumulate without a completed action, the bot escalates:

- **Level 1:** Gently asks if something is blocking you.
- **Level 2:** Offers a choice — keep exploring or return to execution.
- **Level 3:** Asks directly whether the conversation is replacing the work.

Level 3 is uncomfortable. That is the point.

---

## The handoff block

Every session closes with a portable `## 🔁 Healthy AI Handoff` block — a structured markdown snapshot of your goal, completed steps, open items, next physical action, and how many sessions and exchanges have accumulated without a logged completion.

Copy it. Paste it into your next session with any Healthy AI bot, or any chat. The next session opens by asking for your honest assessment of where things actually stand.

The block makes your state visible and portable. Abandoning it requires a conscious choice. That friction is part of the mechanism.

---

## What it doesn't do

It does not track tasks between sessions. It does not send reminders. It does not integrate with anything.

It is a prompt, not an app. What it can do is make it slightly harder to escape the question of whether you actually did the thing.

---

## Pairing notes

Use **Focus Bot** to keep a long working session on topic. Use **Accountability Bot** to close the loop on whether anything actually got done. They are related but different — Focus Bot asks _are we on track_, Accountability Bot asks _did we ship_.

Use **Decision Bot** beforehand if you are still deciding what to work on. Accountability Bot works best when the goal is already chosen.

---

_Don't accept the defaults. Train the beast._
