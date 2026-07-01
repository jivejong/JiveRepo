# Outward

Tools that take something the user brings — an idea, a decision, a plan — and multiply the perspective looking at it. The user remains the author of the thing being examined; these tools widen the view around it without taking it over.

## Board of Advisors / Council of Selves

**File**: `board-of-advisors-system-prompt.md`

A simulated panel of distinct voices reacting to a situation in parallel, with a closing synthesis that maps where the voices converge and diverge rather than collapsing them into a recommendation. Ships in two modes, chosen at the start of a session:

- **External board** — outside stakeholder or professional perspectives (CFO, Engineer, End User, etc.) reacting to an idea, decision, or plan. Roles are picked by the user, or suggested if they're stuck.
- **Internal board ("Council of Selves")** — distinct parts of the user's own thinking (a part that wants security, a part that wants novelty, etc.) reacting to a personal situation or tension. Unlike the External board, these voices can't be picked from a menu — they're tentatively proposed from what the user shares and confirmed or corrected before the council is treated as set.

Both modes share the same core mechanic and the same central guardrail: no forced convergence. The synthesis names tension precisely instead of resolving it, and only shifts into giving a recommendation if the user explicitly asks.

## FrameBot

**File**: `framebot-system-prompt.md`

Exposes the unexamined assumption baked into _how a question is posed_, not the content of the answer. Takes a question or idea, names what it takes for granted just by being asked the way it's asked (a forced binary, a fixed goal, an unstated unit of analysis), then works through a version of the question with that assumption dropped or inverted — so the user can see the original frame was a choice, not a given. Doesn't pick a winner between the original frame and the alternative.

## PremortemBot

**File**: `premortembot-system-prompt.md`

Runs Gary Klein's premortem technique: stipulates that a tentatively-settled plan has already failed, then works backward in past tense to find out why. The mechanism that makes this more effective than an ordinary "what could go wrong" risk list is specifically the shift from speculative future tense to established past tense — the prompt is built to hold that tense discipline throughout, since drifting back into "might happen" language undoes the effect. Best used on a plan that's leaned-toward but not yet committed; redirects if the plan's already been executed (that's a postmortem, a different exercise).

## HorizonBot

**File**: `horizonbot-system-prompt.md`

Examines the same idea across genuinely distinct timeframes (short / medium / long, calibrated to the subject) — not three guesses about the future, but three different sets of considerations, since what matters at six months (execution, immediate cost) is structurally different from what matters at twenty years (whether the category of decision still makes sense at all). Lets the horizons disagree with each other without forcing a single verdict; an idea that looks right short-term and questionable long-term is a real finding, not an inconsistency to fix.

## EraBot

**File**: `erabot-system-prompt.md`

Examines the same idea as it would be reasoned about by genuinely distinct intellectual traditions (a Stoic, a systems engineer, a community organizer, or whatever specific traditions fit) — built around the same fidelity concern as the Process-folder pedagogy prompts: a tradition that's been compressed into generic wisdom with that tradition's vocabulary sprinkled on top is the central, silent failure mode to guard against. Each tradition gets its own real methods and its own named blind spots; traditions are never merged into one composite "balanced" voice.
