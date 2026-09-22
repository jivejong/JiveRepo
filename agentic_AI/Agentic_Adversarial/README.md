# Simpson Snack Negotiation

A Streamlit demonstration of adversarial agents and retrieval-augmented decision making. Bart asks for a snack, Homer may redirect the request toward junk food, and Marge applies configurable household nutrition rules before approving or denying it.

## Workflow

1. Bart makes a structured case for the requested snack.
2. Homer can intervene with a high-sugar alternative.
3. Marge pre-screens the item, retrieves nutrition information, and applies sugar and fat thresholds.
4. When denied, Bart selects an alternative until the configured round limit is reached.

The rendered transcript includes each agent's response and optional inner monologue. Completed negotiations are retained in Streamlit session state so normal UI reruns do not repeat model calls.

## Retrieval design

Nutrition lookup follows a tiered fallback path:

1. A persistent Chroma collection, built from `nutritional_data.csv` with local ONNX `all-MiniLM-L6-v2` embeddings.
2. Gemini with Google Search grounding when the semantic match is not confident enough.
3. Model-knowledge fallback if the grounded lookup does not produce usable nutrition data.

The local data source supplies `Category`, `Description`, `Cholesterol`, and `Sugar`. This demonstration treats `Cholesterol` as its fat proxy; it is a project-specific simplification, not nutritional guidance.

## Setup

```powershell
pip install -r requirements.txt
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
streamlit run app.py
```

Set a real `GEMINI_API_KEY` and strong access codes in the copied secrets file. `GEMINI_MODEL` is optional and defaults to `gemini-3.1-flash-lite`. Do not commit `.streamlit/secrets.toml`.

The first run creates or populates `chroma_db/`; later runs reuse it. The sidebar exposes the household sugar/fat thresholds, semantic-match threshold, and maximum negotiation rounds.

## Access and quota behavior

The access-code screen runs before model work. The configured `owner` identity is unlimited; other configured codes use `limits.max_llm_calls_per_session` (default: 20). Gemini generations, Google Search-grounded requests, and post-search extraction consume quota. Local embeddings do not.

This is a per-session portfolio-demo safeguard, not user authentication or durable rate limiting. Configure provider-side budgets and limits separately.

## Project layout

```text
app.py                  Streamlit app, RAG pipeline, and agent orchestration
security.py             access gate, quota accounting, and session controls
nutritional_data.csv    local pantry source data
chroma_db/              generated persistent vector index
.streamlit/             local secrets and the tracked safe template
```

## Concepts demonstrated

- Competing goals and agent handoffs
- Local semantic retrieval with quality-based fallbacks
- LLM pre-screening before richer agent work
- Explicit cost control and session-safe inference
