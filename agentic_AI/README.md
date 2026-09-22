# Agentic AI Projects

Five independent Streamlit applications that demonstrate distinct agentic-AI patterns. The themes are playful; the implementations focus on explicit orchestration, controlled model calls, structured outputs, retrieval, multimodal interaction, and visible runtime behavior.

## Projects

| Project | Pattern | Summary |
| --- | --- | --- |
| [Agentic Adversarial](Agentic_Adversarial/) | Adversarial multi-agent RAG | Bart, Marge, and a rogue Homer negotiate snack choices against configurable household nutrition rules. |
| [Agentic Approval](Agentic_Approval/) | State-machine orchestration | A spoken idea is transcribed, scored, and routed through an escalating Bundy-household response flow. |
| [Agentic Collaborative](Agentic_Collaborative/) | Compound multimodal pipeline | A TMNT team turns an uploaded or camera image into a verified poem, narration, and mood-matched music. |
| [No Cap](No_Cap/) | Structured single-agent classification | A cultural-linguist prompt evaluates slang and returns a constrained `bussin` / `mid` / `unc` verdict. |
| [South Park Debate](South_Park_Debate/) | Multi-agent simulation | Configurable character debates produce a generated topic, debate transcript, independent judging, and final announcement. |

## Shared approach

All applications use Streamlit and Gemini `gemini-3.1-flash-lite`, configured through local Streamlit Secrets. They are intentionally independent: install dependencies and run commands from the individual project directory.

Each app includes a tracked `.streamlit/secrets.toml.example` template and a private-demo access gate. The `owner` access code is unlimited; other configured identities are subject to a per-session quota. This protects a portfolio demo from casual model spending, but is not a replacement for authentication, provider-side rate limits, or spending controls.

## Quick start

Choose a project, follow its README, then run its Streamlit entry point:

```powershell
cd Agentic_AI\Agentic_Adversarial
pip install -r requirements.txt
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
# Add your Gemini API key and strong access codes to the local secrets file.
streamlit run app.py
```

`No_Cap` has no requirements file; install `streamlit` and `requests`, then run `streamlit run nocap.py`.

## Techniques represented

- Agent handoffs and goal conflict: snack negotiation and character debate
- Explicit state and branching: the approval workflow
- Multimodal image-to-audio composition: the collaborative poet
- Structured JSON outputs and prompt-constrained classification: No Cap
- Retrieval with local embeddings and fallbacks: the snack negotiation app
- Session-aware quotas, deliberate user-triggered calls, and saved results to avoid accidental repeat inference
- OpenTelemetry traces and usage telemetry: the approval workflow and debate app

See the individual READMEs for architecture, configuration, and operational details.
