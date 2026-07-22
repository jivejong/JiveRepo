# 🤖 Agentic AI Projects

A collection of small, self-contained [Streamlit](https://streamlit.io/) apps, each demonstrating a different pattern in agentic AI — from single-agent classifiers to multi-agent negotiations, compound multimodal pipelines, and voice-driven state machines. Every project is playful on the surface but built to showcase a specific, reusable technique underneath.

---

## The Projects

| Project                                | One-liner                                                                                      | Agent Pattern                        |
| -------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------ | ------------- |
| [📸 Agentic Poet](Agentic_Poet/)       | Take a photo → a chain of agents turns it into a narrated poem with mood-matched music         | Compound multimodal pipeline         |
| [🍎 Agentic Snacks](Agentic_Snacks/)   | Three agents negotiate whether a kid gets a snack — with a rogue grandparent pushing junk food | Adversarial multi-agent + Vector RAG |
| [🔥 NoCap](NoCap/)                     | Enter Gen Z slang → get a structured verdict on whether it's still cool                        | Single-agent classifier              |
| [💍 Spouse Approval](Spouse_Approval/) | Speak a bad idea → it's scored and routed through an escalating spousal response pipeline      | Voice-driven state machine           | OpenTelemetry |

---

## Project Details

### 📸 [Agentic Poet](Agentic_Poet/)

A photo passes through six specialized agents — vision analysis (Gemini), poetry composition, quality moderation with closed-loop retry, mood detection, voice narration (gTTS), and music selection from a local library — producing a live audio-visual performance.

**Highlights:** cross-model orchestration (Gemini for vision, Groq for text) · two-stage moderation (cheap Python check before an LLM call) · closed-loop retry when the poem doesn't match the photo.

### 🍎 [Agentic Snacks](Agentic_Snacks/)

Three LLM agents negotiate in real time over a snack request, with a rogue Grandparent agent that randomly hijacks the negotiation to push high-sugar alternatives. The Parent agent pre-screens items and enforces configurable nutrition limits.

**Highlights:** adversarial agents working against each other · three-tier RAG (Chroma vector DB → web search → model knowledge) over ~7,400 embedded food items · LLM-as-classifier pre-screening.

### 🔥 [NoCap](NoCap/)

A single-agent app that evaluates a Gen Z slang term's current cultural relevance, returning a structured verdict (`bussin` / `mid` / `unc`), a relevance score, origin, example usage, and a cringe warning.

**Highlights:** structured JSON output enforced purely through prompt engineering · prompt-persona design · direct REST integration via an OpenAI-compatible endpoint · fully custom CSS-themed Streamlit UI.

### 💍 [Spouse Approval](Spouse_Approval/)

The user speaks a potentially bad idea; the app transcribes it (Whisper), scores its marital risk 1–10, and routes it through an escalating pipeline — calm warning → friend intervention → spousal rage → exile — with neural voice audio (edge-tts) at every stage.

**Highlights:** explicit agentic state machine with guarded transitions · speech-to-text and multi-voice neural TTS · production-grade OpenTelemetry observability (GenAI semantic conventions) exportable to any OTLP backend.

---

## Common Stack

All four projects share a consistent foundation:

| Layer              | Technology                             |
| ------------------ | -------------------------------------- |
| UI                 | Streamlit                              |
| Primary LLM        | Groq API (Qwen3.6 27B / Llama 3.3 70B) |
| Vision (Poet only) | Google Gemini 2.5 Flash                |
| Secrets            | `.streamlit/secrets.toml`              |

Each project also draws on task-specific tooling — Chroma + MiniLM embeddings (Snacks), gTTS and a local audio library (Poet), Whisper + edge-tts (Spouse Approval), and OpenTelemetry instrumentation (Spouse Approval).

---

## Running Any Project

Each project is independent and follows the same pattern:

```bash
cd <project-folder>
pip install -r requirements.txt        # or the pip line in that project's README
```

Add a Groq API key (and a Gemini key for the Poet) to `.streamlit/secrets.toml`:

```toml
GROQ_API_KEY  = "your-groq-api-key"
# GENAI_API_KEY = "your-gemini-api-key"   # Agentic Poet only
```

Then run it:

```bash
streamlit run app.py                   # NoCap uses nocap.py
```

See each project's own `README.md` for setup details, configuration options, and the concepts it demonstrates.

---

## Concepts Across the Collection

Taken together, these projects cover a broad slice of agentic AI patterns:

- **Single-agent classification** with structured output (NoCap)
- **Compound pipelines** where specialized agents each own one step (Poet)
- **Adversarial multi-agent negotiation** with competing goals (Snacks)
- **Agentic state machines** with escalation routing (Spouse Approval)
- **RAG** — multi-tier retrieval with graceful fallback (Snacks)
- **Multimodal input** — vision and voice as first-class inputs (Poet, Spouse Approval)
- **Cost-aware design** — cheap checks before expensive LLM calls, disabled reasoning, token caps
- **Observability** — OpenTelemetry GenAI-convention traces and metrics (Spouse Approval)
