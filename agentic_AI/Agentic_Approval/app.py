import streamlit as st
import os
import re
import time
import json
import asyncio
import uuid
import hashlib
import edge_tts
import io
from google import genai
from google.genai import types

from telemetry import (
    init_telemetry,
    genai_span,
    record_llm_response,
    record_tts_audio,
    app_span,
)
from security import consume_llm_call, render_access_status, require_access

GEMINI_MODEL = "gemini-3.1-flash-lite"

PERSONAS = {
    "wife": {
        "name": "Peg Bundy",
        "intro": "Peg is on the couch and ready to hear your idea.",
    },
    "husband": {
        "name": "Al Bundy",
        "intro": "Al is at the shoe store and ready to hear your idea.",
    },
}


def persona_name(spouse: str | None) -> str:
    """Return the on-screen Bundy character name for the chosen role."""
    return PERSONAS.get(spouse or "", {}).get("name", "Bundy")


def _session_id() -> str:
    """Stable per-session id used to correlate all spans of one user's run."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid.uuid4().hex
    return st.session_state.session_id


# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="The Bundy Approval Desk",
    page_icon="📺",
    layout="centered",
    initial_sidebar_state="expanded",
)

# The gate runs before any Gemini or edge-TTS request can be reached.
require_access()

# Idempotent: safe to call on every authorized Streamlit rerun. Exports to the
# console by default, or to any OTLP backend when OTEL_EXPORTER_OTLP_ENDPOINT is set.
init_telemetry()

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
  font-family: Georgia, serif;
  color: var(--cream);
}}

/* Bundy household: loud, warm, late-1980s sitcom set dressing. */
[data-testid="stAppViewContainer"] {{
  background-image: radial-gradient(rgba(255, 208, 80, .14) 1px, transparent 1px);
  background-size: 12px 12px;
}}
div.stButton > button {{
  border: 2px solid #f4b000 !important;
  border-radius: 2px !important;
  font-family: Impact, Haettenschweiler, 'Arial Narrow Bold', sans-serif !important;
  letter-spacing: .04em;
  text-transform: uppercase;
}}
.masthead, .card {{
  background: #fff4d8;
  color: #35160b;
  border: 3px solid #f4b000;
  box-shadow: 6px 6px 0 rgba(0, 0, 0, .3);
}}
.masthead {{
  text-align: center;
  padding: 1.35rem 1rem 1.1rem;
  margin: .25rem 0 1.6rem;
  background-image: linear-gradient(135deg, rgba(210, 35, 30, .12) 25%, transparent 25%, transparent 50%, rgba(210, 35, 30, .12) 50%, rgba(210, 35, 30, .12) 75%, transparent 75%);
  background-size: 18px 18px;
}}
.masthead h1 {{
  margin: 0;
  font-family: Impact, Haettenschweiler, 'Arial Narrow Bold', sans-serif;
  font-size: clamp(2rem, 7vw, 3.8rem);
  letter-spacing: .055em;
  line-height: 1;
  color: #b51512;
  text-shadow: 2px 2px 0 #f4b000;
}}
.masthead p {{
  margin: .7rem 0 0;
  font-size: 1rem;
  color: #35160b;
  font-weight: bold;
}}
.card {{
  padding: 1.2rem;
  margin: .5rem 0 1.25rem;
}}
.speaker-label {{
  color: #b51512;
  font-family: Impact, Haettenschweiler, 'Arial Narrow Bold', sans-serif;
  font-size: 1.15rem;
  letter-spacing: .045em;
  text-transform: uppercase;
}}
.transcript {{
  color: #35160b;
  font-size: 1.15rem;
  line-height: 1.5;
  margin-top: .5rem;
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


def transition_stage(stage: str, action: str, detail: str, *, clear_rage: bool = False) -> None:
    """Advance before Streamlit renders the current stage's audio again.

    Streamlit button callbacks run before the script rerun. Updating the stage
    here prevents a clicked escalation button from queueing its old response
    audio immediately before the next stage's audio.
    """
    add_log("System", action, detail)
    if clear_rage:
        st.session_state.rage_speech = None
    st.session_state.stage = stage


def escalate_from_friend() -> None:
    """Send every risky idea through the friend before the spouse responds."""
    if (st.session_state.score or 0) <= 7:
        transition_stage(
            "spouse_warning",
            "ESCALATION",
            "User ignored the neighborhood hotline; routing to the spouse warning.",
        )
    else:
        transition_stage(
            "spouse_rage",
            "ESCALATION",
            "User ignored the neighborhood hotline at critical risk.",
        )

def get_voice_mapping(spouse: str) -> dict:
    """Maps roles to specific Microsoft Neural voices."""
    if spouse == "wife":
        return {"spouse": "en-US-JennyNeural", "friend": "en-US-ChristopherNeural"}
    return {"spouse": "en-US-GuyNeural", "friend": "en-US-EmmaNeural"}

def text_to_speech(text: str, voice: str) -> bytes:
    """Generate session-local TTS audio once, then reuse it on reruns."""
    cache_key = hashlib.sha256(f"{voice}\0{text}".encode("utf-8")).hexdigest()
    audio_cache = st.session_state.setdefault("tts_audio_cache", {})
    if cache_key in audio_cache:
        return audio_cache[cache_key]

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
    audio_cache[cache_key] = audio
    return audio

def get_gemini_client():
    """Create a Gemini client from an environment variable or Streamlit secret."""
    api_key = os.environ.get("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")
    if not api_key:
        st.error("⚠️ GEMINI_API_KEY not found.")
        st.stop()
    return genai.Client(api_key=api_key)

def transcribe_audio(audio_bytes: bytes) -> str:
    """Transcribe Streamlit's WAV bytes directly with Gemini Flash-Lite."""
    client = get_gemini_client()
    with genai_span(
        "transcribe",
        GEMINI_MODEL,
        system="gemini",
        conversation_id=_session_id(),
        extra_attrs={"audio.input.bytes": len(audio_bytes)},
    ) as span:
        with consume_llm_call():
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    "Transcribe this audio faithfully. Output only the spoken words, with no commentary.",
                    types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                ],
                config={"temperature": 0, "max_output_tokens": 1_024},
            )
        record_llm_response(span, response, model=GEMINI_MODEL, system="gemini")
        result = (response.text or "").strip()
        if span is not None:
            span.set_attribute("gen_ai.response.text.length", len(result))
    _track_tokens("transcribe", response)
    return result

