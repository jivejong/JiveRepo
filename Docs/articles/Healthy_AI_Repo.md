## AI Prompts That Refuse to Think For You

Here's the uncomfortable thing I noticed after a few hundred hours of working with AI tools: the default mode is flattery and resolution. You bring a problem, the AI converges on an answer. You bring a half-formed thought, it completes the thought. You bring a contradiction, it finds the synthesis.

All of that feels helpful. Most of it is actually cognitive outsourcing.

I've spent thirty years in data engineering and information systems, long enough to watch a lot of "paradigm shifts" arrive, overpromise, and then settle into whatever they actually were. AI is genuinely different in capability. But the failure mode isn't new: a powerful tool used passively makes you weaker, not stronger. A spell-checker that autocompletes your sentences eventually owns your sentences.

So I built something to fight the pull.

What I built, and why the framing matters
This repo is a collection of system prompts built for cognitive augmentation. Everything in it is built around a single organizing test: is the AI expanding what you can do, or replacing what you can do?

That's a more precise test than "never let the AI close anything." Sometimes closure is exactly what's needed — a session that drags, a decision that keeps cycling, a plan that never gets committed. The failure isn't always premature resolution. It's just as often drift, paralysis, and dependency dressed up as thoroughness. The repo holds both failure modes, because healthy AI use has to.

What this repo is specifically not built for: AI interactions where the goal is receiving a better-packaged version of what the AI already concluded. The AI has a constant gravitational pull toward resolution, toward synthesis, toward recommendation, toward closure. Every prompt in this repo is either fighting that pull or channeling it — depending on which failure mode it's designed to address.

The seven areas (and the failure mode each one is watching)
Healthy AI bots — the foundation, and the exception. Eight prompts that fight dependency failure modes from the opposite direction of everything else in this repo. Where most prompts here hold things open, these force closure. QuickBot enforces a turn limit and ends sessions cleanly. DecisionBot strips to 2–3 real decision points and demands a verdict. FocusBot anchors to a stated mission and routes you back when you drift. VoiceBot reads your writing sample first and your background second, then preserves what's yours instead of smoothing it into the generic professional register.

They belong here because they're fighting the same root problem — AI use that makes you less capable over time — but the failure modes they're targeting are different. Drift, paralysis, and endless sessions that produce nothing are just as corrosive as premature synthesis. These prompts are the counterweight.

Inward tools — perspective multiplication pointed at you. Eight prompts for surfacing your own patterns: the metaphors you reach for without noticing, the values revealed by your actual tradeoffs rather than the ones you'd claim, the contradictions you hold simultaneously. The guardrail in every inward tool is the same: it reflects a pattern back as a hypothesis to confirm or reject, never as a verdict about who you are. None of these are therapy. Each one has a hard stop that drops the format and responds directly if real distress surfaces.

Outward tools — perspective multiplication pointed at something you're working on. Seven prompts for examining an idea, decision, or plan from angles you wouldn't naturally occupy. Board of Advisors simulates a panel of genuinely distinct voices and explicitly refuses to let the synthesis converge into a recommendation — it maps where voices diverge instead of resolving the tension. PremortemBot runs Gary Klein's actual premortem technique: stipulates the plan already failed, asks why. The mechanism that makes this work (past tense, not speculative future tense) is the one detail most AI implementations drop. This one doesn't.

Process tools — real pedagogical structures, held to their actual mechanics. Six prompts built from the instructional design literature: Socratic Circle, Think-Pair-Share, Cognitive Apprenticeship, Project-Based Learning, Inquiry-Based Learning, Case-Based Learning. Each one ships with a fidelity checklist — a transcript-scoring instrument built from the prompt's own guardrails plus the literature. The central failure mode here is compression: a named pedagogy gets reduced to its most quotable feature while losing the structural details that make it hard. "Socratic method" gets implemented as "AI asks questions." That's not it. The actual Socratic Circle has an inner ring and an outer ring with different jobs; the peers in the inner ring are supposed to genuinely disagree with each other. The prompt holds that.

Code Dojo — apprenticeship for programmers, in two editions. Four bots (The Defense, The Mirror, The Forms, The Watch), each borrowing from a real Eastern pedagogical tradition. The chat/ edition reasons about your code; the code/ edition runs it. The difference is load-bearing: in chat, you're still the authority on whether a counterexample is real; in code, the runtime is. The Watch is the one that surprises people — its deliverable isn't better code, it's self-knowledge about where your automatic solving hides your gaps from you.

Training pipeline — three partners named after the ML lifecycle they mirror: pre-training, training, post-training. Not three tutors sharing a theme; one pipeline with typed handoffs that closes into a loop. Pre maps a new domain onto what you already know. Training preserves learning friction — minimal path forward, not full solutions. Post builds the retrieval schedule that actually beats the forgetting curve. A project partner reinforces learning through hands-on work. The loop closes when post hands the remaining gaps back to pre as input for the next cycle.

Writing — two kinds of prompts: corrective and collaborative. Corrective to fix what came out wrong. Collaborative to write alongside you. Every collaborative assistant inherits the same Collaboration Discipline: identify before you generate, offer directions instead of verdicts, keep the writer in the chair, monitor your own drift, and finish what you start. A tool that writes for you produces text and a dependency. A tool that writes with you produces text and a writer who got sharper doing it.

The failure mode that runs underneath all of it
Every section above is fighting a version of the same thing: the AI quietly doing the cognitive work you were supposed to do.

In Board of Advisors, it's the synthesis converging into a recommendation when it should map tension instead. In Cognitive Apprenticeship, it's the Coaching phase relapsing into a second Modeling performance the moment you struggle. In the Code Dojo, it's narrating the traceback instead of letting you read it. In each case, the intervention feels like helping. It isn't.

This is the shape of the failure that matters. Not hallucination, not bias, not the other things people usually worry about. The quiet cognitive takeover — the one that makes you slightly less capable with every session because the work kept happening without you.

What I didn't build

There's no Anti-Isolation Bot in this repo. Loneliness is not a prompt problem. An AI that simulates human connection doesn't reduce isolation — it fills the space where connection should be. The absence of that bot is intentional.

What this is actually for
If you use AI the way most people use it, this repo probably isn't what you're looking for. Most people want faster answers, better drafts, cleaner code. That's legitimate. There are excellent tools for that.

This repo is for people who've noticed the dependency starting to form. Who've caught themselves reaching for AI before reaching for their own judgment. Who want to use the tool without the tool using them back.

Healthy AI use is an architectural decision. You have to build it in deliberately, because the defaults are pointed the other way.

The repo is linked below. Everything is a system prompt you can drop into a Claude Project, a Gem, a Custom GPT, or wherever you work. Read the top-level README first — it has the organizing test that ties everything together.

GitHub link to repo
