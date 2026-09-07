import streamlit as st
import requests
import json
import re
from urllib.parse import quote

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="No Cap Slang Check",
    page_icon="🔥",
    layout="centered",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Unbounded:wght@400;700;900&display=swap');

/* Reset & base */
html, body, [class*="css"] {
    font-family: 'Space Grotesk', sans-serif;
}

.stApp {
    background: #0a0a0f;
    color: #f0f0f0;
}

/* Hide default streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }

/* ── Hero header ── */
.hero {
    text-align: center;
    padding: 3rem 0 2rem;
}
.hero-emoji {
    font-size: 4rem;
    display: block;
    animation: bounce 2s ease-in-out infinite;
}
@keyframes bounce {
    0%, 100% { transform: translateY(0); }
    50%       { transform: translateY(-12px); }
}
.hero-title {
    font-family: 'Unbounded', sans-serif;
    font-size: 2.6rem;
    font-weight: 900;
    background: linear-gradient(135deg, #ff6b35, #f7c59f, #ff6b35);
    background-size: 200% auto;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    animation: shimmer 3s linear infinite;
    margin: 0.5rem 0 0.25rem;
    letter-spacing: -1px;
}
@keyframes shimmer {
    to { background-position: 200% center; }
}
.hero-sub {
    font-size: 1rem;
    color: #888;
    letter-spacing: 0.04em;
    margin-bottom: 0;
}

/* ── Input card ── */
.input-card {
    background: #13131a;
    border: 1px solid #2a2a3a;
    border-radius: 20px;
    padding: 2rem 2rem 1.5rem;
    margin: 1.5rem 0;
    box-shadow: 0 0 40px rgba(255, 107, 53, 0.05);
}

/* Override streamlit text input */
.stTextInput > div > div > input {
    background: #0d0d14 !important;
    border: 2px solid #2a2a3a !important;
    border-radius: 14px !important;
    color: #f0f0f0 !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-size: 1.3rem !important;
    font-weight: 600 !important;
    padding: 1rem 1.2rem !important;
    transition: border-color 0.2s ease !important;
}
.stTextInput > div > div > input:focus {
    border-color: #ff6b35 !important;
    box-shadow: 0 0 0 3px rgba(255, 107, 53, 0.15) !important;
    outline: none !important;
}
.stTextInput > label {
    color: #888 !important;
    font-size: 0.8rem !important;
    letter-spacing: 0.1em !important;
    text-transform: uppercase !important;
}

/* ── Button ── */
.stButton > button,
.stFormSubmitButton > button {
    background: linear-gradient(135deg, #ff6b35, #e84a1e) !important;
    color: white !important;
    border: none !important;
    border-radius: 14px !important;
    font-family: 'Unbounded', sans-serif !important;
    font-size: 0.85rem !important;
    font-weight: 700 !important;
    padding: 0.85rem 2.5rem !important;
    letter-spacing: 0.05em !important;
    text-transform: uppercase !important;
    width: 100% !important;
    cursor: pointer !important;
    transition: all 0.2s ease !important;
    box-shadow: 0 4px 20px rgba(255, 107, 53, 0.35) !important;
}
.stButton > button:hover,
.stFormSubmitButton > button:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 30px rgba(255, 107, 53, 0.5) !important;
}
.stButton > button:active,
.stFormSubmitButton > button:active {
    transform: translateY(0px) !important;
}

/* Reset button — ghost style */
.st-key-reset_btn button {
    background: transparent !important;
    border: 2px solid #2a2a3a !important;
    color: #666 !important;
    box-shadow: none !important;
}
.st-key-reset_btn button:hover {
    border-color: #f87171 !important;
    color: #f87171 !important;
    box-shadow: none !important;
    transform: none !important;
}

/* ── Result cards ── */
.result-card {
    border-radius: 20px;
    padding: 1.8rem 2rem;
    margin: 1.5rem 0;
    position: relative;
    overflow: hidden;
    animation: fadeUp 0.5s ease forwards;
}
@keyframes fadeUp {
    from { opacity: 0; transform: translateY(16px); }
    to   { opacity: 1; transform: translateY(0); }
}

.result-card.bussin {
    background: linear-gradient(135deg, #0d2218, #0a1a12);
    border: 1px solid #1a4a2e;
}
.result-card.unc {
    background: linear-gradient(135deg, #2a0d0d, #1a0a0a);
    border: 1px solid #4a1a1a;
}
.result-card.mid {
    background: linear-gradient(135deg, #1a1a0d, #12120a);
    border: 1px solid #3a3a1a;
}

.verdict-badge {
    display: inline-block;
    font-family: 'Unbounded', sans-serif;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    padding: 0.35rem 1rem;
    border-radius: 30px;
    margin-bottom: 1rem;
}
.badge-bussin  { background: #1a4a2e; color: #4ade80; }
.badge-unc     { background: #4a1a1a; color: #f87171; }
.badge-mid     { background: #3a3a1a; color: #fbbf24; }

.verdict-term {
    font-family: 'Unbounded', sans-serif;
    font-size: 2rem;
    font-weight: 900;
    margin: 0.25rem 0 1rem;
}
.verdict-term.bussin { color: #4ade80; }
.verdict-term.unc    { color: #f87171; }
.verdict-term.mid    { color: #fbbf24; }

.verdict-body {
    font-size: 1rem;
    line-height: 1.75;
    color: #c0c0c0;
}

.verdict-score {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-top: 1.25rem;
    padding-top: 1.25rem;
    border-top: 1px solid rgba(255,255,255,0.07);
}
.score-label {
    font-size: 0.75rem;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    white-space: nowrap;
}
.score-bar-bg {
    flex: 1;
    height: 6px;
    background: #1e1e2a;
    border-radius: 99px;
    overflow: hidden;
}
.score-bar-fill {
    height: 100%;
    border-radius: 99px;
    transition: width 1s ease;
}
.score-num {
    font-family: 'Unbounded', sans-serif;
    font-size: 0.9rem;
    font-weight: 700;
    min-width: 2.5rem;
    text-align: right;
}

/* ── Spinner override ── */
.stSpinner > div { border-top-color: #ff6b35 !important; }

/* ── Error ── */
.custom-error {
    background: #2a0d0d;
    border: 1px solid #4a1a1a;
    border-radius: 14px;
    padding: 1rem 1.25rem;
    color: #f87171;
    font-size: 0.9rem;
}

/* ── Examples ── */
.examples-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.75rem;
}
.example-chip {
    display: inline-block;
    background: #1a1a2a;
    border: 1px solid #2a2a3a;
    border-radius: 30px;
    padding: 0.3rem 0.9rem;
    font-size: 0.8rem;
    color: #aaa;
    cursor: pointer;
    transition: all 0.15s ease;
    font-weight: 500;
    text-decoration: none;
}
.example-chip:hover {
    background: #26263a;
    border-color: #ff4d6d;
    color: #fff;
}

/* ── Footer ── */
.footer {
    text-align: center;
    color: #444;
    font-size: 0.75rem;
    padding: 2rem 0 1rem;
    letter-spacing: 0.05em;
}
</style>
""", unsafe_allow_html=True)


# ── Session state ────────────────────────────────────────────────────────────
if "result" not in st.session_state:
    st.session_state.result = None
if "slang_term" not in st.session_state:
    st.session_state.slang_term = ""
if "trigger_search" not in st.session_state:
    st.session_state.trigger_search = False

def reset():
    st.session_state.result = None
    st.session_state.slang_term = ""
    st.session_state.trigger_search = False
    st.query_params.clear()

# ── Helper: call Groq API ─────────────────────────────────────────────────────
def check_slang(term: str) -> dict:
    """Call the Groq API (Qwen3.6 27B) to evaluate a slang term."""
    api_key = st.secrets["GROQ_API_KEY"]

    system_prompt = """You are the ultimate authority on Gen Z slang — a cultural linguist who lives online and knows exactly what's fire and what's cringe. 

When given a slang term, analyze its current cultural relevance and usage among actual Gen Z (born 1997–2012) as of 2024–2025.

Respond ONLY with a valid JSON object in this exact format:
{
  "verdict": "bussin" | "mid" | "unc",
  "score": <integer 0-100>,
  "summary": "<2-3 sentence punchy explanation of whether/how to use it>",
  "origin": "<brief origin/peak era>",
  "example": "<a natural example sentence using the slang>",
  "warning": "<optional: specific cringe warning if verdict is unc or mid, else null>"
}

Verdicts:
- "bussin" = still very much in use, won't make you look out of touch (score 70-100)
- "mid" = fading or contextual — risky to use, depends on your crowd (score 35-69)  
- "unc" = dead, cringe, or will make you sound like someone's uncle trying too hard (score 0-34)

Be honest, be harsh if needed, be funny. This is serious slang business."""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "model": "qwen/qwen3.6-27b",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f'Evaluate this slang term: "{term}"'},
        ],
        "temperature": 0.7,
        # Reasoning models spend tokens on hidden reasoning *before* the
        # answer, and those count against this budget. 500 truncated the
        # JSON mid-object; give it real headroom so the object completes.
        "max_tokens": 4096,
        # Force valid JSON output — qwen3.6 is a reasoning model and will
        # otherwise wrap the JSON in a <think>…</think> preamble.
        "response_format": {"type": "json_object"},
        # Required with JSON mode on a reasoning model: Groq rejects the
        # default "raw" reasoning with a 400. "hidden" drops the reasoning
        # entirely so `content` is pure JSON.
        "reasoning_format": "hidden",
    }

    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]

    # Fallback: strip any reasoning preamble the model may still emit.
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    # Strip markdown fences if present
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    content = content.strip()

    return json.loads(content)


# ── App layout ────────────────────────────────────────────────────────────────

st.markdown("""
<div class="hero">
  <span class="hero-emoji">🔥</span>
  <div class="hero-title">NO CAP SLANG CHECK</div>
  <div class="hero-sub">is your vocabulary bussin or are you an unc?</div>
</div>
""", unsafe_allow_html=True)

# Main input
#st.markdown('<div class="input-card">', unsafe_allow_html=True)

# A clicked example chip arrives here as a ?q= query param — load it into the
# input and queue a search, then clear the param so the same chip can be
# re-clicked later.
chip_query = st.query_params.get("q")
if chip_query:
    st.session_state.slang_term = chip_query
    st.session_state.trigger_search = True
    st.query_params.clear()

# Wrapping the input + submit button in a form makes Enter submit it.
with st.form("check_form"):
    slang_input = st.text_input(
        "SLANG TERM",
        placeholder='e.g. "rizz", "no cap", "slay"...',
        max_chars=60,
        key="slang_term",
        label_visibility="collapsed",
    )

    EXAMPLE_TERMS = ["rizz", "no cap", "bussin", "GOAT", "lowkey", "yeet", "slay", "on fleek"]
    chips_html = "".join(
        f'<a class="example-chip" href="?q={quote(term)}" target="_self">{term}</a>'
        for term in EXAMPLE_TERMS
    )
    st.markdown(f"""
<div style="margin: 0.5rem 0 1rem; color: #555; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em;">
Try these ↓
</div>
<div class="examples-row">{chips_html}</div>
<br>
""", unsafe_allow_html=True)

    check_btn = st.form_submit_button("CHECK THE VIBE →", use_container_width=True)

reset_btn = st.button("RESET", on_click=reset, use_container_width=True, key="reset_btn")

# ── Result ────────────────────────────────────────────────────────────────────
# Run on button/Enter, or when a clicked example chip queued a search.
do_check = check_btn or st.session_state.trigger_search
st.session_state.trigger_search = False
if do_check:
    if not slang_input.strip():
        st.markdown('<div class="custom-error">😭 Type a slang term first... we can\'t read minds (yet).</div>', unsafe_allow_html=True)
    else:
        with st.spinner("Consulting the culture..."):
            try:
                st.session_state.result = check_slang(slang_input.strip())
                st.session_state.result["_term"] = slang_input.strip()
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                if status == 401:
                    st.markdown('<div class="custom-error">🔑 Invalid API key. Check your .streamlit/secrets.toml file.</div>', unsafe_allow_html=True)
                elif status == 403:
                    st.markdown('<div class="custom-error">🚫 Groq blocked this network (403). Turn off any VPN/proxy and try again.</div>', unsafe_allow_html=True)
                elif status == 429:
                    st.markdown('<div class="custom-error">🚦 Rate limit hit. Chill for a sec and try again.</div>', unsafe_allow_html=True)
                else:
                    detail = ""
                    try:
                        detail = e.response.json().get("error", {}).get("message", "")
                    except Exception:
                        detail = e.response.text if e.response is not None else ""
                    st.markdown(f'<div class="custom-error">💀 API error {status}: {detail or str(e)}</div>', unsafe_allow_html=True)
            except requests.exceptions.Timeout:
                st.markdown('<div class="custom-error">⏱️ Request timed out. Groq is busy rn, try again.</div>', unsafe_allow_html=True)
            except json.JSONDecodeError:
                st.markdown('<div class="custom-error">😵 Couldn\'t parse the response. The AI said something unhinged. Try again.</div>', unsafe_allow_html=True)
            except Exception as e:
                st.markdown(f'<div class="custom-error">Something went unexpectedly wrong: {str(e)}</div>', unsafe_allow_html=True)

if st.session_state.result:
    result = st.session_state.result
    verdict = result.get("verdict", "mid").lower()
    score = result.get("score", 50)
    summary = result.get("summary", "")
    origin = result.get("origin", "")
    example = result.get("example", "")
    warning = result.get("warning")
    term = result.get("_term", "")

    if verdict == "bussin":
        bar_color = "#4ade80"
        verdict_emoji = "🔥"
    elif verdict == "unc":
        bar_color = "#f87171"
        verdict_emoji = "💀"
    else:
        bar_color = "#fbbf24"
        verdict_emoji = "😬"

    st.markdown(f"""
<div class="result-card {verdict}">
  <span class="verdict-badge badge-{verdict}">{verdict_emoji} {verdict.upper()}</span>
  <div class="verdict-term {verdict}">"{term}"</div>
  <div class="verdict-body">{summary}</div>

  {'<div style="margin-top:1rem; padding: 0.75rem 1rem; background:rgba(248,113,113,0.08); border-radius:10px; color:#f87171; font-size:0.88rem;">⚠️ ' + warning + '</div>' if warning else ''}

  <div style="margin-top: 1.25rem; display:grid; grid-template-columns:1fr 1fr; gap:1rem;">
    <div style="background:rgba(255,255,255,0.03); border-radius:12px; padding:0.9rem 1rem;">
      <div style="font-size:0.7rem; color:#555; text-transform:uppercase; letter-spacing:0.1em; margin-bottom:0.4rem;">Origin / Era</div>
      <div style="font-size:0.9rem; color:#ddd; font-weight:500;">{origin}</div>
    </div>
    <div style="background:rgba(255,255,255,0.03); border-radius:12px; padding:0.9rem 1rem;">
      <div style="font-size:0.7rem; color:#555; text-transform:uppercase; letter-spacing:0.1em; margin-bottom:0.4rem;">Example</div>
      <div style="font-size:0.9rem; color:#ddd; font-style:italic;">"{example}"</div>
    </div>
  </div>

  <div class="verdict-score">
    <span class="score-label">Relevance</span>
    <div class="score-bar-bg">
      <div class="score-bar-fill" style="width:{score}%; background:{bar_color};"></div>
    </div>
    <span class="score-num" style="color:{bar_color};">{score}</span>
  </div>
</div>
""", unsafe_allow_html=True)

st.markdown("""
<div class="footer">
  POWERED BY GROQ · BUILT FOR THE CULTURE · NO UNC ENERGY ALLOWED
</div>
""", unsafe_allow_html=True)