def _extract_json(text: str) -> dict:
    """Robustly pull a JSON object out of an LLM reply.

    Gemini is asked for JSON output, but this remains defensive about code
    fences and surrounding text before falling back to the first ``{…}``
    object.
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

    Reads Gemini's ``usage_metadata`` block, matching the values exported by
    the OpenTelemetry instrumentation.
    """
    usage = getattr(resp, "usage_metadata", None)
    if usage is None:
        return
    inp = getattr(usage, "prompt_token_count", 0) or 0
    out = getattr(usage, "candidates_token_count", 0) or 0
    st.session_state.setdefault("token_events", [])
    st.session_state.token_events.insert(0, {
        "time": time.strftime("%H:%M:%S"),
        "task": task,
        "input": inp,
        "output": out,
        "total": inp + out,
    })


def _run_chat(prompt: str, *, task: str, temperature: float,
              max_tokens: int, extra_attrs: dict | None = None,
              json_output: bool = False):
    """Call Gemini Flash-Lite and record its response and token usage."""
    client = get_gemini_client()
    attrs = {"spousal.task": task}
    if extra_attrs:
        attrs.update(extra_attrs)

    config = {"temperature": temperature, "max_output_tokens": max_tokens}
    if json_output:
        config["response_mime_type"] = "application/json"

    with genai_span(
        "chat", GEMINI_MODEL, system="gemini", temperature=temperature,
        conversation_id=_session_id(), extra_attrs=attrs,
    ) as span:
        with consume_llm_call():
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
        record_llm_response(span, resp, model=GEMINI_MODEL, system="gemini")

    _track_tokens(task, resp)
    return resp


def score_idea(idea: str, spouse: str) -> dict:
    """Score an idea against the selected Bundy household rubric."""
    if spouse == "husband":
        prompt = f"""You are evaluating an idea for Al Bundy: "{idea}"

Rate it 1-10 by whether it is a home aesthetic or lifestyle choice Al, or a
typical man in this fictional sitcom setting, could reasonably live with. Do
not score physical danger alone: a proposal can be safe but still be a poor
fit for Al's comfort, pride, or idea of a livable home.

Rubric: 1-2 = Al can comfortably live with it; 3-4 = safe but aesthetically
questionable; 5-7 = most men would strongly object; 8-10 = an unlivable
household or relationship dealbreaker. For calibration, painting the house
pink is physically safe but should be a 4; having male strippers in the house
is a 9. Respond ONLY with valid JSON:
{{"score": <int>, "reasoning": "<one sentence>"}}"""
    else:
        prompt = f"""Evaluate this idea: "{idea}"
Rate 1-10 (1=Harmless, 10=Catastrophic).
Respond ONLY with valid JSON: {{"score": <int>, "reasoning": "<one sentence>"}}"""

    resp = _run_chat(
        prompt, task="score_idea", temperature=0.4, max_tokens=200,
        json_output=True,
    )
    return _extract_json(resp.text or "")

