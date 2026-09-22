import streamlit as st
import json
import io
import re
import os
import asyncio
from PIL import Image
from security import (
    consume_llm_call,
    end_session,
    max_llm_calls_per_session,
    remaining_llm_calls,
    require_access,
)

# ── DEPENDENCIES ──────────────────────────────────────────────────────────────
# pip install google-genai edge-tts pillow streamlit

try:
    from google import genai
    from google.genai import types
except ImportError:
    st.error("Missing dependency: run `pip install google-genai`")
    st.stop()

try:
    import edge_tts
except ImportError:
    st.error("Missing dependency: run `pip install edge-tts`")
    st.stop()

# ── 1. PAGE CONFIG ────────────────────────────────────────────────────────────
st.set_page_config(page_title="TMNT Agentic Poet", page_icon="🐢", layout="wide")

st.markdown(
    """
    <style>
        .stApp {
            background: radial-gradient(circle at top right, #344d1e 0, #10180c 42%, #090d07 100%);
            color: #fbfdf2;
            font-size: 18px;
        }
        .stApp p,
        .stApp li,
        .stApp label,
        .stApp .stMarkdown,
        .stApp [data-testid="stCaptionContainer"],
        .stApp [data-testid="stWidgetLabel"] p {
            color: #fbfdf2 !important;
            font-size: 1.08rem;
        }
        .stApp [data-testid="stWidgetLabel"] p {
            font-size: 1.15rem;
            font-weight: 650;
        }
        h1, h2, h3 { color: #e7f8a5 !important; }
        h1 { font-size: 3rem !important; }
        h2 { font-size: 2.15rem !important; }
        h3 { font-size: 1.65rem !important; }
        [data-testid="stSidebar"] { background: #18220e; }
        [data-testid="stSidebar"] * {
            color: #fbfdf2;
            font-size: 1.04rem;
        }
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            color: #e7f8a5 !important;
        }
        .stButton > button { font-size: 1.05rem; font-weight: 650; }
        div[data-testid="stStatusWidget"] { border-color: #83ad35; }
        [data-testid="stExpander"] details > summary,
        [data-testid="stExpander"] details > summary *,
        [data-testid="stExpander"] details > summary svg {
            color: #111111 !important;
            -webkit-text-fill-color: #111111 !important;
            fill: #111111 !important;
        }
        [data-testid="stExpander"] details > summary {
            background: #f8f9fc !important;
        }
        .tmnt-roster {
            padding: 0.8rem 1rem;
            border: 1px solid #6f9130;
            border-radius: 0.6rem;
            background: rgba(24, 34, 14, 0.82);
            color: #fbfdf2;
            font-size: 1.12rem;
            margin-bottom: 1rem;
        }
        .block-container {
            max-width: 1180px;
            padding-top: 2.25rem;
            padding-bottom: 3rem;
        }
        [data-testid="stFileUploader"] {
            background: rgba(24, 34, 14, 0.72);
            border-radius: 0.6rem;
            padding: 0.35rem;
        }
        .stApp button[kind="secondary"],
        .stApp button[kind="secondary"] *,
        .stApp [data-testid="stFormSubmitButton"] button,
        .stApp [data-testid="stFormSubmitButton"] button *,
        .stApp [data-baseweb="select"],
        .stApp [data-baseweb="select"] *,
        .stApp input,
        .stApp textarea,
        [data-testid="stFileUploader"] button,
        [data-testid="stFileUploader"] button * {
            color: #000000 !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── 2. PRIVATE DEMO ACCESS ────────────────────────────────────────────────────
# This gate runs before model clients are initialized or any remote call is possible.
access_identity = require_access()

# ── 3. API KEY SETUP ──────────────────────────────────────────────────────────
# Gemini powers both the vision and text agents.
gemini_key = (
    st.secrets.get("GENAI_API_KEY")
    or st.secrets.get("GEMINI_API_KEY")
)
if not gemini_key:
    st.error("Missing Gemini API key in Streamlit Secrets (GENAI_API_KEY or GEMINI_API_KEY).")
    st.stop()

gemini_client = genai.Client(api_key=gemini_key)

GEMINI_MODEL = "gemini-3.1-flash-lite"

# ── 3. SESSION STATE ──────────────────────────────────────────────────────────
for key, default in [("photo_key", 0), ("final_output", None)]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── 4. UTILITIES ──────────────────────────────────────────────────────────────

def clean_json(text: str) -> str:
    """Extract the first {...} block from a string (strips markdown fences / reasoning)."""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return match.group(0) if match else text.strip()


def call_gemini(prompt: str, max_tokens: int = 1024) -> dict:
    """
    Single Gemini Flash Lite call for text agents, returning a parsed JSON dict.
    """
    consume_llm_call(access_identity)
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=max_tokens,
            temperature=0.6,
        ),
    )
    content = response.text or ""
    candidate = clean_json(content)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Gemini did not return valid JSON ({e}). Raw output: {content[:300]!r}"
        ) from e


def keyword_precheck(entities: list, poem: str) -> bool:
    """
    Fast Python check BEFORE burning a Gemini call on moderation.
    Returns True if at least one entity keyword appears in the poem.
    This handles the obvious pass case cheaply.
    """
    poem_lower = poem.lower()
    return any(str(e).lower() in poem_lower for e in entities)


def get_mood_music(mood: str):
    """Master Splinter as Maestro: load MP3 from audio_library/ by mood name."""
    path = f"audio_library/{mood.lower()}.mp3"
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def format_poem_for_narration(poem: str) -> str:
    """Normalize poem lines and add spoken pauses for a measured recitation."""
    lines = [line.strip() for line in re.split(r"\s*/\s*|\n+", poem) if line.strip()]
    paced_lines = []
    for line in lines:
        if line[-1] not in ".!?…":
            line += "."
        paced_lines.append(line)
    return "\n\n".join(paced_lines)


async def _stream_splinter_voice(text: str) -> bytes:
    """Generate Master Splinter's narration with a deeper, measured male voice."""
    voice_io = io.BytesIO()
    narrator = edge_tts.Communicate(
        format_poem_for_narration(text),
        voice="en-US-GuyNeural",
        rate="-24%",
        pitch="-8Hz",
    )
    async for chunk in narrator.stream():
        if chunk["type"] == "audio":
            voice_io.write(chunk["data"])
    return voice_io.getvalue()


def narrate_as_master_splinter(text: str) -> bytes:
    """Run the async Edge TTS client from Streamlit's synchronous pipeline."""
    return asyncio.run(_stream_splinter_voice(text))


# ── 5. THE AGENTS ─────────────────────────────────────────────────────────────

def agent_visionary(image_file) -> dict:
    """
    AGENT 1 — DONATELLO, THE VISIONARY (Gemini, vision required)
    Analyzes the image and returns structured scene data.
    This is the image-understanding call in the pipeline.
    """
    raw_img = Image.open(image_file)
    raw_img.thumbnail((1024, 1024))  # resize before sending — saves tokens

    prompt = """
    You are Donatello, the Visionary agent. Analyze this image carefully with
    Donatello's precise, observant mindset. Keep the result factual; do not
    invent TMNT characters or details that are not visible in the image.
    Return ONLY a raw JSON object with these exact keys:
    {
      "description": "A 2-sentence factual summary of the scene.",
      "entities": ["list", "of", "key", "objects", "or", "subjects"],
      "setting": "brief scene setting (e.g. urban street at dusk)"
    }
    """
    consume_llm_call(access_identity)
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[prompt, raw_img]
    )
    return json.loads(clean_json(response.text))


