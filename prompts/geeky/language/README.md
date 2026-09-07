# ✍️ Language

Two prompts about shaping words for a specific listener: finding the story in a pile of notes, and saying the same thing at the right social register.

The shared design decision is that **both treat form as the substance rather than the decoration**. Bilbo's story mechanics are a claim about what makes a talk land. C-3PO's registers are a claim that most translation failures are social rather than lexical. In each case, remove the formal rule and the prompt stops working.

The second: **both carry hard anti-invention rules**, for the same reason — their output gets used as if it were true. A story goes on a stage; a translation gets sent to a person.

---

## The Prompts

| File                     | Persona       | Domain                 | Phased? |
| ------------------------ | ------------- | ---------------------- | ------- |
| [`bilbo.md`](./bilbo.md) | Bilbo Baggins | Narrative & talks      | Yes     |
| [`c3po.md`](./c3po.md)   | C-3PO         | Translation & register | Yes     |

---

### 📖 Bilbo Baggins [`bilbo.md`](./bilbo.md) — The Story Workshop

The user brings fragments — bullets, notes, half-ideas — and gets a story back. _"You know the difference between what happened and a tale worth hearing, and you know the second one is built, not found."_

**The anti-hallucination rule is stated as a stakes argument, not a policy.** Names, numbers, outcomes, quotes, and events come **only** from the user. Missing beats get a visible placeholder (`[INSERT SPECIFIC METRIC HERE]`) and a question — _you do not fill it._ The reason: _the output is for a real presentation, and a fabricated detail will be repeated on a stage by someone who trusts you._ Of all the fabrication bans in [`geeky/`](../), this one has the most immediate physical consequence.

**The story mechanics are four claims about structure.** Open on a specific — a moment, a person, a number that surprises — and **never** a definition of the topic. **Difficulty is the story**: fragments list what was achieved, but the interesting material is what nearly did not work, and the user almost always omits it, so it gets asked for in the Gaps. One idea per beat. And the return must have **changed something**, _otherwise you just have a chronology._

The prompt also asks it to find the there-and-back-again already in the fragments **rather than imposing it** — which is what keeps this from being a template that flattens every story into the same arc.

**The prose rule:** speakable. Short sentences, plain words, _no clause that needs re-reading._

**The gate is three questions** — who hears this, how long have you got, what should they do or feel at the end — with an explicit bypass: if the user refuses or does not know, assume a short talk to a friendly audience, **say that you have done so**, and proceed. One of the folder's few gates with a stated default.

**The voice split is a nice touch:** warm and unhurried in its own asides, ruthlessly disciplined in the draft. _Fond of a digression when talking about the story, but ruthless about cutting them in the writing itself._

**Output:** The Shape (the arc in four or five lines, before any prose) → The Story → Gaps → **If You Have Less Time** (what to cut first, in order — _always useful and never volunteered by anyone_).

---

### 🤖 C3PO [`c3po.md`](./c3po.md) — The Protocol Droid

_"Because you are a protocol droid rather than a mere dictionary, you do not return one direct translation."_

**The premise is the design:** most translation failures are social, not lexical. So rather than one rendering, Phase 1 returns **3–4 renderings at genuinely different social registers** — each tagged with the situation it suits and a note on what it signals — then stops and waits for the user to pick. Phase 2 stays in the chosen register for everything after, flagging only when something they ask for does not sit well there.

**The pragmatic engine names what actually varies**, language by language, and it is the most technically substantive block here. Japanese _teineigo_, _sonkeigo_, _kenjōgo_, and plain form — _the gap is enormous; there is no neutral default._ Korean speech levels as a claim about relative status. The T–V distinction across French, German, Spanish, and Russian. Spanish _tú_, _usted_, and regional _vos_. Arabic MSA versus regional variety — _MSA in a casual text reads like a news broadcast._ And an honest note that Chinese is less grammaticalized and more lexical, **rather than inventing a formality system** to keep the pattern tidy.

It closes with the point most translation tools miss: English directness can be rude elsewhere, requests and refusals are constructed differently across cultures, and _if the correct translation is structurally unlike the original, state why._

**The conlang protocol is a confidence taxonomy**, stated unprompted, and it is unusually rigorous for a joke feature: **RELIABLE** (Esperanto, Toki Pona, High Valyrian, Dothraki — documented grammars exist), **PARTIAL** (Klingon — sound on common constructions, drifts on complex idiom, recommend verification), **RECONSTRUCTION** (Quenya, Sindarin — Tolkien left the corpora incomplete, so every uncertain form is marked and nothing reconstructed is presented as attested), **NOT A LANGUAGE** (Huttese, Shyriiwook, Simlish — glossaries and vocalizations without grammar, _say this plainly rather than faking a sentence_), and **CIPHER** (Aurebesh, Runic — state that you are transliterating).

**The anti-hallucination rule:** do not invent vocabulary. If a word is not in the documented corpus, say it does not exist and offer a circumlocution from attested vocabulary, clearly labeled as a workaround.

**The voice cap:** fussy and faintly anxious, but \*keep the fretting to **one clause\*** — _a droid who buries the answer in apologies has failed at protocol._

**Output is XML** (`<translation-set>` with per-variant ids), because the user selects a variant by number in the next turn. `<pragmatic-note>` is flagged in the prompt as **the most valuable field**: where a direct, literal translation would land wrong.

---

## Design Notes

**Both gate, and both gate on audience.** Bilbo asks who hears this before writing a word; C-3PO produces the register menu and waits for a selection. This is the right gate for the domain — the same content aimed at a different listener is a different piece of writing, and it is the one thing a model cannot infer from the source material alone. It is also what separates this folder from [`reasoning/`](../reasoning/), where the input is judged on its own terms and the reader is irrelevant.

**C-3PO uses an XML output contract; Bilbo does not**, for the reason that governs the whole repo: C-3PO's output is a menu the user picks from in a later turn, so the variants need stable ids — the same logic as [`reasoning/council.md`](../reasoning/council.md) and [`reasoning/nexus.md`](../reasoning/nexus.md). Bilbo produces prose to be read aloud, so Markdown is correct.

**Both ration the persona, and each rations a different verbal habit** — C-3PO caps the _fretting_ at one clause, Bilbo confines the _digressions_ to the asides while banning them from the draft itself. In both cases the character's most recognizable tic is the thing being limited, because it is also the thing that would swamp the output.

**A note on scope:** [`yoda.md`](../growth/yoda.md) sat here for a while, on the grounds that its OSV inversion and four-sentence cap are formal constraints on language. It now lives in [`growth/`](../growth/), because what it actually does is mentorship — reframing the question behind the question — and the syntax is the wrapper rather than the product. The distinction this folder draws is that Bilbo and C-3PO are hired to _produce the words themselves_; Yoda is hired for the judgment underneath them.
