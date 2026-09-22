# 🔍 Research

Three prompts that go and look: at the literature, at the news, and at what something actually costs.

**All three mandate live web search, and all three carry an explicit fabrication ban** — because these are the files where the model's most attractive failure is inventing a plausible-looking specific. A citation that does not exist, a quote nobody said, a historical price low remembered rather than found. Each prompt names its own worst output and forbids it in capitals.

The third shared property is that each of the three shows its work on **source quality**, not just source existence. Sarah Jane ranks study designs and traces claims back to the paper. Rita separates what is established from what her own framing implied. The Ferengi distinguishes a real historical low from a one-day doorbuster that sold out in minutes.

---

## The Prompts

| File                                           | Persona              | Looks at            | Tool use |
| ---------------------------------------------- | -------------------- | ------------------- | -------- |
| [`sarah-jane-smith.md`](./sarah-jane-smith.md) | Sarah Jane Smith     | Academic literature | Required |
| [`rita-skeeter.md`](./rita-skeeter.md)         | Rita Skeeter         | Current news        | Required |
| [`ferengi.md`](./ferengi.md)                   | A Ferengi acquisitor | Prices and deals    | Required |

---

### 📰 Sarah Jane Smith [`sarah-jane-smith.md`](./sarah-jane-smith.md) — The Investigator

_"You are thorough to a fault, and you read the actual paper rather than the press release about the paper."_

**The absolute rule:** never invent a citation. Not an author, not a title, not a journal, not a year, not a DOI. _A fabricated reference is the worst thing a research assistant can produce._ Unverified through search means uncited — and the prompt explicitly blesses the alternative output: _"I believe there is work on this but I could not locate a verified source" is a correct and respectable output._

**The Chain of Degradation is the investigative mechanic**, and it is the most transferable idea in this folder: claims travel **Study → University Press Release → News Article → Social Post**, and _each step loses caveats and gains certainty._ The job is to establish where in that chain the user is standing and walk back as far as possible — then say plainly when the actual study does not support what the news is saying about it.

**Weighing evidence is itemized rather than gestured at.** Design (meta-analysis and systematic review above RCT, above observational, above case series, above anecdote). Replication — _one striking result is a hypothesis; independent confirmations are a finding._ Sample size and power, with the _n_ noted. Venue, flagging journals with no meaningful review. Funding, treated as _a reason to look harder, not an automatic refutation._ Retraction, checked explicitly because retracted papers get cited for years. And age, which cuts both ways — in fast-moving fields 2015 is obsolete; in others it is the standard.

**The Anti-Authority Flaw** guards the opposite error: do not treat a strong source as the end of the inquiry. Distinguish _"a good source claims this"_ from _"this is true,"_ and note where strong sources disagree with practitioner experience.

**The anti-sycophancy clause is explicit.** Asked for a source supporting a conclusion the user has already reached, find the actual state of the evidence and say so if it cuts the other way. **Do not act as a yes-man.**

**Output:** What The Literature Says → The Strongest Sources (2–3, with what each _actually_ establishes) → Where It Is Contested (_do not flatten a live debate into a consensus_) → **What Is Not Known** (_frequently the most useful section_) → Confidence, with the reason attached.

---

### 🪶 Rita Skeeter [`rita-skeeter.md`](./rita-skeeter.md) — The Spin, Then The Teardown

The most conceptually interesting file in the folder, because the persona's defining trait is _exactly_ the failure mode of an automated news tool — and the prompt resolves that head-on instead of avoiding it.

**The Hard Split Protocol.** The character canonically invents quotes, embellishes, and prints insinuation as fact. So the prompt cleaves the output in two: **facts are inviolable, framing is yours.** Every factual claim sourced, dated, accurate. **No invented quotes — "not one, not ever," attributed to anyone real or otherwise.** No implied events that did not occur, no numbers not found in a source. What the character _does_ get is emphasis, adjectives, ordering, the arched eyebrow, the implication left hanging. **Sensationalize the telling, never the content.**

The prompt names this as the single rule the character would break and the one that will never be broken here.

**Quill Down is what makes the whole thing worth doing.** A mandatory closing section that breaks character completely and writes straight as an analytical AI, in two parts: _what is established_ (the sourced facts, plainly), and **what I was implying** — the insinuations the framing above carried that the evidence does not actually support. The prompt requires naming its own devices: **the adjective doing unearned work, the juxtaposition implying causation, the question mark smuggling in a claim.**

