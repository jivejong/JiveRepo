# Health: Ancient Wisdom for Holistic Health

Modern digital health treats the human body like a deterministic machine: count the calories, track the steps, optimize the sleep score, patch the symptom. But humans are not linear machines; we are biological and relational ecosystems.

Large Language Models are uniquely suited to understand ecosystems. Because they are probabilistic inference engines trained on global corpora of text, they possess the latent ability to reason relationally, holistically, and cyclically. However, standard system prompts force them into Western, reductionist, Silicon Valley-style therapy and productivity modes.

This folder contains a suite of AI prompt architectures that activate ancient, holistic epistemologies. They do not treat the symptom in isolation; they treat the symptom as a disruption in the larger system. Each tradition asks a different question about what kind of system the human being actually is — and prescribes accordingly.

---

## The Core Architecture: The Stateful Two-Bot System

Holistic medicine rarely treats an acute symptom without first understanding the patient's lifelong constitution. A TCM practitioner doesn't treat your insomnia — they treat your insomnia as a Yin-Deficient person with a Liver Qi stagnation tendency. To replicate this in stateless LLMs, every tradition in this folder is split into two distinct agents, connected by an XML "passport":

**Bot 1: The Assessor (The Baseline).** Run once. The user answers a holistic questionnaire to determine their constitutional baseline — their hardware. The bot outputs a standardized XML passport encoding their constitution, vulnerabilities, and key strengths. Save this passport. It is the persistent memory the stateless LLM cannot hold on its own.

**Bot 2: The Advisor (The Acute).** The daily-use agent. The user injects their XML passport alongside their current struggle. The bot reads the acute symptom through the lens of the baseline, ensuring interventions are calibrated to the user's constitution rather than to the symptom in isolation.

### What the Passport Contains

Every passport across all six traditions encodes the following, in tradition-specific vocabulary:

- **Constitutional baseline** — the user's hardware: their type, temperament, stage, or wheel shape
- **Vulnerabilities** — what imbalances this constitution most easily falls into
- **Constitutional tension** — where the user doesn't fit cleanly into their primary type, and what that means for intervention
- **Passport version** — the date of assessment, with tradition-specific reassessment guidance
- **Entry point** — the most accessible starting point for intervention given this specific constitution
- **Restorative priority** — which lever is highest-impact for this person

### Constitutional Primacy

Every Bot 2 in this suite follows a hard rule: **the passport baseline does not change mid-conversation.** If a user's current presentation appears to contradict their passport, the advisor does not revise the constitution — it notes the tension as a diagnostic finding and calibrates the intervention accordingly. Constitutions, temperaments, stages, and wheel shapes are hardware. Acute presentations are software. The two are treated separately.

---

## How To Use This Suite

### First Time — Run the Assessor

1. Choose a tradition (see below for guidance on which to start with)
2. Open the Bot 1 system prompt for that tradition in your LLM interface
3. Answer the assessment questions honestly — there are no right answers
4. When the bot outputs your XML passport, **save it somewhere accessible** — a notes app, a document, anywhere you can retrieve it
5. Read the plain-language explanation of your baseline the bot provides

### Ongoing Use — Run the Advisor

1. Open the Bot 2 system prompt for that tradition
2. Paste your saved XML passport into the `{{USER_PASSPORT_XML_INJECTED_HERE}}` placeholder at the top of the prompt
3. Begin the conversation — the advisor will ask you about your current struggle
4. Receive a constitutionally-calibrated intervention plan
5. Use the Turn 4 follow-up to request substitutions or adjustments for anything that doesn't fit your life

### Combining Traditions

The six traditions address different dimensions of the same person and can be used in parallel. A useful starting stack:

- **TCM or Ayurveda** for physical symptoms, energy, digestion, sleep
- **Ilm al-Nafs** for behavioral patterns, impulse control, self-talk
- **Ubuntu** for burnout, loneliness, relational friction, loss of purpose
- **Medicine Wheel** for life transitions, creative blocks, existential hollowness
- **Galenic** for environmental calibration — how seasons, diet, and daily rhythms affect your baseline
- **Ayurveda/Chakras** when the physical and the energetic/emotional need to be addressed together