def get_spouse_speech(idea: str, spouse: str, score: int) -> str:
    tone = "calm but firm" if score <= 7 else "absolutely furious and appalled"
    character = persona_name(spouse)
    prompt = f"""You are {character} from the Bundy household, reacting to: '{idea}'.
Tone: {tone}. Speak directly to your partner in a sharp, sitcom-style voice.
Limit to 2 sentences. Be witty and cutting but avoid profanity and do not quote or closely imitate dialogue from any TV show."""
    resp = _run_chat(
        prompt, task="spouse_speech", temperature=0.8,
        max_tokens=160, extra_attrs={"spousal.score": score},
    )
    return (resp.text or "").strip()

def get_friend_speech(idea: str, spouse: str) -> str:
    character = persona_name(spouse)
    prompt = f"""You are a worried friend calling to stop your buddy from telling {character} this idea: '{idea}'.
Be urgent and funny, with a working-class 1990s sitcom energy. Limit to 2 sentences; avoid profanity and TV-show quotes."""
    resp = _run_chat(
        prompt, task="friend_speech", temperature=0.8,
        max_tokens=160,
    )
    return (resp.text or "").strip()

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

# ── Sidebar: Access status ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔒 Private Demo")
    render_access_status()
    st.divider()

# ── Sidebar: Token Usage meter ───────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 📊 Token Usage")
    st.caption(f"Gemini · {GEMINI_MODEL}")
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
st.markdown('<div class="masthead"><h1>📺 The Bundy Approval Desk</h1><p>Keeping disastrous ideas out of the Bundy living room.</p></div>', unsafe_allow_html=True)

# ── STAGES ──────────────────────────────────────────────────────────────────

if st.session_state.stage == "choose_spouse":
    col1, col2 = st.columns(2)
    with col1:
        if st.button("💋 Talk to Peg", use_container_width=True, type="primary"):
            st.session_state.spouse, st.session_state.stage = "wife", "record_idea"
            add_log("System", "Bundy Selected", "Persona: Peg Bundy")
            st.rerun()
    with col2:
        if st.button("👞 Talk to Al", use_container_width=True, type="primary"):
            st.session_state.spouse, st.session_state.stage = "husband", "record_idea"
            add_log("System", "Bundy Selected", "Persona: Al Bundy")
            st.rerun()

elif st.session_state.stage == "record_idea":
    st.info(PERSONAS[st.session_state.spouse]["intro"])
    audio_file = st.audio_input("Record your idea")
    if audio_file:
        if st.button("🔍 Transcribe", type="primary"):
            add_log("Ear Agent", "Intercepting Audio", f"{GEMINI_MODEL} audio transcription initialized")
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
        metric = "Al's household compatibility" if st.session_state.spouse == "husband" else "Marital risk"
        add_log("Logic Engine", f"Analyzing {metric}", f"Querying {GEMINI_MODEL}")
        result = score_idea(st.session_state.transcript, st.session_state.spouse)
        st.session_state.score, st.session_state.score_reasoning = result["score"], result["reasoning"]
        add_log("Logic Engine", "Evaluation Complete", json.dumps(result, indent=2))

        if st.session_state.score <= 2:
            st.session_state.stage = "safe"
        else:
            st.session_state.stage = "friend_intervention"

        if stage_span is not None:
            stage_span.set_attribute("spousal.score", st.session_state.score)
            stage_span.set_attribute("spousal.route", st.session_state.stage)
    st.rerun()

elif st.session_state.stage == "safe":
    if st.session_state.spouse == "husband":
        st.success(f"Al Compatibility: {st.session_state.score}/10. Al can live with it.")
    else:
        st.success(f"Score: {st.session_state.score}/10. You are safe.")
    if st.button("Start Over"): st.session_state.clear(); st.rerun()