def agent_bard(description: str, setting: str, entities: list) -> str:
    """
    AGENT 2 — MICHELANGELO, THE BARD (Gemini Flash Lite, text only)
    Receives the Visionary's structured output and composes a poem.
    Separated from the Visionary so the handoff is explicit and visible.
    """
    prompt = f"""
    You are Michelangelo, the Bard agent. Donatello has analyzed an image and
    handed you this data. Bring Michelangelo's playful, energetic rhythm to the
    poem, but do not add TMNT references unless they are present in the scene:

    Scene Description: {description}
    Setting: {setting}
    Key Entities: {entities}

    Your task: Write a 4-line rhythmic poem that is clearly inspired by
    the scene and references at least one of the key entities. Give each line
    a natural spoken cadence, with roughly similar line lengths and deliberate
    punctuation that creates a pause at the end of every line.

    Return ONLY a JSON object:
    {{"poem": "line one\\nline two\\nline three\\nline four"}}
    """
    data = call_gemini(prompt, max_tokens=1024)
    return data["poem"]


def agent_moderator(entities: list, poem: str, description: str) -> dict:
    """
    AGENT 3 — LEONARDO, THE MODERATOR (Gemini Flash Lite, text only)
    Two-stage verification:
      Stage A: Fast Python keyword check (free).
      Stage B: LLM semantic check only if Stage A fails.
    Returns {verified: bool, reason: str}
    """
    # Stage A: cheap keyword pre-check
    if keyword_precheck(entities, poem):
        return {
            "verified": True,
            "reason": "Python keyword check passed — entity found in poem."
        }

    # Stage B: semantic LLM check (only runs if Stage A failed)
    prompt = f"""
    You are Leonardo, the Moderator agent. Your job is disciplined, strict
    quality control.

    Scene entities: {entities}
    Scene description: {description}
    Poem to review: "{poem}"

    Does this poem thematically relate to the scene entities and description?
    Be strict. If the poem could describe ANY scene, it fails.

    Return ONLY a JSON object:
    {{"verified": true_or_false, "reason": "one sentence explanation"}}
    """
    return call_gemini(prompt, max_tokens=1024)


