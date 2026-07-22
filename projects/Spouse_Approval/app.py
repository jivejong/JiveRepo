import streamlit as st
import os
import re
import time
import json
import tempfile
import base64
import asyncio
import uuid
import edge_tts
import io
from pathlib import Path
from groq import Groq

try:
    from groq import BadRequestError
except ImportError:  # very old SDs — fall back to a broad catch for the retry
    BadRequestError = Exception

from telemetry import (
    init_telemetry,
    genai_span,
    record_llm_response,
    record_tts_audio,
    app_span,
)

# ── OpenTelemetry ────────────────────────────────────────────────────────────
# Idempotent: safe to call on every Streamlit rerun. Exports to the console by
# default, or to any OTLP backend when OTEL_EXPORTER_OTLP_ENDPOINT is set.
init_telemetry()

LLM_MODEL = "qwen/qwen3.6-27b"
STT_MODEL = "whisper-large-v3"


def _session_id() -> str:
    """Stable per-session id used to correlate all spans of one user's run."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid.uuid4().hex
    return st.session_state.session_id


# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Spousal Approval System",
    page_icon="💍",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ── Inject CSS ────────────────────────────────────────────────────────────────
_spouse = st.session_state.get("spouse", None)

if _spouse == "husband":
    _theme_vars = """
  --bg:        #f5f0eb;
  --surface:   #ffffff;
  --sidebar:   #ede7e0; /* Matches the light theme */
  --border:    #d0c4b8;
  --gold:      #8b6914; /* Deep Bronze */
  --gold-dim:  #b8922a;
  --rose:      #c0392b;
  --cream:     #2c2018;
  --muted:     #7a6a5a;
  --danger:    #c0392b;
  --friend:    #1a6fa8;
  --safe:      #1e8449;
  --btn-bg:    #8b6914; 
  --btn-text:  #ffffff;
"""
else:
    _theme_vars = """
  --bg:        #f5f0eb;
  --surface:   #ffffff;
  --sidebar:   #ede7e0; /* Matches the light theme */
  --border:    #d0c4b8;
  --gold:      #8b6914; /* Deep Bronze */
  --gold-dim:  #b8922a;
  --rose:      #c0392b;
  --cream:     #2c2018;
  --muted:     #7a6a5a;
  --danger:    #c0392b;
  --friend:    #1a6fa8;
  --safe:      #1e8449;
  --btn-bg:    #8b6914; 
  --btn-text:  #ffffff;
"""

st.markdown(f"""
<style>
:root {{
  {_theme_vars}
}}

/* Main App Background */
[data-testid="stAppViewContainer"] {{
  background: var(--bg) !important;
}}

/* Sidebar Background and Borders */
[data-testid="stSidebar"] {{
  background-color: var(--sidebar) !important;
  border-right: 1px solid var(--border);
}}

/* High-Visibility Button Overrides */
div.stButton > button {{
    background-color: var(--btn-bg) !important;
    color: var(--btn-text) !important;
    border-radius: 4px;
    border: none;
    font-weight: bold;
    transition: all 0.3s ease;
}}

div.stButton > button:hover {{
    box-shadow: 0px 4px 15px var(--gold-dim);
    transform: translateY(-1px);
}}

/* Ensure text area and expanders inside sidebar match theme */
[data-testid="stSidebar"] .stExpander {{
    background-color: var(--surface) !important;
    border: 1px solid var(--border) !important;
}}

