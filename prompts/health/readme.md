# Health: Ancient Wisdom for Holistic Health

Modern digital health treats the human body like a deterministic machine: count the calories, track the steps, optimize the sleep score, patch the symptom. But humans are not linear machines; we are biological and relational ecosystems.

Large Language Models are uniquely suited to understand ecosystems. Because they are probabilistic inference engines trained on global corpuses of text, they possess the latent ability to reason relationally, holistically, and cyclically. However, standard system prompts force them into Western, reductionist, Silicon Valley-style therapy and productivity modes.

This folder contains a suite of AI prompt architectures that activate ancient, holistic epistemologies. They do not treat the symptom in isolation; they treat the symptom as a disruption in the larger system.

## The Core Architecture: The "Stateful Two-Bot" System

Holistic medicine rarely treats an acute symptom without first understanding the patient's lifelong constitution. To replicate this in stateless LLMs, every tradition in this folder is split into two distinct agents, connected by an XML "passport":

1. **Bot 1: The Assessor (The Baseline).** Run once. The user answers a holistic questionnaire to determine their constitutional baseline (their "hardware"). The bot outputs a standardized XML passport (e.g., `<ayurvedic_energetic_passport>`).
2. **Bot 2: The Advisor (The Acute).** The daily-use agent. The user injects their XML passport alongside their current struggle. The bot reads the acute symptom _through the lens of the baseline_, ensuring interventions are perfectly calibrated to the user's constitution.

## The Six Traditions

These prompts cover physical, energetic, psychological, and relational ecosystems.

### 1. Traditional Chinese Medicine (TCM)

- **The Engine:** Somatic flow and environmental harmony.
- **The Mechanic:** Assesses the user's Nine Constitutions baseline (e.g., Yin Deficiency, Phlegm-Dampness). The Advisor uses acupressure, dietary therapy, and Qi-regulation to move stagnation and tonify deficiency.

### 2. Ayurveda & The Chakras (Yogic Science)

- **The Engine:** Metabolic hardware and energetic software.
- **The Mechanic:** Combines the physical Dosha (Vata, Pitta, Kapha) with the energetic Chakra holding patterns. The Advisor prescribes specific _Asana_ (postures), _Pranayama_ (breathwork), and _Mantra_ tailored to ensure a grounding practice for a Vata doesn't accidentally burn out a Pitta.

### 3. Galenic Medicine (Classical Humors)

- **The Engine:** The Law of Contraries and environmental levers.
- **The Mechanic:** Assesses the user's elemental Temperament (Sanguine, Choleric, Melancholic, Phlegmatic). The Advisor uses the "Six Non-Naturals" (diet, sleep, environment, exercise, excretion, and emotion) to apply cooling/moistening interventions to hot/dry imbalances, restoring _eucrasia_ (equilibrium).

### 4. Ilm al-Nafs (Islamic Golden Age Psychology)

- **The Engine:** Metacognition and impulse control.
- **The Mechanic:** Rooted in early Cognitive Behavioral Therapy, this assesses the user's stage of the _Nafs_ (Ego) versus their _Aql_ (Intellect). The Advisor prescribes _Tazkiyah_ (refinement) exercises like _Muraqaba_ (mindfulness) to help the intellect master the reactive ego.

### 5. Ubuntu (Southern African Relational Ecology)

- **The Engine:** Relational systems theory. "I am because we are."
- **The Mechanic:** Rejects the Western premise of isolated "self-care." The Assessor maps the user's relational web (community, intimacy, nature, lineage). When the user is burned out or anxious, the Advisor prescribes _network-care_—actions of vulnerability, service, or connection to repair the frayed node in the web.

### 6. The Medicine Wheel (Native American)

- **The Engine:** Cyclical completeness and multidimensional integration.
- **The Mechanic:** Assesses which of the four quadrants (Physical, Mental, Emotional, Spiritual) the user over-relies on. The Advisor refuses linear, single-quadrant fixes. If a user tries to solve an emotional crisis with mental planning, the bot forces them to "walk the wheel," completing one action in all four directions to close the circle.

## The LLM Safety Mechanism: The `<scratchpad>`

Modern instruction-tuned LLMs are desperate to be helpful in a modern, Western way. If a user complains of burnout, the LLM will default to saying: "Set boundaries, take a mental health day, and practice self-care."

To prevent this, every prompt in this folder utilizes a forced `<scratchpad>` containment zone.
Before speaking to the user, the AI must emit a hidden XML block where it explicitly contrasts the "Western Reframe" with the specific ancient tradition (e.g., `secret_diagnosis: How does Ubuntu view this?`). By venting its default training into the scratchpad, the AI maintains strict, unadulterated epistemological discipline in the user-facing output.

---

_Disclaimer: These architectures are experimental applications of historical and cultural frameworks to modern lifestyle habits. They are designed for educational self-reflection and systemic thinking. They do not replace professional biomedical or psychological treatment._
