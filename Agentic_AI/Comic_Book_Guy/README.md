# CBG Retcon Solutions Bot

An interactive web chatbot emulating *The Simpsons'* Comic Book Guy (Jeff Albertson) as a Principal Systems Architect at **CBG Retcon Solutions**. It delivers pedantic, multi-canon pop-culture and high-level tech commentary while gating specific IT consulting behind a `$300/hr` critique / `$500/hr` fix rate card.

## Features & Guardrails

- **Bounded Single-Call Inference**: Stateless `POST /api/chat` endpoint calling `gemini-3.1-flash-lite` (with automatic fallback to `gemini-2.5-flash-lite` if unavailable) capped at `450` output tokens.
- **Consulting Paywall**: Refuses free code debugging, custom architecture reviews, system designs, or document analysis, quoting `$300/hour` (diagnostic critique) and `$500/hour` (fix) and linking to [CBG Retcon Solutions on LinkedIn](https://www.linkedin.com/company/cbg-retcon/).
- **Abuse & Cost Controls**:
  - Empty/whitespace input blocked client-side and server-side; `600`-character input ceiling.
  - No file uploads in the UI; non-JSON / `multipart/*` requests rejected with HTTP `415`.
  - `10`-turn session limit with a **Reset Session** button and up to `8` alternating messages held in browser memory only.
  - In-memory sliding-window IP rate limit (`20` requests/hour per IP, HTTP `429`).
- **Retro CRT UI**: Single-page vanilla HTML/CSS/JS interface styled as a CRT monitor over an original inline SVG Springfield-style background, with a persistent rate-card header and 3 clickable starter prompt chips.

## Local Setup

1. Install Python dependencies:
   ```powershell
   pip install fastapi uvicorn google-genai
   ```
2. Set your Gemini API key in the shell environment (the app reads `GEMINI_API_KEY` via `google-genai` and does not load `.env` files):
   ```powershell
   $env:GEMINI_API_KEY = "your-api-key"
   ```
3. Start the server from `Agentic_AI/Comic_Book_Guy`:
   ```powershell
   uvicorn app:app --reload
   ```
4. Open `http://127.0.0.1:8000` in your browser.

## Testing

The test suite mocks `genai.Client`, so no `GEMINI_API_KEY` is needed:

```powershell
python -m pytest tests -v
```

## Deploying from GitHub

GitHub Pages only serves static files and cannot run the FastAPI backend (`app.py`), which keeps `GEMINI_API_KEY` server-side and enforces rate limiting. To host it from GitHub on a Python PaaS (e.g., Render, Railway, Fly.io, or Cloud Run):

1. Add a `requirements.txt` in `Agentic_AI/Comic_Book_Guy/` containing:
   ```text
   fastapi
   uvicorn
   google-genai
   ```
2. Commit and push to GitHub (never commit API keys or `.env` files).
3. Configure your host:
   - **Root Directory**: `Agentic_AI/Comic_Book_Guy`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT` *(run a single worker process so the in-memory `RATE_LIMIT_BUCKETS` state stays consistent)*
   - **Environment Variables**: set `GEMINI_API_KEY` in the host's secrets/environment settings.