/* General Font Styling */
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {{
  font-family: 'Crimson Pro', Georgia, serif;
  color: var(--cream);
}}
</style>
""", unsafe_allow_html=True)

# ── Helpers ──────────────────────────────────────────────────────────────────

def add_log(agent, action, detail=""):
    """Logs system orchestration events to the session state."""
    if "logs" not in st.session_state:
        st.session_state.logs = []
    timestamp = time.strftime("%H:%M:%S")
    st.session_state.logs.insert(0, {
        "time": timestamp,
        "agent": agent.upper(),
        "action": action,
        "detail": detail
    })

def get_voice_mapping(spouse: str) -> dict:
    """Maps roles to specific Microsoft Neural voices."""
    if spouse == "wife":
        return {"spouse": "en-GB-SoniaNeural", "friend": "en-US-ChristopherNeural"}
    return {"spouse": "en-GB-RyanNeural", "friend": "en-US-EmmaNeural"}

def text_to_speech(text: str, voice: str) -> bytes:
    """Optimized: Generates neural audio bytes faster by bypassing manual chunking."""
    async def _generate():
        communicate = edge_tts.Communicate(text, voice)
        # We use a temporary buffer to stream the data directly
        output = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                output.write(chunk["data"])
        return output.getvalue()

    with genai_span(
        "text_to_speech",
        voice,
        system="edge-tts",
        conversation_id=_session_id(),
        extra_attrs={"tts.text.length": len(text)},
    ) as span:
        audio = asyncio.run(_generate())
        record_tts_audio(span, num_bytes=len(audio), voice=voice)
        return audio

def get_groq_client():
    api_key = os.environ.get("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY", "")
    if not api_key:
        st.error("⚠️ GROQ_API_KEY not found.")
        st.stop()
    return Groq(api_key=api_key)

def transcribe_audio(audio_bytes: bytes) -> str:
    client = get_groq_client()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        with genai_span(
            "transcribe",
            STT_MODEL,
            conversation_id=_session_id(),
            extra_attrs={"audio.input.bytes": len(audio_bytes)},
        ) as span:
            with open(tmp_path, "rb") as f:
                transcription = client.audio.transcriptions.create(
                    file=("audio.wav", f, "audio/wav"),
                    model=STT_MODEL,
                    response_format="text",
                )
            result = transcription.strip()
            if span is not None:
                span.set_attribute("gen_ai.response.text.length", len(result))
            return result
    finally:
        os.unlink(tmp_path)

def _extract_json(text: str) -> dict:
    """Robustly pull a JSON object out of an LLM reply.

    Qwen3.6 is a hybrid reasoning model, so the content can be wrapped in
    ``<think>…</think>`` blocks or code fences. Strip those, then fall back to
    grabbing the first ``{…}`` object if the whole string isn't clean JSON.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _track_tokens(task: str, resp) -> None:
    """Record a call's token usage into session state for the sidebar meter.

    Reads the same ``usage`` block the OpenTelemetry spans use, so the in-app
    counter and the exported metrics stay in sync.
    """
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    inp = getattr(usage, "prompt_tokens", 0) or 0
    out = getattr(usage, "completion_tokens", 0) or 0
    st.session_state.setdefault("token_events", [])
    st.session_state.token_events.insert(0, {
        "time": time.strftime("%H:%M:%S"),
        "task": task,
        "input": inp,
        "output": out,
        "total": inp + out,
    })


def _run_chat(prompt: str, *, task: str, temperature: float, reasoning: bool,
              max_tokens: int, extra_attrs: dict | None = None,
              response_format: dict | None = None):
    """Single entry point for every Groq chat call.

    Caps output length and disables the model's thinking
    (``reasoning_effort="none"``) so Qwen3.6 doesn't burn thinking tokens on
    tasks that don't need it. Falls back gracefully if the model rejects the
    reasoning parameter, and records token usage for the sidebar meter.
    """
    client = get_groq_client()
    attrs = {"spousal.task": task, "spousal.reasoning": reasoning}
    if extra_attrs:
        attrs.update(extra_attrs)

    kwargs = dict(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_completion_tokens=max_tokens,
    )
    if response_format:
        kwargs["response_format"] = response_format
    if not reasoning:
        kwargs["reasoning_effort"] = "none"

    with genai_span(
        "chat", LLM_MODEL, temperature=temperature,
        conversation_id=_session_id(), extra_attrs=attrs,
    ) as span:
        try:
            resp = client.chat.completions.create(**kwargs)
        except BadRequestError:
            # This model/endpoint doesn't accept reasoning_effort — retry without.
            kwargs.pop("reasoning_effort", None)
            resp = client.chat.completions.create(**kwargs)
        record_llm_response(span, resp, model=LLM_MODEL)

    _track_tokens(task, resp)
    return resp


