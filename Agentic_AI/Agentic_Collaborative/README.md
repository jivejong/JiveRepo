# TMNT Agentic Poet

A compound, multimodal Streamlit workflow that turns an uploaded or camera-captured image into a short narrated performance. The TMNT framing makes the handoffs memorable, while each step owns a focused transformation.

## Agent pipeline

```text
Image -> Donatello: scene JSON -> Michelangelo: four-line poem
                                      ^
                                      | Leonardo rejects it (up to two rewrites)
                                      v
                  Raphael: mood -> Master Splinter: narration + local music
```

| Agent | Output |
| --- | --- |
| Donatello, Visionary | Structured image description, entities, and setting |
| Michelangelo, Bard | A four-line scene-based poem |
| Leonardo, Moderator | Relevance approval; first checks entity keywords locally, then uses semantic review only when needed |
| Raphael, Sentiment | One of `MELANCHOLY`, `WHIMSICAL`, `EPIC`, or `EERIE` |
| Master Splinter | `edge-tts` narration plus a matching local MP3 when available |

In Gemini mode, `gemini-3.1-flash-lite` performs image understanding and the text-agent steps. In Groq mode, the vision-capable `qwen/qwen3.8-27b` handles Donatello's image extraction and `openai/gpt-oss-120b` handles every downstream text agent. The image is downscaled before the vision request, and the primary action is deliberate: changing an uploader or widget does not start inference.

## Setup

```powershell
pip install -r requirements.txt
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
streamlit run app.py
```

Set `GENAI_API_KEY`, `GROQ_API_KEY`, and strong access codes in the copied file. The `audio_library/` folder contains mood-named MP3 files (`melancholy.mp3`, `whimsical.mp3`, `epic.mp3`, and `eerie.mp3`); narration still works if a matching music file is unavailable.

## Modes

The two top-level booleans in `.streamlit/secrets.toml` control the app:

```toml
secured = true # false bypasses the password gate and session usage limit
gemini = true  # false selects the Groq-backed pipeline
```

Both settings default to `true` when omitted, preserving the original secured Gemini behavior. Use TOML booleans (`true` or `false`) rather than quoted strings. GPT-OSS 120B is text-only, so Groq mode uses Groq's multimodal Qwen model only for the required image-to-scene step; all subsequent LLM work uses GPT-OSS 120B.

## Access and quota behavior

When `secured = true`, the private-demo gate runs before the provider client is initialized. The `owner` identity has unlimited calls; all other configured identities use `limits.max_llm_calls_per_session` (default: 20). Vision, poem, semantic moderation when needed, and mood classification consume quota. Local music lookup and Edge TTS do not. When `secured = false`, the password gate, usage counter, quota, and End session control are disabled.

Results remain in session state during ordinary reruns to avoid repeating inference. The session quota is a demo safeguard, not a substitute for user authentication or provider-side spending limits.

## Project layout

```text
app.py                  Streamlit UI and compound agent pipeline
security.py             access gate and per-session quota helpers
audio_library/          local mood-matched music assets
requirements.txt        application dependencies
.streamlit/             local secrets and the tracked safe template
```

## Concepts demonstrated

- Multimodal image-to-text-to-audio orchestration
- Explicit agent handoffs and closed-loop quality control
- Cost-aware local verification before a semantic model call
- Separate local-asset and remote-model responsibilities