def agent_sentiment(poem: str, description: str) -> tuple:
    """
    AGENT 4 — RAPHAEL, THE SENTIMENT AGENT (Gemini Flash Lite, text only)
    Reads the approved poem and picks the best mood for music selection.
    Separated from the Bard so mood analysis is its own visible step.
    """
    prompt = f"""
    You are Raphael, the Sentiment agent. Analyze the emotional tone of this poem
    and the scene it describes, then select the single best mood category.

    Poem: "{poem}"
    Scene: "{description}"

    Choose EXACTLY one mood from: MELANCHOLY, WHIMSICAL, EPIC, EERIE

    Return ONLY a JSON object:
    {{"mood": "ONE_MOOD", "reason": "one sentence justification"}}
    """
    data = call_gemini(prompt, max_tokens=512)
    return data["mood"].upper(), data.get("reason", "")


# ── 6. ORCHESTRATOR ───────────────────────────────────────────────────────────

MAX_BARD_RETRIES = 2  # how many times to ask Bard to rewrite before giving up

def run_pipeline(image_file):
    """
    Full multi-agent pipeline with real closed-loop retry on Moderator rejection.

    Flow:
      Donatello / Visionary (Gemini) -> Michelangelo / Bard (Gemini Flash Lite) -> Leonardo / Moderator (Gemini Flash Lite)
           ^___________________________|  (retry up to MAX_BARD_RETRIES times)
      -> Raphael / Sentiment (Gemini Flash Lite) -> Master Splinter / Narrator + Maestro
    """
    with st.status("Orchestrating Multi-Agent Workflow...", expanded=True) as status:

        # ── AGENT 1: VISIONARY ─────────────────────────────────────────────
        st.write("🟣 **Donatello · Visionary Agent**: Analyzing image...")
        try:
            scene = agent_visionary(image_file)
        except Exception as e:
            st.error(f"Donatello's vision analysis failed: {e}")
            return None

        st.write(f"   → Scene understood. Entities: `{', '.join(scene['entities'])}`")

        # ── AGENT 2 + 3: BARD → MODERATOR (closed-loop retry) ─────────────
        poem       = None
        mod_result = None
        attempt    = 0

        while attempt <= MAX_BARD_RETRIES:
            attempt += 1
            label = f"(Attempt {attempt})" if attempt > 1 else ""

            st.write(f"🟠 **Michelangelo · Bard Agent**: Composing poem... {label}")
            try:
                poem = agent_bard(scene["description"], scene["setting"], scene["entities"])
            except Exception as e:
                st.error(f"Michelangelo's poem failed: {e}")
                return None

            st.write("   → Poem drafted. Sending it to Leonardo...")
            st.write("🔵 **Leonardo · Moderator Agent**: Verifying poem relevance...")

            try:
                mod_result = agent_moderator(scene["entities"], poem, scene["description"])
            except Exception as e:
                st.warning(f"Leonardo hit an error on attempt {attempt}: {e}. Retrying Michelangelo...")
                mod_result = {"verified": False, "reason": f"Leonardo moderation error: {e}"}

            if mod_result["verified"]:
                st.write(f"   ✅ Approved: {mod_result['reason']}")
                break
            else:
                if attempt <= MAX_BARD_RETRIES:
                    st.write(f"   ⚠️ Rejected: {mod_result['reason']} — asking Michelangelo to rewrite...")
                else:
                    st.write(f"   ⚠️ Rejected after {MAX_BARD_RETRIES + 1} attempts — proceeding with best effort.")

        # ── AGENT 4: RAPHAEL / SENTIMENT ───────────────────────────────────
        st.write("🔴 **Raphael · Sentiment Agent**: Determining mood for music selection...")
        try:
            mood, mood_reason = agent_sentiment(poem, scene["description"])
        except Exception as e:
            st.warning(f"Raphael's sentiment analysis failed ({e}), defaulting to WHIMSICAL.")
            mood, mood_reason = "WHIMSICAL", "Default fallback."

        st.write(f"   → Mood detected: **{mood}** — {mood_reason}")

        # ── MASTER SPLINTER: male neural narration ─────────────────────────
        st.write("🐀 **Master Splinter · Narrator**: Recording poem recitation...")
        try:
            voice_bytes = narrate_as_master_splinter(poem)
        except Exception as e:
            st.error(f"Master Splinter's narration failed: {e}")
            return None

        # ── MASTER SPLINTER: MAESTRO / local music lookup ──────────────────
        music_bytes = get_mood_music(mood)

        status.update(label="✅ All Agents Complete!", state="complete", expanded=False)

        return {
            "scene":       scene,
            "poem":        poem,
            "moderator":   mod_result,
            "mood":        mood,
            "mood_reason": mood_reason,
            "voice":       voice_bytes,
            "music":       music_bytes,
        }