def score_idea(idea: str) -> dict:
    prompt = f"""Evaluate this idea: "{idea}"
Rate 1-10 (1=Harmless, 10=Catastrophic).
Respond ONLY with valid JSON: {{"score": <int>, "reasoning": "<one sentence>"}}"""

    # Scoring just needs a number — thinking is disabled to save tokens. With
    # reasoning off, Groq JSON mode is reliable; _extract_json is a safety net.
    resp = _run_chat(
        prompt, task="score_idea", temperature=0.4, reasoning=False, max_tokens=200,
        response_format={"type": "json_object"},
    )
    return _extract_json(resp.choices[0].message.content or "")

def get_spouse_speech(idea: str, spouse: str, score: int) -> str:
    tone = "calm but firm" if score <= 7 else "absolutely furious and appalled"
    prompt = f"You are a {spouse} reacting to: '{idea}'. Tone: {tone}. Speak directly to your partner. Lmiit to 2 sentences. Be witty and cutting, but avoid profanity."
    # Pure creative output — no thinking needed, so reasoning is disabled to save tokens.
    resp = _run_chat(
        prompt, task="spouse_speech", temperature=0.8, reasoning=False,
        max_tokens=160, extra_attrs={"spousal.score": score},
    )
    return resp.choices[0].message.content.strip()

def get_friend_speech(idea: str, spouse: str) -> str:
    prompt = f"You are a friend calling your buddy to stop them from telling their {spouse} this idea: '{idea}'. Be urgent and funny. Limit to 2 sentences."
    resp = _run_chat(
        prompt, task="friend_speech", temperature=0.8, reasoning=False,
        max_tokens=160,
    )
    return resp.choices[0].message.content.strip()

def autoplay_audio(audio_bytes: bytes):
    """Uses Streamlit's native audio player, which correctly forces a cache refresh."""
    st.audio(audio_bytes, format="audio/mp3", autoplay=True)

# ── Session state init ────────────────────────────────────────────────────────
if "stage" not in st.session_state:
    st.session_state.update({
        "stage": "choose_spouse", "spouse": None, "transcript": "",
        "score": None, "score_reasoning": "", "spouse_speech": "",
        "friend_speech": "", "exile_until": None, "logs": [], "token_events": []
    })

# ── Sidebar: Token Usage meter ───────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 📊 Token Usage")
    st.caption(f"Groq · {LLM_MODEL}")
    token_events = st.session_state.get("token_events", [])
    total_in = sum(e["input"] for e in token_events)
    total_out = sum(e["output"] for e in token_events)
    c1, c2, c3 = st.columns(3)
    c1.metric("Input", f"{total_in:,}")
    c2.metric("Output", f"{total_out:,}")
    c3.metric("Total", f"{total_in + total_out:,}")
    if token_events:
        with st.expander(f"Per-call breakdown ({len(token_events)} calls)", expanded=False):
            for e in token_events:
                st.caption(f"`{e['time']}` · **{e['task']}** · {e['input']} in + {e['output']} out = {e['total']}")
    else:
        st.info("No LLM calls yet.")
    st.divider()

# ── Sidebar Logic Trace ──────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🖥️ The Translation Layer")
    st.caption("System Orchestration Trace (Alexandria/Arlington Node)")
    st.divider()
    if not st.session_state.logs:
        st.info("System idle. Awaiting user input...")
    for log in st.session_state.logs:
        with st.expander(f"**{log['time']}** | {log['agent']}", expanded=True):
            st.markdown(f"**Action:** {log['action']}")
            if log['detail']:
                st.code(log['detail'], language="json")
    if st.button("Clear Logs"):
        st.session_state.logs = []
        st.session_state.token_events = []
        st.rerun()