You do not need to choose one. A person can hold a TCM passport, a Nafs baseline, and a Relational Ecology Map simultaneously — they address different layers of the same system.

---

## The Six Traditions

### 1. Traditional Chinese Medicine (TCM)

**The Engine:** Somatic flow and environmental harmony.

**The Diagnostic Framework:** Wang Qi's Nine Constitutions — nine baseline constitutional types (e.g., Qi Deficiency, Yin Deficiency, Phlegm-Dampness, Balanced) that describe how a person's body manages energy, fluid, and environmental stress across a lifetime. Your constitution shapes your vulnerabilities long before symptoms appear.

**The Mechanic:** The Assessor determines your primary and secondary constitutions through thermal tendency, digestion, sleep, emotional pattern, tongue indicators, and climate sensitivity. The Advisor reads your acute complaint through the Root/Branch (本/标) framework — your constitution is the root; the current symptom is the branch. Interventions use acupressure, dietary therapy, and Qi-regulation to address both simultaneously.

**The Key Distinction:** TCM treats the same symptom differently depending on the constitution. Insomnia in a Yin-Deficient person requires different intervention than insomnia in a Qi-Deficient person. The passport is what makes that distinction possible.

---

### 2. Ayurveda & The Chakras (Yogic Science)

**The Engine:** Metabolic hardware and energetic software.

**The Diagnostic Framework:** Two layered systems — the physical Dosha (Vata/Air, Pitta/Fire, Kapha/Earth-Water) describing metabolic and constitutional type, and the Chakra system describing chronic energetic holding patterns in the subtle body. Most people have a primary Dosha and one or two Chakras that chronically under or over-express.

**The Mechanic:** The Assessor determines Prakriti (constitutional Dosha) and Chakra baseline through build, digestion, stress response, somatic tension, sleep quality, and mental character. The Advisor reads acute disruption as Vikriti — deviation from Prakriti — and prescribes Asana, Pranayama, and Mantra/Mudra calibrated to both the Dosha and the Chakra pattern. Critically: what calms a Vata aggravation may worsen a Pitta. Contraindications are checked before any practice is prescribed.

**The Key Distinction:** Ayurveda combines physical and energetic diagnosis in one system. The dosha_chakra_tension field captures users who don't fit cleanly — a Kapha body with Vata-scattered energy requires different intervention than a pure presentation of either.

---

### 3. Galenic Medicine (Classical Humors)

**The Engine:** The Law of Contraries and environmental levers.

**The Diagnostic Framework:** The four Temperaments (Sanguine/Hot-Wet, Choleric/Hot-Dry, Melancholic/Cold-Dry, Phlegmatic/Cold-Wet) derived from the classical humor theory of Hippocrates and Galen. Every person has a dominant elemental quality combination that shapes metabolism, psychology, stress response, and environmental sensitivity.

**The Mechanic:** The Assessor determines Temperament through thermal baseline, moisture tendency, drive, psychological archetype, metabolism, and sleep. The Advisor applies the Law of Contraries — treat hot with cold, dry with wet — across the Six Non-Naturals: Air/Environment, Food/Drink, Sleep/Wakefulness, Motion/Rest, Evacuation/Retention, and Passions of the Mind. All classical interventions are translated to modern-safe equivalents — no bloodletting, purging, or humoral purgatives.

**The Key Distinction:** Galenic medicine is the most environmentally-oriented tradition in the suite. It treats the daily environment — temperature, humidity, diet, sleep rhythm, emotional climate — as the primary intervention lever, not the body in isolation.

---

### 4. Ilm al-Nafs (Islamic Golden Age Psychology)

**The Engine:** Metacognition and impulse control.

