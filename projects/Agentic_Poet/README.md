# 📸 The Agentic Poet

A multimodal, compound agentic AI pipeline built with Streamlit. A photo is taken, then passed through a chain of specialized agents — vision analysis, poetry composition, quality moderation, mood detection, voice narration, and music selection — producing a live audio-visual performance.

**Demonstrates:** multimodal AI · compound agent pipelines · closed-loop retry · cross-model orchestration · agentic handoffs · TTS · local asset management

---

## What It Does

The user takes a photo. The pipeline runs automatically:

1. **Visionary Agent** — analyzes the image and extracts a structured scene description, entity list, and setting
2. **Bard Agent** — receives the Visionary's output and composes a 4-line poem referencing the scene
3. **Moderator Agent** — verifies the poem is actually relevant to the photo; if not, sends it back to the Bard (up to 2 retries)
4. **Sentiment Agent** — analyzes the approved poem's mood and selects a music category
5. **Narrator** — converts the poem to speech via gTTS
6. **Maestro** — loads a matching background music track from a local audio library

The result is a live performance: poem narration and background music played simultaneously.

---

## Agent Architecture

| Agent | Model | Role |
|---|---|---|
| 🔍 Visionary | Gemini 2.5 Flash | Image → structured scene data |
| ✍️ Bard | Groq · Llama 3.3 70B | Scene data → poem |
| ⚖️ Moderator | Groq · Llama 3.3 70B | Verify poem relevance; trigger retry if rejected |
| 🎭 Sentiment | Groq · Llama 3.3 70B | Poem → mood category |
| 🎙️ Narrator | gTTS (local) | Poem → voice audio |
| 🎵 Maestro | Local audio files | Mood → background music |

**Model split:** Gemini handles vision (Groq is text-only). All text agents run on Groq.

---

## Pipeline Flow

```
Photo
  └─▶ Visionary (Gemini) ──▶ scene data
        └─▶ Bard (Groq) ──▶ poem
              └─▶ Moderator (Groq) ──▶ verified?
                    ├── NO  ──▶ back to Bard (max 2 retries)
                    └── YES ──▶ Sentiment (Groq) ──▶ mood
                                  └─▶ Narrator (gTTS) + Maestro (local)
                                        └─▶ Combined Performance
```

---

## Moderation Logic

The Moderator uses a two-stage verification approach to minimize API calls:

- **Stage A (free):** Python keyword check — if any scene entity appears in the poem, it passes immediately
- **Stage B (LLM):** Semantic relevance check via Groq — only triggered if Stage A fails

---

## Mood Categories & Music

The Sentiment Agent classifies the poem into one of four moods:

| Mood | File Expected |
|---|---|
| `MELANCHOLY` | `audio_library/melancholy.mp3` |
| `WHIMSICAL` | `audio_library/whimsical.mp3` |
| `EPIC` | `audio_library/epic.mp3` |
| `EERIE` | `audio_library/eerie.mp3` |

If no matching file is found, the app plays voice narration only.

---

## Tech Stack

| Component | Technology |
|---|---|
| Vision LLM | Google Gemini 2.5 Flash (`google-generativeai`) |
| Text LLM | Groq API · `llama-3.3-70b-versatile` |
| Text-to-Speech | gTTS (Google Text-to-Speech, local) |
| Image processing | Pillow |
| UI | Streamlit |

---

## Setup

### 1. Install dependencies

```bash
pip install groq google-generativeai gtts pillow streamlit
```

### 2. Add API keys

Create `.streamlit/secrets.toml`:

```toml
GROQ_API_KEY   = "your-groq-api-key"
GENAI_API_KEY  = "your-gemini-api-key"
```

### 3. Add background music

Create an `audio_library/` directory in the project root and add MP3 files named by mood:

```
audio_library/
├── melancholy.mp3
├── whimsical.mp3
├── epic.mp3
└── eerie.mp3
```

### 4. Run the app

```bash
streamlit run app.py
```

---

## Project Structure

```
.
├── app.py                  # Main application and agent pipeline
├── audio_library/          # Local mood-matched background music
│   ├── melancholy.mp3
│   ├── whimsical.mp3
│   ├── epic.mp3
│   └── eerie.mp3
└── .streamlit/
    └── secrets.toml        # API keys
```

---

## Key Concepts Demonstrated

- **Compound agent pipeline** — six specialized agents each own a distinct step; no single agent handles the full task
- **Cross-model orchestration** — Gemini for vision, Groq for all text; the handoff between models is explicit and logged
- **Closed-loop retry** — the Moderator can reject the Bard's output and trigger a retry, creating a real feedback loop
- **Two-stage moderation** — cheap Python check before burning an LLM call, demonstrating cost-aware agent design
- **Multimodal input** — live camera feed processed as image input to a vision model
- **Local asset orchestration** — the Maestro pattern: mood-based lookup into a local file system rather than an API call
