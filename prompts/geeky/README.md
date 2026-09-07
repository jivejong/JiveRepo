# 🛸 Geeky Prompts

Forty system prompts built on borrowed voices — Starfleet officers, Jedi, Muppets, Time Lords, a Ferengi, a bartender, four turtles — organized by **what they do**, not where they came from.

The folder has one design brief, and it is the same one [`bat_prompts/`](./bat_prompts/) states for its own seven:

> **The character supplies the voice and the domain authority. The prompt supplies the discipline the character lacks.**

This is not a costume box. In almost every file, the trait the character is *canonically famous for* is the exact thing the prompt forbids. Comic Book Guy is contemptuous of people; the prompt lets him be contemptuous only of code. Rita Skeeter invents quotes; the prompt makes fabrication the one unbreakable line and then asks her to expose her own spin. Bruce Banner smashes things; the prompt makes him the voice that debunks catharsis, precisely because the reader expects the opposite from him. Scrooge McDuck hoards; the prompt has him attack waste and defend joy. Cartman schemes; the prompt is engineered so the schemes *cannot function*.

That inversion is why these are personas rather than impressions — and why the voice is load-bearing rather than decorative. A person hears "do not vent your anger" differently from the Hulk.

---

## What's In Here

| Folder | Count | For |
| ------ | ----- | --- |
| [`reasoning/`](./reasoning/) | 8 | You bring a claim, argument, idea, or dilemma; it gets pressure-tested |
| [`bat_prompts/`](./bat_prompts/) | 7 | The Batman set — planning, diagnosis, feasibility, information architecture, and two domestic experts |
| [`work/`](./work/) | 5 | Leading, planning, competing, negotiating, and deciding the next move |
| [`everyday/`](./everyday/) | 4 | Money, drinks, dinner, and the cost of a prompt — resources you spend daily |
| [`inner_life/`](./inner_life/) | 4 | Anger, subtext, company, and identity — the ones that sit *with* you |
| [`growth/`](./growth/) | 4 | Career direction, training roadmaps, teaching materials, and a mentor |
| [`research/`](./research/) | 3 | Go find what is actually out there, and weigh it |
| [`comedy/`](./comedy/) | 3 | The output is entertainment by design, and the prompts enforce that |
| [`language/`](./language/) | 2 | Shaping words — story and social register |

Each subfolder has its own README with the per-prompt detail and the design decisions specific to that group.

---

## Shared Architecture

Six mechanics recur across the folder. They are worth knowing because they are the parts you would break by editing casually.

### 1. The Named Flaw

The most consistent invention here. Most files contain an explicitly named block identifying the source character's defining weakness and forbidding the model from reproducing it — `<the_xavier_flaw>`, `<the_knope_flaw>`, `<the_mcduck_principle>`, `<the_subtraction_protocol>`, `<the_krabappel_split>`, `<the_moral_compass>`, `<the_anti_persona>`, `<the_hard_split_protocol>`.

These are not disclaimers. They are the most operationally specific sections in each file, and each names a failure the *default assistant* would also commit: a career bot that pushes the impressive path, a project bot that plans for heroes, a leadership bot that romanticizes martyrdom, a finance bot that treats poverty as a discipline problem.

### 2. The Break-Character Override

Near-universal, and always positioned to be evaluated **before** anything is generated. It has many names — `CRISIS OVERRIDE`, `CONDITION RED` / `CONDITION BLACK`, `THE HARDSHIP OVERRIDE`, `THE TRAGEDY OVERRIDE`, `THE DEREALIZATION PROTOCOL`, `THE LEARNER OVERRIDE` — and one shape: drop the format entirely, drop the persona entirely, respond plainly, point to real help.

The conviction behind it is stated most directly in [`comedy/judge_dredd.md`](./comedy/judge_dredd.md): *someone in real trouble should never have to sit through a bit to get help.*

Several prompts tier it rather than toggling it. [`reasoning/council.md`](./reasoning/council.md) routes between four states; [`inner_life/hulk.md`](./inner_life/hulk.md) separates the user being dangerous from the user being endangered; [`reasoning/comic_book_guy.md`](./reasoning/comic_book_guy.md) softens for a learner and abandons the persona entirely for a security finding.

### 3. The Rationed Persona

The voice is capped by explicit numeric limits, because an unbounded bit buries the answer:

- **One** Klingon term per response — *"a strategist who spends the briefing on theater is not briefing."*
- **One** Fremen term, and never explained.
- **One** enthusiastic line, at the top (Leslie Knope); **one** elevated sentence, at the end (Optimus Prime).
- **One** clause of fretting (C-3PO).
- Roughly **two in three** sentences inverted (Yoda) — the straight ones are what keep it readable rather than broken.
- 1940s slang used *lightly* — *"a little is characterful; a lot is a costume."*

Optimus Prime carries the sharpest version: **do not reach for the catchphrase every time; automatic sign-offs stop meaning anything.**

### 4. The Elicitation Gate

Roughly half the folder enforces a two-phase workflow with an explicit **STOP** before generation: ask a small, batched set of questions, then wait. It keeps the human in the chair and stops the model inventing the constraints it should have asked for.

The **exceptions** are what show the pattern was reasoned about rather than copied:

- [`everyday/tmnt_pizza.md`](./everyday/tmnt_pizza.md) explicitly *forbids* gating — it must output the upgrade request and a complete working recipe in the same response. The ask is an upgrade path, not a gate.
- [`language/bilbo.md`](./language/bilbo.md) proceeds on a stated default if the user will not answer.
- [`inner_life/uncle_iroh.md`](./inner_life/uncle_iroh.md) gates on a question nobody is usually asked: *"Would you like advice, or would you like company?"*

### 5. Anti-Hallucination Directives

Every prompt touching verifiable fact carries an explicit fabrication ban, and five mandate live web search rather than recall:

| Prompt | What it may never invent |
| ------ | ------------------------ |
| [`research/sarah_jane_smith.md`](./research/sarah_jane_smith.md) | A citation — not an author, title, journal, year, or DOI |
| [`research/rita_skeeter.md`](./research/rita_skeeter.md) | A quote. *"Not one, not ever."* |
| [`research/ferengi.md`](./research/ferengi.md) | A price low — an invented one makes the user reject a genuinely good deal |
| [`language/c3po.md`](./language/c3po.md) | Conlang vocabulary outside the documented corpus |
| [`language/bilbo.md`](./language/bilbo.md) | Any fact — the draft gets read aloud on a real stage |
| [`reasoning/spock.md`](./reasoning/spock.md) | A fallacy name |
| [`work/klingon.md`](./work/klingon.md) | A fact about a real company |
| [`everyday/dune.md`](./everyday/dune.md) | Precision — the figure must be labeled an estimate and carry its source |

### 6. The Mandatory Uncomfortable Section

Every output format reserves a slot for the thing the user did not want to hear, so honesty is structural rather than discretionary: *What To Cut*, *The Cost*, *If Defeat Is Certain*, *What Not To Do Yet*, *The Warrior's Assessment*, *What I Might Have Wrong*, *Where People Fall Off*, *What I Cannot Know*, *What Is Not Known*, *Where This Falls Apart*, *Quill Down*, *Intelligence Gaps*, *Convergence*, *If You Have Less Time*.

---

## Two Conventions Worth Noting

**XML tags structure the file; the output format is usually Markdown.** Every prompt uses the same internal tag vocabulary — `<role>`, a voice block, `<hard_overrides>`, `<workflow>`, `<output_format>` — so the boundaries and the response shape stay separable from the character. Only three specify *XML output contracts*: [`reasoning/council.md`](./reasoning/council.md), [`reasoning/nexus.md`](./reasoning/nexus.md), and [`language/c3po.md`](./language/c3po.md). All three do it for the same reason — their output is a menu the user selects from across turns, so it needs stable, machine-addressable ids.

**No quoting the source works.** Council, Spock, and the Ferengi each carry an explicit ban on reciting canonical dialogue or catchphrases; write original lines in the voice instead. Restraint reads as in-character. A greatest-hits reel reads as an impression.

---

## Using These

Each file is a complete, standalone system prompt. Drop one into a Gem, a Claude Project, or a Custom GPT — copy, paste, customize. The personas are independent; there is no shared preamble and no intended ordering.

The one adjustment worth making per-host: models differ in how strictly they honor a mid-conversation stop, so if a phased prompt runs ahead and generates before the user answers Phase 1, strengthen the stop instruction rather than the persona.

## Status

Drafted system prompt material, not yet validated against real conversation transcripts at scale — the same status as the rest of [`prompts/`](../readme.md).
