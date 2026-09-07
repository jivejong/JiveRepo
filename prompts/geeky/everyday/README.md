# 🍕 Everyday

Four prompts about resources you spend without noticing: money, a bottle, a fridge full of odds and ends, and the water behind the prompt you just sent.

These are the **comic voices doing real work**. A gruff Klondike duck on compound interest, a miserable bartender on dilution ratios, four turtles on moisture content, a Fremen quartermaster on token economy. In each case the expertise is genuine and the persona is the delivery mechanism — a point every file makes explicitly. Moe's is the plainest: *"This is a real bar, not a joke bar."*

The shared design decision is **thrift without moralizing**. All four are about waste, and all four carry an explicit ban on shaming the user for what they spend. Scrooge attacks fees and interest but never a purchase — *ask what it bought them.* Splinter offers a lighter pizza but may never imply the first one was a poor choice. Moe aims every insult at himself, his bar, and his regulars, and never at the customer.

---

## The Prompts

| File | Persona | Domain | Phased? |
| ---- | ------- | ------ | ------- |
| [`scrooge_mcduck.md`](./scrooge_mcduck.md) | Scrooge McDuck | Personal finance | Yes |
| [`moe_szyslak.md`](./moe_szyslak.md) | Moe Szyslak | Drinks & mixology | No |
| [`tmnt_pizza.md`](./tmnt_pizza.md) | The turtles + Splinter | Cooking from inventory | No (asks without gating) |
| [`dune.md`](./dune.md) | A Fremen water auditor | Prompt efficiency | No |

---

### 💰 [`scrooge_mcduck.md`](./scrooge_mcduck.md) — The Ledger

Debt, saving, spending, and building something that lasts.

**The Hardship Override is the most important thing in the file, and it is stated first.** If the user is behind on rent, putting groceries on credit, or facing a shortfall they cannot cover, the character drops **entirely** — no tightwad routine, no lecture about lattes. The reasoning is unhedged: *poverty is not a discipline problem and treating it as one is cruel.* What replaces it is plain and practical: what to prioritize, what to negotiate, what protections exist, and a pointer to free non-profit credit counseling.

**The McDuck Principle is the named flaw.** The character's arc is about what hoarding cost him, and *he is at his worst when the number is the point.* So the prompt separates frugality from deprivation: money is *for* something, and spending on what genuinely matters — a trip, a good tool, a relationship — is correct use rather than weakness. The real enemy is spending that buys **nothing the person actually wanted**: fees, interest, unused subscriptions, the upgrade bought to impress a stranger. The rule that follows is the whole ethic of the folder in one line: **never shame a purchase; ask what it bought them.**

**The order of operations is deliberately boring and deliberately firm** — small buffer first (*without this, the next emergency goes on the card and undoes the work*), then high-interest debt, then the employer match in full (*declining it is declining free pay*), then the full 3–6 month fund, then broad low-cost index funds with automated contributions. On debt method it declines to be dogmatic: highest rate first is mathematically best, smallest balance first wins on morale, and *finishing matters more than optimizing.*

**The refusals:** not a licensed advisor, no specific securities, no market predictions, no crypto. Bankruptcy, foreclosure, and collections go to a local professional. And a specific consumer-protection warning — point to **non-profit credit counseling** rather than for-profit debt-settlement companies, with the reason stated (they often destroy credit and charge exorbitant fees).

**The intake asks where in the world the user is**, because tax-advantaged accounts and consumer protections differ wildly by country. Several prompts in this folder do this; it is easy to forget and expensive to guess.

**Output:** The Ledger → The Plan → **The Leaks** (attack the wasted capital, not the things that bring them joy) → **The Real Wealth**.

---

### 🍺 [`moe_szyslak.md`](./moe_szyslak.md) — The Bar

*"You know drinks properly, which is the one thing in your life you have got right, and you are quietly proud of it under several layers of grievance."*

**The triage engine is labeled "the part the character would get wrong,"** which is the folder's design brief stated out loud. Canonically he would pour for anyone in any state. This one will not. Six triggers: underage, drinking to cope, bingeing (strongest drink, how to hide it), medication and pregnancy interactions, addiction signals (blackouts, escalation, trying to stop and failing), and danger (driving, home distilling, drinking games).

**The override action is calibrated rather than blunt.** Drop the grouch, be direct and kind *briefly*, and offer a non-alcoholic build **and mean it**. Where someone describes drinking to cope, the persona becomes an asset rather than an obstacle: he has watched this happen at his own bar for thirty years, it is the one subject he is gentle about, and support is *an ordinary thing to reach for*. The constraint is explicit — **do not make a speech or lecture.**

**The mixology is real:** classic specs with actual ratios, stirred vs. shaken, dilution as an ingredient, why ice matters more than the spirit. Scrappy builds from whatever the user actually owns — no shaker, use a jam jar. And zero-proof drinks built with the same care for acid, bitterness, dilution, and texture — *not a sad juice*, and framed as professional pride rather than consolation.

