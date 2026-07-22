# 💍 Spousal Approval System

A voice-driven agentic AI app built with Streamlit. The user speaks a potentially bad idea into the microphone. The app transcribes it, scores its marital risk, and routes it through an escalating response pipeline — from a calm spouse warning, to a friend intervention, to full spousal rage, to exile — with neural voice audio at every stage.

**Demonstrates:** speech-to-text · agentic state machines · escalation routing · neural TTS · multi-voice synthesis · session state management

---

## What It Does

1. User selects their spouse (Wife or Husband — affects voice personas and theming)
2. User records their idea via microphone
3. **Ear Agent** transcribes the audio using Whisper
4. **Logic Engine** scores the idea's marital risk on a 1–10 scale
5. The app routes to one of three outcomes based on score:

| Score | Route | What Happens |
|---|---|---|
| 1–2 | ✅ Safe | Idea is approved, no drama |
| 3–7 | ⚠️ Spouse Warning | Spouse delivers a pointed response; user can abort or escalate |
| 8–10 | 🚨 Friend Intervention | A friend calls to talk the user down; user can abort or escalate |

6. If the user escalates past a warning, the **Spouse Rage** stage fires — angrier response, last chance to abort
7. If the user doubles down through rage, the **Exile** stage triggers — spouse leaves, 20-second countdown, full session reset

---

## Stage Machine

```
choose_spouse
    └─▶ record_idea
          └─▶ review_transcript
                └─▶ evaluating
                      ├── score 1-2  ──▶ safe
                      ├── score 3-7  ──▶ spouse_warning ──▶ abort_success
                      │                       └─▶ (escalate) ──▶ spouse_rage ──▶ abort_success
                      │                                               └─▶ (double down) ──▶ exile
                      └── score 8-10 ──▶ friend_intervention ──▶ abort_success
                                              └─▶ (escalate) ──▶ spouse_rage ──▶ abort_success
                                                                      └─▶ (double down) ──▶ exile
```

---

## Agent & Component Roles

| Component | Role |
|---|---|
| 🎙️ Ear Agent | Transcribes voice input via Whisper large-v3 |
| ⚖️ Logic Engine | Scores idea severity 1–10 via Qwen3.6 27B |
| 💬 Spouse Agent | Generates in-character spouse response (tone scales with score) |
| 📞 Friend Agent | Generates urgent friend intervention call |
| 🔊 Voice Synthesizer | Converts all speech to neural audio via edge-tts |

---

## Voice Personas

Voice assignments change based on the selected spouse:

| Spouse | Spouse Voice | Friend Voice |
|---|---|---|
| Wife | `en-GB-SoniaNeural` | `en-US-ChristopherNeural` |
| Husband | `en-GB-RyanNeural` | `en-US-EmmaNeural` |

---

## Tech Stack

| Component | Technology |
|---|---|
| Speech-to-Text | Groq Whisper (`whisper-large-v3`) |
| LLM | Groq API · `qwen/qwen3.6-27b` |
| Text-to-Speech | Microsoft Neural voices via `edge-tts` |
| UI | Streamlit |
| Audio playback | Streamlit native audio with autoplay |
| Observability | OpenTelemetry (GenAI semantic conventions) — traces + metrics |

---

## Setup

### 1. Install dependencies

```bash
pip install groq edge-tts streamlit
```

### 2. Add your Groq API key

Create `.streamlit/secrets.toml`:

```toml
GROQ_API_KEY = "your-groq-api-key"
```

Alternatively, set it as an environment variable:

```bash
export GROQ_API_KEY="your-groq-api-key"
```

### 3. Run the app

```bash
streamlit run app.py
```

> **Note:** `edge-tts` requires an active internet connection to synthesize audio via Microsoft's neural voice service.

---

## Project Structure

```
.
├── app.py              # Main application and state machine
├── telemetry.py        # OpenTelemetry setup + GenAI-convention instrumentation
├── requirements.txt    # Dependencies (incl. OpenTelemetry)
└── .streamlit/
    └── secrets.toml    # API keys
```

---

## Sidebar

The sidebar has two live windows:

- **📊 Token Usage** — running input/output/total token counters for the session, with a per-call breakdown. Fed from the same `usage` block the OpenTelemetry spans read, so the in-app meter and exported metrics stay in sync.
- **🖥️ System Orchestration Trace** — a timestamped log of every agent action taken during the session. Useful for demoing agentic orchestration transparently.

Both can be cleared between runs.

> **Token note:** Qwen3.6 is a hybrid *reasoning* model, so thinking is **disabled** on every call (`reasoning_effort="none"`) — scoring just needs a number, and the speech calls are short creative quips. All calls are also capped with `max_completion_tokens`. This keeps token usage low, as the Token Usage meter shows.

---

## Observability

The full AI pipeline is instrumented with **OpenTelemetry**, following the
[GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).
Every AI operation becomes a properly attributed span, and token/latency
metrics are emitted alongside.

**What's traced** (see [telemetry.py](telemetry.py)):

| Operation | Span | Key attributes |
|---|---|---|
| Whisper transcription | `transcribe whisper-large-v3` | `gen_ai.system`, `gen_ai.operation.name`, `audio.input.bytes` |
| Risk scoring | `chat qwen/qwen3.6-27b` | `gen_ai.request.model`, `gen_ai.request.temperature`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`, `spousal.task` |
| Spouse / friend / rage speech | `chat qwen/qwen3.6-27b` | same as above + `spousal.score` |
| Neural TTS | `text_to_speech <voice>` | `tts.voice`, `tts.audio.bytes`, `tts.text.length` |
| Evaluation stage | `stage.evaluating` | `spousal.score`, `spousal.route` (nests the scoring LLM span) |

All spans share a per-session `gen_ai.conversation.id`, so one user's run reads
as a single correlated trace.

**Metrics:** `gen_ai.client.token.usage` (broken out by `gen_ai.token.type` =
`input` / `output`, plus `reasoning` if a model ever emits thinking tokens),
`gen_ai.client.operation.duration`, and `tts.audio.bytes`.

### Configuring the exporter

Zero config by default — spans and metrics print to the **console**:

```bash
streamlit run app.py   # traces/metrics printed to the terminal
```

To ship to any **OTLP backend** (Jaeger, Grafana Tempo/Cloud, Honeycomb, …),
just set the standard OpenTelemetry environment variables — no code change:

```bash
# Example: local Jaeger all-in-one (OTLP/HTTP on :4318)
docker run -d --name jaeger -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one:latest

export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
streamlit run app.py   # traces now visible at http://localhost:16686

# Example: Honeycomb
export OTEL_EXPORTER_OTLP_ENDPOINT="https://api.honeycomb.io"
export OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=<your-api-key>"
```

The instrumentation degrades gracefully: if the OpenTelemetry packages aren't
installed, the app still runs — telemetry calls become no-ops.

---

## Key Concepts Demonstrated

- **Agentic state machine** — the app moves through explicit named stages with guarded transitions, not a simple if/else flow
- **Escalation routing** — score-based branching sends the user to qualitatively different agent responses, not just different text
- **Speech-to-text pipeline** — Groq Whisper transcribes real microphone input; the user can review and edit the transcript before submission
- **Neural TTS with persona switching** — different Microsoft Neural voices assigned by role and spouse selection
- **Session state as agent memory** — Streamlit session state tracks the full conversation and prevents redundant LLM calls on re-render
- **Transparent orchestration log** — the sidebar traces every agent action with timestamps, making the agentic flow visible to observers
- **Production-grade observability** — OpenTelemetry GenAI-convention spans and metrics across STT, LLM, and TTS, exportable to any OTLP backend without code changes