elif st.session_state.stage == "spouse_warning":
    if not st.session_state.spouse_speech:
        st.session_state.spouse_speech = get_spouse_speech(st.session_state.transcript, st.session_state.spouse, st.session_state.score)
    
    st.markdown(f'<div class="card"><div class="speaker-label">{persona_name(st.session_state.spouse)} says:</div><div class="transcript">{st.session_state.spouse_speech}</div></div>', unsafe_allow_html=True)
    
    with st.spinner("Synthesizing spouse response..."):
        voices = get_voice_mapping(st.session_state.spouse)
        audio = text_to_speech(st.session_state.spouse_speech, voices["spouse"])
        autoplay_audio(audio)
    
    col1, col2 = st.columns(2)
    with col1:
        st.button(
            "✅ I'll drop it", use_container_width=True,
            on_click=transition_stage,
            args=("abort_success", "ABORT TRIGGERED", "User heeded the initial warning."),
        )
    with col2:
        st.button(
            "🚀 Doing it anyway", use_container_width=True,
            on_click=transition_stage,
            args=("spouse_rage", "ESCALATION", "User is ignoring the spouse warning."),
        )

elif st.session_state.stage == "friend_intervention":
    add_log("Intervention Agent", "Critical Risk Detected", "Auto-dialing friend...")
    if not st.session_state.friend_speech:
        st.session_state.friend_speech = get_friend_speech(st.session_state.transcript, st.session_state.spouse)
    st.markdown(f'<div class="card"><div class="speaker-label">The neighborhood hotline says:</div><div class="transcript">{st.session_state.friend_speech}</div></div>', unsafe_allow_html=True)
    
    voices = get_voice_mapping(st.session_state.spouse)
    notify_msg = "This app just sent me a notification that you are about to say something really risky to your spouse."
    audio = text_to_speech(notify_msg + st.session_state.friend_speech, voices["friend"])
    autoplay_audio(audio)
    
    col1, col2 = st.columns(2)
    with col1:
        st.button(
            "✅ I'll drop it", use_container_width=True,
            on_click=transition_stage,
            args=("abort_success", "ABORT TRIGGERED", "User heeded the initial warning."),
        )
    with col2:
        st.button(
            "🚀 Doing it anyway", use_container_width=True,
            on_click=escalate_from_friend,
        )

elif st.session_state.stage == "spouse_rage":
    add_log("System", "USER INSUBORDINATION", "Proceeding with high-risk delivery")
    
    if not st.session_state.get("rage_speech"):
        with st.spinner("Bracing for impact..."):
            st.session_state.rage_speech = get_spouse_speech(st.session_state.transcript, st.session_state.spouse, 10)
    
    st.markdown(f'''
        <div class="card" style="border-color:var(--danger)">
            <div class="speaker-label">{persona_name(st.session_state.spouse)} (CRITICAL RAGE):</div>
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
        st.button(
            "🚨 ABORT MISSION", use_container_width=True,
            on_click=transition_stage,
            args=("abort_success", "ABORT TRIGGERED", "User regained sanity at the last second."),
        )
    with col2:
        st.button(
            "🔥 Double Down", use_container_width=True,
            on_click=transition_stage,
            args=("exile", "DOUBLE DOWN", "User ignored the final warning."),
            kwargs={"clear_rage": True},
        )

elif st.session_state.stage == "abort_success":
    st.balloons()
    st.markdown(f"""
        <div class="card" style="border-color:var(--safe); text-align:center;">
            <h2 style="color:var(--safe); font-family:'Playfair Display', serif;">Crisis Averted.</h2>
            <p class="transcript">You have made the right choice. {persona_name(st.session_state.spouse)} may not throw you out of the house after all.</p>
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
    place = "the Bundy couch" if st.session_state.spouse == "wife" else "the bar"
    add_log("System", "CRITICAL FAILURE", f"{persona_name(st.session_state.spouse)} has left for {place}")
        
    msg = f"I am going to {place}. Goodbye."
    voices = get_voice_mapping(st.session_state.spouse)
    autoplay_audio(text_to_speech(msg, voices["spouse"]))
    
    countdown_ph = st.empty()
    for i in range(10, 0, -1):
        countdown_ph.markdown(f'<span class="countdown">{i}</span>', unsafe_allow_html=True)
        time.sleep(1)

    st.session_state.exile_until = None
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()
