# No Cap Slang Check

A focused Streamlit classifier demo. Enter a slang term and the app asks the configured LLM for a structured cultural-relevance assessment, then renders the result as a custom themed card.

## Output contract

The cultural-linguist prompt asks for JSON with a verdict, 0-100 score, summary, origin, example, and optional warning. The verdict taxonomy is deliberately small:

| Verdict | Score | Interpretation |
| --- | --- | --- |
| `bussin` | 70-100 | Current and safe to use |
| `mid` | 35-69 | Fading or context-dependent |
| `unc` | 0-34 | Dead or likely to sound out of touch |

The application parses the response, handles provider and malformed-JSON errors in the UI, and provides example terms. It can use Gemini `gemini-3.1-flash-lite` or Groq `openai/gpt-oss-120b` through their REST APIs.

## Setup

```powershell
pip install streamlit requests
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
streamlit run nocap.py
```

Add `GEMINI_API_KEY`, `GROQ_API_KEY`, and strong access codes to the copied secrets file. Do not commit the local `secrets.toml` file.

## Modes

The two top-level booleans in `.streamlit/secrets.toml` control the app:

```toml
secured = true # false bypasses the password gate and session usage limit
gemini = true  # false selects GPT-OSS 120B through Groq
```

Both settings default to `true` when omitted, preserving the original secured Gemini behavior. Use TOML booleans (`true` or `false`) rather than quoted strings.

## Access and usage limit

When `secured = true`, the app asks for an access code before it loads the working interface or calls the selected provider. `owner` has unlimited calls; other configured codes use `limits.max_llm_calls_per_session` (default: 20). The quota applies to each requested assessment and resets with a new Streamlit session. The End session action clears access and session data. When `secured = false`, the password gate, usage counter, quota, and End session control are disabled.

This is lightweight portfolio-demo protection, not a full authentication or billing-control system. Use provider-side limits for a deployment that can receive public traffic.

## Project layout

```text
nocap.py                Streamlit UI, REST request, parsing, and rendering
security.py             access gate and session quota helpers
.streamlit/             local secrets and the tracked safe template
```

## Concepts demonstrated

- LLM-as-classifier rather than open-ended chat
- Prompt-constrained structured output and defensive JSON handling
- Small, explicit taxonomy and result contract
- Session-aware controls around paid model inference