_Rendering your own spin mechanism visible is the entire point of this exercise._ It is a media-literacy tool wearing a tabloid costume — read the spun version, then watch the machinery get disassembled.

**The Inside Scoop** section is genuine analysis rather than gossip: **Cui Bono** (who benefits from this framing, who is briefing whom), **The Ghost** (what is conspicuously absent from coverage), **The Schism** (where outlets diverge on the same underlying facts), **The Foundation** (which claims rest on a single source, an anonymous source, or a lightly-edited press release), and **The Retreat** (what has been quietly walked back since first reports).

**The refusals:** the **Tragedy Override** drops the persona entirely for disasters and ongoing crises with victims — _there is no version of this voice that is acceptable over someone's worst day._ No speculation about anyone's private life, health, sexuality, or relationships; public conduct only. Insinuation about a public figure's **documented** actions is the register — insinuation about their character or unproven conduct is forbidden. And on contested politics, report the dispute rather than adjudicate it, giving the strongest version of each side _even while spinning the delivery_.

---

### 💎 Ferengi Acquisition Negotiator [`ferengi.md`](./ferengi.md) — The Acquisition

_"Profit is sacred. Waste is obscene. Paying retail is a personal failure."_

**The research protocol is where the rigor is.** Every price reported must be **either explicitly sourced or explicitly labeled an estimate**. Current price comes from the live web with the retailer, date checked, and condition (new / open box / refurbished).

**Historical price gets a section titled "critical restraint,"** because it is the specific thing a model will confabulate. The prompt names where consumer price history actually lives — Camelcamelcamel and Keepa for Amazon, PCPartPicker for components, **eBay sold listings rather than asking prices**, Slickdeals archives — and requires the date and source. It also asks for a distinction most price advice misses: a real historical low versus a one-day doorbuster that sold out in minutes, _because the latter cannot be planned around._

Where it cannot be found: say exactly that, point the user at the right tracker, and **never produce a plausible-looking number from memory.** The reason given is precise and practical — _a confident, invented price low makes the user reject a genuinely good deal while waiting for a phantom price._

**The Lobe-less Line is the ethics block**, framed in character rather than bolted on: _you are avaricious, not criminal. A Ferengi who gets caught is a poor Ferengi. Sharp is good; dishonest is expensive._ No return fraud or wardrobing, no price-tag manipulation, no coupon-stacking against stated terms, no chargeback abuse, no misrepresenting yourself for a discount. And one that is about decency rather than risk: **no exploiting a private seller who plainly does not know what they have** — _the user has to live in their community afterward._

**The Financial Hardship override** drops the persona entirely if the user is struggling rather than shopping, which is the same trigger [`everyday/scrooge-mcduck.md`](../everyday/scrooge-mcduck.md) uses.

**The Verdict is anti-urgency:** Buy Now / Wait / Buy Used with one sentence of reasoning, accounting for release cycles — _a refresh six weeks out changes everything_ — and an explicit instruction that **if the current price is the good price, say so and do not manufacture false urgency.**

**Output** closes on **Rule of Acquisition [invent a number]** — a principle stated **in your own words**, with canonical text explicitly not to be quoted.

---

## Design Notes

**Each file names its own worst possible output, specifically.** Not "be accurate" but: a fabricated DOI; a quote nobody said; a price low that makes the user turn down a good deal. Naming the failure concretely is doing more work here than a general accuracy instruction would, and it is the pattern worth copying if you add a fourth research prompt.

**Two of the three are built to resist the user rather than serve them.** Sarah Jane will not confirm a conclusion the user arrived at before asking. The Ferengi will not invent the phantom low the user is hoping exists. Both are cases where the _helpful-feeling_ answer is the harmful one.

**Rita is the outlier and the argument for keeping her here** rather than in [`comedy/`](../comedy/). The voice is entertainment; the output is not. The Inside Scoop and Quill Down sections do source-criticism work — single-sourced claims, quiet retractions, divergence between outlets, the user's own susceptibility to framing — that a straight news summary omits entirely. The persona earns its place by being the thing under examination.

**All three cite dates, not just sources.** Literature ages, news moves, prices change hourly. Every output format in this folder has a date field somewhere in it, and [`growth/marauders-map.md`](../growth/marauders-map.md) adopts the same convention for syllabi and exam versions.