# ── Masthead ──────────────────────────────────────────────────────────────────
st.markdown('<div class="masthead"><h1>💍 Spousal Approval System</h1><p>Protecting marriages one bad idea at a time</p></div>', unsafe_allow_html=True)

# ── STAGES ──────────────────────────────────────────────────────────────────

if st.session_state.stage == "choose_spouse":
    col1, col2 = st.columns(2)
    with col1:
        if st.button("👰 My Wife", use_container_width=True, type="primary"):
            st.session_state.spouse, st.session_state.stage = "wife", "record_idea"
            add_log("System", "Spouse Selected", "Persona: Wife")
            st.rerun()
    with col2:
        if st.button("🤵 My Husband", use_container_width=True, type="primary"):
            st.session_state.spouse, st.session_state.stage = "husband", "record_idea"
            add_log("System", "Spouse Selected", "Persona: Husband")
            st.rerun()

elif st.session_state.stage == "record_idea":
    audio_file = st.audio_input("Record your idea")
    if audio_file:
        if st.button("🔍 Transcribe", type="primary"):
            add_log("Ear Agent", "Intercepting Audio", "Whisper-large-v3 initialized")
            st.session_state.transcript = transcribe_audio(audio_file.read())
            add_log("Ear Agent", "Transcription Complete", st.session_state.transcript)
            st.session_state.stage = "review_transcript"
            st.rerun()

elif st.session_state.stage == "review_transcript":
    edited = st.text_area("Review your idea", value=st.session_state.transcript)
    if st.button("⚖️ Submit for Evaluation", type="primary"):
        st.session_state.transcript, st.session_state.stage = edited, "evaluating"
        st.rerun()

elif st.session_state.stage == "evaluating":
    with app_span("stage.evaluating", {"gen_ai.conversation.id": _session_id()}) as stage_span:
        add_log("Logic Engine", "Analyzing Marital Risk", f"Querying {LLM_MODEL}")
        result = score_idea(st.session_state.transcript)
        st.session_state.score, st.session_state.score_reasoning = result["score"], result["reasoning"]
        add_log("Logic Engine", "Evaluation Complete", json.dumps(result, indent=2))

        if st.session_state.score <= 2: st.session_state.stage = "safe"
        elif st.session_state.score <= 7: st.session_state.stage = "spouse_warning"
        else: st.session_state.stage = "friend_intervention"

        if stage_span is not None:
            stage_span.set_attribute("spousal.score", st.session_state.score)
            stage_span.set_attribute("spousal.route", st.session_state.stage)
    st.rerun()

elif st.session_state.stage == "safe":
    st.success(f"Score: {st.session_state.score}/10. You are safe.")
    if st.button("Start Over"): st.session_state.clear(); st.rerun()

elif st.session_state.stage == "spouse_warning":
    if not st.session_state.spouse_speech:
        st.session_state.spouse_speech = get_spouse_speech(st.session_state.transcript, st.session_state.spouse, st.session_state.score)
    
    st.markdown(f'<div class="card"><div class="speaker-label">{st.session_state.spouse} says:</div><div class="transcript">{st.session_state.spouse_speech}</div></div>', unsafe_allow_html=True)
    
    with st.spinner("Synthesizing spouse response..."):
        voices = get_voice_mapping(st.session_state.spouse)
        audio = text_to_speech(st.session_state.spouse_speech, voices["spouse"])
        autoplay_audio(audio)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ I'll drop it", use_container_width=True):
            add_log("System", "ABORT TRIGGERED", "User heeded the initial warning.")
            st.session_state.stage = "abort_success"
            st.rerun()
    with col2:
        if st.button("🚀 Doing it anyway", use_container_width=True):
            add_log("System", "ESCALATION", "User is ignoring the initial warning.")
            st.session_state.stage = "spouse_rage"
            st.rerun()