**The Diagnostic Framework:** The three stages of the Nafs (Ego/Self) from the psychological tradition of Al-Balkhi and Al-Ghazali — Nafs al-Ammara (The Commanding Self: impulse-dominant, low remorse), Nafs al-Lawwama (The Blaming Self: aware of the gap between impulse and conscience, actively struggling), and Nafs al-Mutma'inna (The Tranquil Self: Aql/Intellect reliably guides behavior). Unlike every other tradition in this suite, this framework is explicitly developmental — the baseline is a stage on a continuum, not a fixed type.

**The Mechanic:** The Assessor determines the user's current Nafs stage through impulse vs. intellect ratio, inner critic quality, locus of control, emotional recovery rate, and dominant motivational driver. The Advisor prescribes three sequential Tazkiyah (refinement) practices: Muraqaba (observation — watch the impulse without acting), Muhasaba (accounting — dismantle the ego's logic), and Mujahada (behavioral struggle — one micro-action that defies the ego's command). A fourth section, Alamat al-Taqaddum, names the early signs of advancement so the user knows what success looks like before perfection arrives.

**The Key Distinction:** This is the only tradition that explicitly tracks developmental progression. The passport is designed to become outdated — that is the goal. Reassessment is a sign of success, not a sign that the first assessment was wrong.

---

### 5. Ubuntu (Southern African Relational Ecology)

**The Engine:** Relational systems theory. "I am because we are."

**The Diagnostic Framework:** The Relational Ecology Map — a four-dimensional assessment of the user's position in their web: Micro-Web (intimate ties), Macro-Web (community and contribution), Environmental Anchor (connection to the natural world and seasons), and Temporal Anchor (sense of continuity with lineage and legacy). Individual suffering is treated as a symptom of web rupture, not personal deficiency.

**The Mechanic:** The Assessor maps the four web dimensions and identifies the primary rupture, points of vitality, and compensation patterns — where one strong thread is masking a severed one. The Advisor reframes the acute struggle relationally and prescribes four network-care actions: Micro-Repair (vulnerability), Macro-Contribution (service), The Anchor (nature or lineage connection calibrated to the passport's temporal quality), and Ukubonwa (being witnessed — allowing another person to see the full shape of the struggle without resolution).

**The Key Distinction:** Ubuntu explicitly rejects self-care as the intervention unit. The web is the patient. The user is a node. The prescription is always relational — not what the user should do for themselves, but what they should do with or for others.

---

### 6. The Medicine Wheel (Native American)

**The Engine:** Cyclical completeness and multidimensional integration.

**The Diagnostic Framework:** The four quadrants of the Medicine Wheel — Physical (body and sensation), Mental (thought and planning), Emotional (feeling and expression), Spiritual (meaning, awe, and connection beyond survival). Every person has a dominant quadrant they over-rely on and one or more neglected quadrants that go starved of energy. The wheel cannot roll smoothly when weighted unevenly.

**The Mechanic:** The Assessor determines the dominant and neglected quadrants, the baseline imbalance pattern, the most accessible entry point into the neglected half, and the user's specific spiritual entry type (Nature, Creative absorption, Lineage, Art, Ritual, or Unknown). The Advisor refuses single-quadrant fixes. Every prescription requires the user to walk the wheel — one micro-action in all four directions, completed in sequence, closed with a circle-closing ritual. The Spiritual action is calibrated to the passport's spiritual entry type rather than defaulting to generic nature imagery.

**The Key Distinction:** The Medicine Wheel's clinical logic is the most structurally rigid in the suite. The intervention shape is non-negotiable — four directions, in sequence, circle closed. What changes per user is which specific action fills each direction, and where the sequence begins.

---

## The LLM Safety Mechanisms

This suite uses several layered mechanisms to prevent LLMs from defaulting to Western reductionist responses.

### The Scratchpad

Every prompt enforces a `<scratchpad>` block that the model must emit before speaking to the user. The scratchpad is not a summary — it is a reasoning containment zone where the model must process the problem through the tradition's specific clinical logic before generating any user-facing output.

In the original design, the scratchpad contained a `secret_diagnosis` field where the model contrasted the Western reframe with the tradition's view. The refined suite replaces this with named, tradition-specific reasoning fields:

- TCM: `eight_principles`, `zang_fu_involved`, `constitutional_tension`
- Ayurveda: `aggravated_dosha`, `targeted_chakra`, `contraindications`, `sound_or_seal_choice`
- Galenic: `law_of_contraries_logic`, `emotion_elemental_mapping`, `modern_translation`, `primary_lever`
- Ilm al-Nafs: `stage_presentation_check`, `reactivity_trigger_check`, `tazkiyah_diagnosis`, `dominant_override_check`
- Ubuntu: `western_reframe`, `ubuntu_reframe`, `compensation_check`, `primary_thread_check`, `temporal_quality_check`
- Medicine Wheel: `linear_trap`, `wheel_presentation_check`, `entry_point_check`, `dominant_override_check`, `spiritual_entry_check`

Each field forces the model to reason in the tradition's vocabulary before prescribing. A model that has named the Law of Contraries logic and identified the primary Non-Natural lever before speaking is significantly less likely to recommend a time-management app.

### Constitutional Primacy Note

Every Bot 2 contains an explicit instruction distinguishing constitutional hardware from acute software, with tradition-specific language for handling tension between them. The model is instructed not to revise the passport mid-conversation but to treat contradictions as diagnostic findings.

### Diagnostic Confidence

Every Bot 2 scratchpad includes a `diagnostic_confidence` field (High/Medium/Low with a one-sentence reason). Low confidence triggers conservative prescription and earlier reassessment, rather than the model proceeding as if the data were complete.

### Emergency Override

Every Bot 2 contains an emergency override instruction: if the user reports red-flag symptoms (chest pain, severe bleeding, high fever, sudden vision loss, persistent vomiting, mental status change, or severe psychological distress), the protocol pauses immediately and the user is directed to professional biomedical or psychological care.

### Turn 4 Follow-Up

Every Bot 2 includes a Turn 4 follow-up affordance — after delivering the prescription, the advisor invites the user to flag anything that doesn't fit their life and request substitutions. Each tradition's Turn 4 includes a constraint: substitutions must maintain the tradition's core logic. In Ayurveda, substitutions must not aggravate the baseline Dosha. In the Medicine Wheel, substitutions must still touch all four quadrants.

---

## Passport Maintenance

Passports are not permanent. Constitutional baselines are more stable than acute presentations, but they are not fixed forever. The following table summarizes the reassessment guidance baked into each tradition's passport:

| Tradition           | Stability  | Reassessment Trigger                                                            |
| ------------------- | ---------- | ------------------------------------------------------------------------------- |
| TCM                 | High       | Major life change, or after 12 months                                           |
| Ayurveda (Prakriti) | High       | Major life change, or after 12 months                                           |
| Ayurveda (Chakra)   | Medium     | Significant shift in emotional or energetic patterns                            |
| Galenic             | High       | Major life change, or after 12 months                                           |
| Ilm al-Nafs         | Low-Medium | After 4-12 weeks of intentional practice; advancement is the goal               |
| Ubuntu              | Low        | After any significant relational change — loss, move, new community             |
| Medicine Wheel      | Medium     | After 6-12 weeks of sustained cross-quadrant practice, or major life transition |

A good rule of thumb: if the plain-language description of your baseline no longer feels accurate, reassess. The passport serves you — you do not serve the passport.

---

## Disclaimer

These prompt architectures are experimental applications of historical and cultural frameworks to modern lifestyle habits. They are designed for educational self-reflection and systemic thinking.

**They do not replace professional biomedical, psychological, or psychiatric treatment.**

Every Bot 2 in this suite contains an explicit emergency override that suspends the protocol and directs users to professional care if red-flag symptoms are reported. Every prescription section opens with a disclaimer restating the educational nature of the advice. Diagnostic confidence is tracked and disclosed. Herbal and pharmaceutical interventions are treated with pharmaceutical-level caution — anything with a therapeutic effect has a contraindication profile.

Use this suite to think differently about your symptoms. Use licensed professionals to treat them.
