# 🔥 No Cap Slang Check

A single-agent AI app built with Streamlit. The user enters a Gen Z slang term and the app evaluates its current cultural relevance, returning a structured verdict, relevance score, origin, example usage, and cringe warning if applicable.

**Demonstrates:** LLM-as-classifier · structured JSON output · prompt engineering · custom Streamlit UI theming

---

## What It Does

The user types a slang term. The app sends it to Qwen3.6 27B with a specialized cultural linguist system prompt, which returns a structured verdict across five fields rendered as an animated result card.

---

## Verdict System

| Verdict | Score Range | Meaning |
|---|---|---|
| 🔥 `bussin` | 70–100 | Still very much in use — safe to deploy |
| 😬 `mid` | 35–69 | Fading or context-dependent — use with caution |
| 💀 `unc` | 0–34 | Dead slang — will make you sound like someone's uncle |

---

## Response Fields

Each evaluation returns:

| Field | Description |
|---|---|
| `verdict` | `bussin`, `mid`, or `unc` |
| `score` | Integer 0–100 relevance rating |
| `summary` | 2–3 sentence punchy cultural assessment |
| `origin` | Brief origin and peak era of the term |
| `example` | A natural example sentence using the term |
| `warning` | Optional cringe warning (present for `unc` and `mid` verdicts) |

---

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Groq API · `qwen/qwen3.6-27b` (via OpenAI-compatible endpoint) |
| UI | Streamlit |
| Fonts | Space Grotesk · Unbounded (Google Fonts) |

---

## Setup

### 1. Install dependencies

```bash
pip install streamlit requests
```

### 2. Add your Groq API key

Create `.streamlit/secrets.toml`:

```toml
GROQ_API_KEY = "your-groq-api-key"
```

### 3. Run the app

```bash
streamlit run nocap.py
```

---

## Project Structure

```
.
├── nocap.py            # Main application
└── .streamlit/
    └── secrets.toml    # API keys
```

---

## Error Handling

The app handles API failure cases explicitly with styled in-app error messages:

| Error | Message |
|---|---|
| `401 Unauthorized` | Invalid API key |
| `429 Too Many Requests` | Rate limit — retry prompt |
| Request timeout | Groq busy — retry prompt |
| JSON parse failure | Malformed response — retry prompt |

---

## Key Concepts Demonstrated

- **LLM-as-classifier** — the model doesn't just generate text; it returns a structured classification across a defined taxonomy (`bussin` / `mid` / `unc`)
- **Structured JSON output via prompt engineering** — the system prompt enforces strict JSON schema compliance without using a formal `response_format` parameter
- **Prompt persona design** — the system prompt constructs a specific expert identity ("cultural linguist who lives online") to anchor the model's judgment
- **OpenAI-compatible endpoint** — uses the Groq REST API directly via `requests` rather than the Groq SDK, demonstrating API-level integration
- **Custom Streamlit theming** — full dark-mode UI with animated gradients, custom fonts, and verdict-specific color theming built entirely in CSS injected via `st.markdown`