# ── 7. UI ─────────────────────────────────────────────────────────────────────

st.title("🐢 TMNT Agentic Poet")
st.caption("A sewer-studio AI performance: Vision → Verse → Discipline → Sound")
st.markdown(
    """
    <div class="tmnt-roster">
        <strong>The team:</strong> Donatello sees · Michelangelo rhymes · Leonardo verifies ·
        Raphael reads the mood · Master Splinter narrates and conducts.
    </div>
    """,
    unsafe_allow_html=True,
)
st.markdown("---")

# Sidebar: architecture explainer (great for portfolio demos)
with st.sidebar:
    st.header("🐢 Turtle Team")
    st.markdown("""
    | Agent | Model | Role |
    |---|---|---|
    | 🟣 Donatello · Visionary | Gemini 3.1 Flash Lite | Image → Scene data |
    | 🟠 Michelangelo · Bard | Gemini 3.1 Flash Lite | Scene → Poem |
    | 🔵 Leonardo · Moderator | Gemini 3.1 Flash Lite | Verify poem relevance |
    | 🔴 Raphael · Sentiment | Gemini 3.1 Flash Lite | Poem → Mood |
    | 🐀 Master Splinter · Narrator | Edge TTS · Guy | Poem → Male voice |
    | 🐀 Master Splinter · Maestro | Local files | Mood → Music |
    """)
    st.divider()
    st.caption("Gemini 3.1 Flash Lite powers the full turtle team")
    st.caption("Leonardo sends Michelangelo back for up to 2 rewrites")
    if access_identity == "owner":
        st.caption("LLM access: Unlimited")
    else:
        st.caption(
            f"LLM calls remaining: {remaining_llm_calls(access_identity)} "
            f"/ {max_llm_calls_per_session()}"
        )

    if st.button("🔒 End session", use_container_width=True):
        previous_photo_key = st.session_state["photo_key"]
        st.session_state["final_output"] = None
        for widget_key in (f"upload_{previous_photo_key}", f"camera_{previous_photo_key}"):
            st.session_state.pop(widget_key, None)
        st.session_state["photo_key"] = previous_photo_key + 1
        end_session()
        st.rerun()

