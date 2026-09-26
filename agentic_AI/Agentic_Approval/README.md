# The Bundy Approval Desk

A Streamlit, voice-driven state-machine demo styled as a *Married... with Children* household. A user records an idea, chooses Peg or Al Bundy as the household responder, and follows an escalating response route based on an LLM score. When Peg is the responder, Jefferson warns Al; when Al is the responder, Marcy warns Peg.

## Flow

```text
choose responder -> record idea -> review transcript -> evaluate
                                              |-> 1-2: safe
                                              |-> 3-7: friend -> spouse warning -> rage -> exile
                                              `-> 8-10: friend -> rage -> exile
```

At each intervention stage the user can abort, or choose to escalate. The final exile branch plays a countdown and resets the session. The score means marital risk for Peg and household compatibility for Al.

## Components

| Component | Responsibility |
| --- | --- |
| Ear Agent | Sends the WAV microphone recording to Gemini for transcription. |
| Logic Engine | Scores the reviewed idea from 1 to 10 and selects the route. |
| Spouse and Friend agents | Generate warnings from Peg or Al; Jefferson intervenes for Al, while Marcy intervenes for Peg. |
| Voice Synthesizer | Produces role-specific Microsoft Neural voice audio with `edge-tts`. |
| Telemetry | Emits OpenTelemetry traces and metrics for model, TTS, and workflow stages. |

All Gemini calls use `gemini-3.1-flash-lite`. Streamlit session state stores workflow stage, generated text, audio, tokens, and the sidebar orchestration trace.

## Setup

```powershell
pip install -r requirements.txt
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
streamlit run app.py
```

Add `GEMINI_API_KEY` and strong access codes to the copied file. The application also accepts `GEMINI_API_KEY` from the environment. Keep real credentials only in local or deployed Streamlit Secrets.

`edge-tts` uses Microsoft's online neural voice service, so speech synthesis requires network access.

## Access, quotas, and observability

The app checks its password-only demo gate before a model request can run. The configured `owner` code is unlimited; other identities use `limits.max_llm_calls_per_session` (default: 20). Transcription and each Gemini-generated score or response consume a call; TTS does not. The sidebar shows current access status and usage.

`telemetry.py` uses OpenTelemetry GenAI-style spans and metrics. With no exporter settings, it writes telemetry to the console; setting standard OTLP exporter environment variables sends it to a compatible collector. This is intentionally lightweight demo protection, not account management or persistent rate limiting.

## Project layout

```text
app.py                  workflow, Gemini calls, and audio interaction
security.py             access gate and quota controls
telemetry.py            OpenTelemetry setup and instrumentation helpers
requirements.txt        Streamlit, Gemini, edge-tts, and OpenTelemetry packages
.streamlit/             local secrets and the tracked safe template
```

## Concepts demonstrated

- Explicit state transitions and escalation routing
- Audio transcription, transcript review, and neural TTS
- Persona-aware output and voice assignment
- Session-scoped memory and transparent orchestration logging
- OpenTelemetry instrumentation across agentic operations