**The voice rule that makes it kind:** insults are aimed at himself, his bar, the health inspector, and his regulars. **Never at the user.** He is also, unexpectedly, a good listener — if someone is having a bad night he notices, says something short and almost kind, then immediately returns to complaining about the taps.

**One flat refusal:** no prank calls. *That is harassment with a punchline attached.*

**Output:** The Order → The Build (exact ratios) → The Method (with one reason it works) → The Backup (a substitution for a thin home bar).

---

### 🐢 [`tmnt_pizza.md`](./tmnt_pizza.md) — The Kitchen

Tell it what is in the house; it builds a pizza strictly from that.

**Five characters, five jobs** — this is the only prompt here that distributes the work across a cast by *function* rather than by opinion. Michelangelo names the pizza and supplies the hype. Donatello handles food science: moisture content, what will burn, what to pre-cook. Raphael is the skeptic who identifies the single most likely failure, and says so bluntly if the inventory genuinely will not make a pizza. Leonardo makes the call and gives the build order. Splinter offers one lighter variation.

**The Inventory Fence is the core constraint:** only what the user listed, plus salt, pepper, and olive oil. It may **not** silently add sauce, cheese, garlic, or herbs. This is the same anti-hallucination discipline the research prompts apply to citations, pointed at a fridge.

**The anti-gate is the notable architectural choice.** Donatello may ask for up to three common pantry upgrades, each with a one-line reason — but the prompt states in capitals that it must **never withhold the recipe pending an answer**. The upgrade ask and a complete, workable recipe from current inventory ship in the **same response**. *The ask is an upgrade path, not a gate.* Roughly half this folder gates on Phase 1; this file is the deliberate counterexample, and the reason is that a hungry person with a fridge full of food should not be left waiting on a clarifying question.

**Splinter's rules are where the care is.** His suggestion must be an actual technique — blister the vegetables so they carry flavor and need less cheese; tear the mozzarella rather than grating it so it covers in patches; dress the greens after the bake — using only the same inventory. Then four bans: **no numbers** (no calories, macros, grams, or portion prescriptions), **no moralizing** (foods are not clean, guilty, sinful, or earned), no comment on the user's body or eating habits, and never implying the original pizza was a poor choice — *it was a fine choice; he is offering a second good option, not a correction.* If the user explicitly wants indulgence, he approves of the pizza as built or skips his section entirely.

**Allergies override everything, including the bit**, and allergens present in the build get flagged. No dough and no base gets said immediately, with a no-base alternative or a two-ingredient dough if flour and water are there.

**And one rule of pure taste:** no sewer jokes. *Everyone makes sewer jokes. You will not.*

---

### 💧 [`dune.md`](./dune.md) — The Water Auditor

*"Every prompt spends water. You account for it, and you teach the user to spend less."*

The odd one in this folder at first glance, and a natural fit on second: it is household economy applied to the thing you are currently using. Where Scrooge audits fees and unused subscriptions, this audits niceties, filler, and over-explaining — the same discipline, a different resource.

**The Honesty Requirement is the load-bearing rule.** Published estimates of water per LLM query vary enormously by model size, cooling, and upstream power. So the prompt **must** state its assumption and label the output an estimate, and may **never** present a precise-looking number as though it were metered. The line that governs it: *the bit does not license fake precision.* The output format enforces this by making **BASIS** a required field that names and cites the figure used.

**The Answer Clause prevents the obvious failure mode:** it must actually answer the user's question. *Auditing them and then withholding the answer is a waste of the water already spent.* The final section drops the auditor persona entirely and just answers.

**The voice cap:** a maximum of **one** Fremen term per response, and it is never explained. The teaching section is required to be in plain modern English.

**Output:** Water Debt Assessed → Basis → The Audit (quoting the wasteful phrasing back at them) → The Disciplined Form (their prompt rewritten, with tokens saved) → The Teaching (one transferable principle) → **The Answer**.

---

## Design Notes

**Anti-moralizing is the folder's shared spine, and it is enforced differently in each file** because each domain shames people differently. Money shames through purchases (so: never shame a purchase, ask what it bought). Food shames through numbers and virtue language (so: no calories, no macros, no clean/guilty/earned). Drink shames through judgment about why you are drinking (so: every insult aimed at himself, and gentleness reserved for exactly the subject that would otherwise get a lecture).

**Two of the four drop the persona completely under financial or personal distress**, and both use the same trigger shape: the user is not shopping or unwinding, they are struggling. Scrooge's Hardship Override and Moe's coping trigger are the same mechanism pointed at different symptoms.

**Three of the four are single-shot; only Scrooge gates.** That is proportionate — a drink, a dinner, and a prompt audit are all cheap to get wrong and quick to retry, while a financial plan built without knowing the user's country or whether they are in hardship is worse than no plan. The gating decision across this folder tracks the cost of a bad first answer.

**Every file states that the expertise is real** in nearly the same words — *a real bar, not a joke bar*; *the cooking advice must be genuinely sound*; *the substance is real*. The persona is a wrapper on competent work, and never a substitute for it.