elif st.session_state.stage == "friend_intervention":
    add_log("Intervention Agent", "Critical Risk Detected", "Auto-dialing friend...")
    if not st.session_state.friend_speech:
        st.session_state.friend_speech = get_friend_speech(st.session_state.transcript, st.session_state.spouse)
    st.markdown(f'<div class="card"><div class="speaker-label">Friend says:</div><div class="transcript">{st.session_state.friend_speech}</div></div>', unsafe_allow_html=True)
    
    voices = get_voice_mapping(st.session_state.spouse)
    notify_msg = "This app just sent me a notification that you are about to say something really risky to your spouse."
    audio = text_to_speech(notify_msg + st.session_state.friend_speech, voices["friend"])
    autoplay_audio(audio)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ I'll drop it", use_container_width=True):
            add_log("System", "ABORT TRIGGERED", "User heeded the initial warning.")
            st.session_state.stage = "abort_success"
            st.rerun()
    with col2:
        if st.button("🚀 Doing it anyway", use_container_width=True):
            add_log("System", "ESCALATION", "User is ignoring the initial warning.")
            st.session_state.stage = "spouse_rage"
            st.rerun()

elif st.session_state.stage == "spouse_rage":
    add_log("System", "USER INSUBORDINATION", "Proceeding with high-risk delivery")
    
    if not st.session_state.get("rage_speech"):
        with st.spinner("Bracing for impact..."):
            st.session_state.rage_speech = get_spouse_speech(st.session_state.transcript, st.session_state.spouse, 10)
    
    st.markdown(f'''
        <div class="card" style="border-color:var(--danger)">
            <div class="speaker-label">{st.session_state.spouse} (CRITICAL RAGE):</div>
            <div class="transcript" style="color:var(--danger); font-weight:bold;">
                {st.session_state.rage_speech}
            </div>
        </div>
    ''', unsafe_allow_html=True)
    
    with st.spinner("Generating rage audio..."):
        voices = get_voice_mapping(st.session_state.spouse)
        audio = text_to_speech(st.session_state.rage_speech, voices["spouse"])
        autoplay_audio(audio)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🚨 ABORT MISSION", use_container_width=True):
            add_log("System", "ABORT TRIGGERED", "User regained sanity at the last second.")
            st.session_state.stage = "abort_success"
            st.rerun()
    with col2:
        if st.button("🔥 Double Down", use_container_width=True):
            st.session_state.rage_speech = None 
            st.session_state.stage = "exile"
            st.rerun()

elif st.session_state.stage == "abort_success":
    st.balloons()
    st.markdown(f"""
        <div class="card" style="border-color:var(--safe); text-align:center;">
            <h2 style="color:var(--safe); font-family:'Playfair Display', serif;">Crisis Averted.</h2>
            <p class="transcript">You have made the right choice. Your {st.session_state.spouse} will appreciate your sudden moment of clarity.</p>
        </div>
    """, unsafe_allow_html=True)
    
    # Supportive confirmation audio
    confirm_msg = "That was a close one. Let's just pretend this conversation never happened."
    voices = get_voice_mapping(st.session_state.spouse)
    # Using the 'Friend' voice here provides a "I've got your back" vibe
    autoplay_audio(text_to_speech(confirm_msg, voices["friend"]))
    
    if st.button("Return to Safety", use_container_width=True):
        # Clear specific keys but keep logs if you want the history to persist
        for key in ['stage', 'spouse', 'transcript', 'score', 'score_reasoning', 'spouse_speech', 'friend_speech', 'rage_speech']:
            if key in st.session_state:
                st.session_state[key] = None
        st.session_state.stage = "choose_spouse"
        st.rerun()

elif st.session_state.stage == "exile":
    place = "mother's house" if st.session_state.spouse == "wife" else "the bar"
    add_log("System", f"CRITICAL FAILURE", "Spouse has left for {place}")
        
    msg = f"I am going to {place}. Goodbye."
    voices = get_voice_mapping(st.session_state.spouse)
    autoplay_audio(text_to_speech(msg, voices["spouse"]))
    
    countdown_ph = st.empty()
    for i in range(20, 0, -1):
        countdown_ph.markdown(f'<span class="countdown">{i}</span>', unsafe_allow_html=True)
        time.sleep(1)

    st.session_state.exile_until = None
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()