photo_col, guide_col = st.columns([3, 2])

with photo_col:
    source = st.radio(
        "Bring a scene to the lair",
        ["Upload or drag & drop", "Use phone or webcam"],
        horizontal=True,
        key="photo_source",
    )

    if source == "Upload or drag & drop":
        photo_input = st.file_uploader(
            "Drop a photo here or browse your computer",
            type=["jpg", "jpeg", "png", "webp"],
            key=f"upload_{st.session_state.photo_key}",
            help="Drag a photo from your desktop onto this area, or choose a file.",
        )
    else:
        photo_input = st.camera_input(
            "Take a photo with your phone or webcam",
            key=f"camera_{st.session_state.photo_key}",
        )

with guide_col:
    st.info(
        "**Desktop tip:** drag an image directly from File Explorer into the upload area. "
        "Choose the camera option when you want a fresh shot from your phone or webcam."
    )

# Process a selected image only after an explicit action, never on a normal rerun.
run_requested = st.button(
    "🐢 CREATE LAIR PERFORMANCE",
    type="primary",
    use_container_width=True,
    disabled=not photo_input or st.session_state.final_output is not None,
)
if run_requested:
    if st.session_state.get("llm_call_in_flight"):
        st.warning("A turtle-team request is already running.")
    else:
        st.session_state["llm_call_in_flight"] = True
        try:
            results = run_pipeline(photo_input)
            if results:
                st.session_state.final_output = results
        finally:
            st.session_state["llm_call_in_flight"] = False

        if st.session_state.final_output:
            st.rerun()

# ── RESULTS DISPLAY ───────────────────────────────────────────────────────────
if st.session_state.final_output:
    out = st.session_state.final_output

    col1, col2 = st.columns(2)

    with col1:
        with st.expander("🟣 Donatello's Visionary Report", expanded=True):
            st.write(out["scene"]["description"])
            st.caption(f"Setting: {out['scene']['setting']}")
            st.caption(f"Entities: {', '.join(out['scene']['entities'])}")

    with col2:
        with st.expander("🟠 Michelangelo's Poem", expanded=True):
            st.info(out["poem"])
            verified = out["moderator"]["verified"]
            icon     = "✅" if verified else "⚠️"
            st.caption(f"{icon} Leonardo · Moderator: {out['moderator']['reason']}")

    st.divider()
    st.subheader(f"🐢 The Lair Performance  ·  Raphael's Mood: **{out['mood']}**")
    st.caption(f"_{out['mood_reason']}_")

    if out["music"]:
        c1, c2 = st.columns(2)
        with c1:
            st.write("**🐀 Master Splinter · Narrator (male voice)**")
            st.audio(out["voice"], format="audio/mp3")
        with c2:
            st.write("**🐀 Master Splinter · Maestro**")
            st.audio(out["music"], format="audio/mp3")

        if st.button("▶️ PLAY COMBINED PERFORMANCE", use_container_width=True):
            st.components.v1.html(
                """
                <script>
                    var audios = window.parent.document.querySelectorAll('audio');
                    audios.forEach(a => { a.currentTime = 0; a.play(); });
                </script>
                """,
                height=0,
            )
            st.balloons()
    else:
        st.warning("⚠️ No music file found in /audio_library/ for this mood. Playing voice only.")
        st.audio(out["voice"], format="audio/mp3")

    st.markdown("<br>", unsafe_allow_html=True)

    if st.button("🔄 START OVER", type="primary", use_container_width=True):
        st.session_state.final_output = None
        st.session_state.photo_key += 1
        st.rerun()
