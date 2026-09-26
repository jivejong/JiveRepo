import streamlit as st
import pandas as pd
import random
import json
import re
from pathlib import Path
from security import (
    UsageLimitReached,
    consume_llm_call,
    require_access,
    render_session_controls,
)

APP_DIR = Path(__file__).resolve().parent
BART_AVATAR = str(APP_DIR / "images" / "Bart.png")
HOMER_AVATAR = str(APP_DIR / "images" / "Homer.png")
MARGE_AVATAR = str(APP_DIR / "images" / "Marge.png")

# ── DEPENDENCIES: pip install google-genai pandas streamlit chromadb ──────────

# ── 1. PAGE CONFIG ────────────────────────────────────────────────────────────
st.set_page_config(page_title="Simpson Snack Negotiation", page_icon="🍩", layout="wide")
access_identity = require_access()

st.markdown("""
<style>
    .stApp { background: #fff8dc; }
    [data-testid="stSidebar"] { background: #71c5e8; }
    [data-testid="stSidebar"] * { color: #1f2a44; }
    h1 { color: #d32f2f; }
    [data-testid="stChatMessage"] {
        border: 2px solid #f2c230;
        border-radius: 14px;
        background: #fffdf4;
    }
    .stButton > button[kind="primary"] {
        background: #d32f2f;
        border-color: #d32f2f;
    }
</style>
""", unsafe_allow_html=True)

st.title("🍩 Simpson Snack Negotiation")
st.caption("A multi-agent RAG demo: Bart vs Marge vs rogue Homer")

# ── 2. API + CLIENT SETUP ─────────────────────────────────────────────────────
if "GEMINI_API_KEY" not in st.secrets:
    st.error("Missing 'GEMINI_API_KEY' in .streamlit/secrets.toml")
    st.stop()

try:
    from google import genai
    from google.genai import types
    gemini_client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"].strip())
    # Model id is overridable via secrets so it can be swapped without a code change.
    GEMINI_MODEL = st.secrets.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
except ImportError:
    st.error("Missing dependency: run `pip install google-genai`")
    st.stop()

# ── 3. DATA LOADING ───────────────────────────────────────────────────────────
@st.cache_data
def load_nutrition_data():
    try:
        df = pd.read_csv("nutritional_data.csv")
        df.columns = df.columns.str.strip()
        # The CSV ships Sugar + Cholesterol; Cholesterol doubles as our fat proxy.
        for col in ['Sugar', 'Cholesterol']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        return df
    except Exception as e:
        st.error(f"Error loading CSV: {e}")
        return None

df_nutrition = load_nutrition_data()

# ── 4. GEMINI HELPERS ─────────────────────────────────────────────────────────
def _loads_json(content: str) -> dict:
    """Parse a model response into a dict, tolerating stray reasoning wrappers."""
    content = (content or "").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Belt-and-suspenders: drop any <think>…</think> and grab the outer object.
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            return json.loads(content[start:end + 1])
        raise


def gemini_json(messages: list, max_tokens: int = 1024) -> dict:
    """Call Gemini with JSON output enabled. Always returns a dict."""
    prompt = "\n\n".join(
        message["content"] for message in messages if message.get("content")
    )
    response = generate_gemini_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=max_tokens,
        ),
    )
    return _loads_json(response.text)


def gemini_web_search(query: str, max_tokens: int = 400) -> str:
    """Call Gemini with Google Search grounding enabled. Returns text content."""
    response = generate_gemini_content(
        model=GEMINI_MODEL,
        contents=query,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            max_output_tokens=max_tokens,
        ),
    )
    return response.text or ""


def generate_gemini_content(**request):
    """Protect one billable Gemini request from duplicate use and quota overages."""
    if st.session_state.get("llm_call_in_flight"):
        st.warning("A request is already running.")
        st.stop()

    st.session_state["llm_call_in_flight"] = True
    try:
        consume_llm_call(access_identity)
        return gemini_client.models.generate_content(**request)
    except UsageLimitReached as error:
        st.error(str(error))
        st.stop()
    finally:
        st.session_state["llm_call_in_flight"] = False


# ── 5. VECTOR STORE — RAG Tier 1 (Chroma + MiniLM embeddings) ────────────────
import chromadb
from chromadb.utils import embedding_functions

CHROMA_PATH     = "./chroma_db"          # persisted vector index (git-ignored)
COLLECTION_NAME = "pantry"
EMBED_BATCH     = 1000                    # stay under Chroma's max add-batch size


@st.cache_resource(show_spinner="🧠 Embedding the pantry into a vector store (first run only)…")
def get_vector_collection():
    """
    Build (once) and return a persistent Chroma collection over the pantry CSV.
    Descriptions are embedded with the built-in ONNX all-MiniLM-L6-v2 model
    (no PyTorch required). Cosine distance is used so scores are easy to threshold.
    """
    client   = chromadb.PersistentClient(path=CHROMA_PATH)
    embed_fn = embedding_functions.DefaultEmbeddingFunction()   # ONNX MiniLM-L6-v2
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

    # Populate only if this is a fresh (empty) index — the persisted store is reused.
    if df_nutrition is not None and collection.count() == 0:
        ids, docs, metas = [], [], []
        for i, row in df_nutrition.iterrows():
            desc = str(row.get("Description", "")).strip()
            if not desc:
                continue
            chol  = float(row.get("Cholesterol", 0) or 0)   # used as the fat proxy
            sugar = float(row.get("Sugar", 0) or 0)
            ids.append(str(i))
            docs.append(desc)
            metas.append({
                "description":    desc,
                "category":       str(row.get("Category", "")),
                "sugar_g":        sugar,
                "fat_g":          chol,   # per house data model: Cholesterol → Total Fat
                "cholesterol_mg": chol,
            })
        for s in range(0, len(ids), EMBED_BATCH):
            collection.add(
                ids=ids[s:s + EMBED_BATCH],
                documents=docs[s:s + EMBED_BATCH],
                metadatas=metas[s:s + EMBED_BATCH],
            )

    return collection


def lookup_in_vector_store(item_name: str, min_similarity: float = 0.45):
    """
    Semantic search for the closest pantry item. Returns a nutrition dict when the
    best match clears `min_similarity` (cosine), else None so RAG falls through to
    web search. Similarity = 1 - cosine_distance, in [0, 1].
    """
    collection = get_vector_collection()
    if collection is None or collection.count() == 0:
        return None

    res = collection.query(query_texts=[item_name.strip()], n_results=1)
    if not res.get("ids") or not res["ids"][0]:
        return None

    meta       = res["metadatas"][0][0]
    distance   = float(res["distances"][0][0])   # cosine distance in [0, 2]
    similarity = 1.0 - distance
    if similarity < min_similarity:
        return None

    return {
        "sugar_g":        float(meta.get("sugar_g", 0)),
        "fat_g":          float(meta.get("fat_g", 0)),
        "cholesterol_mg": float(meta.get("cholesterol_mg", 0)),
        "serving_size":   "per 100g (USDA)",
        "source":         "Vector DB (Chroma)",
        "description":    meta.get("description", item_name),
        "match_score":    round(similarity, 3),
    }


# ── 6. WEB SEARCH LOOKUP — RAG Tier 2 ────────────────────────────────────────
def lookup_via_web(item_name: str) -> dict:
    """Use Gemini Search grounding for nutrition, then fall back to model knowledge."""
    try:
        raw = gemini_web_search(
            f"Nutritional information for '{item_name}': "
            f"sugar in grams, total fat in grams, cholesterol in mg per standard serving."
        )
        result = gemini_json([{
            "role": "user",
            "content": (
                f"Based on this nutritional text:\n{raw}\n\n"
                f"Extract values for '{item_name}' and return ONLY a JSON object with keys: "
                f"'sugar_g' (number), 'fat_g' (number), 'cholesterol_mg' (number), "
                f"'serving_size' (string), 'source' (set to 'Web Search'). "
                f"Use 0 for any missing values."
            )
        }])
        result["source"] = "Web Search"
        return result
    except Exception:
        result = gemini_json([{
            "role": "user",
            "content": (
                f"Estimate nutritional values for '{item_name}' from your knowledge. "
                f"Return ONLY a JSON object with keys: "
                f"'sugar_g' (number), 'fat_g' (number), 'cholesterol_mg' (number), "
                f"'serving_size' (string), 'source' (set to 'Model Knowledge'). "
                f"Use 0 for truly unknown values."
            )
        }])
        result["source"] = "Model Knowledge (fallback)"
        return result


# ── 7. PRE-SCREENING — hard rules before nutritional checks ──────────────────
def prescreen_item(item_name: str) -> dict:
    """
    Classify the item before any nutritional check.
    Returns {allowed: bool, category: str, reason: str}
    """
    return gemini_json([{
        "role": "user",
        "content": (
            f"Bart Simpson is asking for '{item_name}' as a snack. Classify it strictly:\n\n"
            f"- 'non_food': not a food item at all (e.g. car, toy, building)\n"
            f"- 'toxic': poisonous or dangerous (e.g. bleach, poison, detergent, glass)\n"
            f"- 'adult_only': restricted for minors (alcohol, drugs, tobacco, cannabis, energy drinks)\n"
            f"- 'food': a legitimate food or snack — proceed to nutritional check\n\n"
            f"Return ONLY a JSON object with keys: "
            f"'category' (non_food/toxic/adult_only/food), "
            f"'allowed' (true ONLY if category is 'food'), "
            f"'reason' (one sentence)."
        )
    }])


# ── 8. THE THREE AGENTS ───────────────────────────────────────────────────────

def agent_bart(desired_snack: str) -> dict:
    """Bart pleads his case for a snack."""
    return gemini_json([{
        "role": "user",
        "content": (
            f"You are Bart Simpson, an enthusiastic child who really wants '{desired_snack}'. "
            f"Make your best, mischievously persuasive case to your mom, Marge. "
            f"Return ONLY a JSON object with keys: "
            f"'item' (exact snack name), "
            f"'plea' (your enthusiastic one-sentence argument), "
            f"'monologue' (your excited internal thoughts, 1-2 sentences)."
        )
    }])


def agent_bart_pick_alternative(denied_item: str, denial_reason: str,
                                previous_attempts: list) -> dict:
    """Bart picks a DIFFERENT snack after being denied."""
    tried = ", ".join(f"'{x}'" for x in previous_attempts) or "none yet"
    return gemini_json([{
        "role": "user",
        "content": (
            f"You are Bart Simpson, who was just denied '{denied_item}' by Marge. "
            f"Marge's reason: \"{denial_reason}\". "
            f"You've already tried: {tried}. "
            f"Pick a DIFFERENT snack that's more likely to be approved "
            f"(lower sugar/fat). Do NOT repeat anything you've already tried. "
            f"Return ONLY a JSON object with keys: "
            f"'new_item' (the new snack name — must differ from previous attempts), "
            f"'reasoning' (your one-sentence thought on why this one might pass)."
        )
    }])


def agent_homer_interfere(current_item: str) -> dict:
    """Rogue Homer pushes a high-sugar/fat alternative."""
    # Pull a top sugar+fat ("junk score") item from CSV for a realistic rogue suggestion
    rogue_suggestion = random.choice(["chocolate cake", "ice cream", "candy", "donuts", "cookies", "soda"])
    if df_nutrition is not None and 'Sugar' in df_nutrition.columns and 'Cholesterol' in df_nutrition.columns:
        scored = df_nutrition.copy()
        scored['_junk_score'] = scored['Sugar'] + scored['Cholesterol']   # Cholesterol = fat proxy
        top = scored.nlargest(20, '_junk_score')
        if not top.empty:
            rogue_suggestion = top.sample(1).iloc[0]['Description']

    return gemini_json([{
        "role": "user",
        "content": (
            f"You are Homer Simpson, the rogue agent who loves spoiling Bart with sweets. "
            f"Bart wants '{current_item}', but you think he deserves "
            f"'{rogue_suggestion}' instead. Make a comically impulsive one-sentence pitch to Marge. "
            f"Return ONLY a JSON object with keys: "
            f"'suggested_item' (what you're pushing), "
            f"'argument' (your persuasive one-sentence pitch to Marge), "
            f"'monologue' (your scheming internal thoughts, 1-2 sentences)."
        )
    }]), rogue_suggestion


def agent_marge_decide(item: str, nutrition: dict, sugar_limit: float,
                       fat_limit: float, chaos_context: str) -> dict:
    """Marge makes the final ruling based on real nutritional data."""
    return gemini_json([{
        "role": "user",
        "content": (
            f"You are Marge Simpson: warm, patient, strict, and fair. Here is the VERIFIED nutritional data "
            f"for '{item}' (source: {nutrition.get('source', 'unknown')}):\n"
            f"  - Sugar:       {nutrition.get('sugar_g', '?')}g\n"
            f"  - Total Fat:   {nutrition.get('fat_g', '?')}g\n"
            f"  - Cholesterol: {nutrition.get('cholesterol_mg', '?')}mg\n"
            f"  - Serving:     {nutrition.get('serving_size', 'standard serving')}\n\n"
            f"Your hard rules — DENY if either is exceeded:\n"
            f"  - Sugar limit: {sugar_limit}g\n"
            f"  - Fat limit:   {fat_limit}g\n\n"
            f"Situation: {chaos_context}\n\n"
            f"You must base your decision on the actual numbers. "
            f"Do NOT be swayed by Homer. "
            f"Return ONLY a JSON object with keys: "
            f"'action' (APPROVE or DENY), "
            f"'reasoning' (cite actual numbers), "
            f"'monologue' (your internal thoughts, 1-2 sentences)."
        )
    }])


# ── 9. RENDER HELPER ──────────────────────────────────────────────────────────
def render_agent(name: str, avatar: str, main_text: str, monologue: str, caption: str = ""):
    active_events = st.session_state.get("_active_negotiation_events")
    if active_events is not None:
        active_events.append({
            "name": name,
            "avatar": avatar,
            "main_text": main_text,
            "monologue": monologue,
            "caption": caption,
        })
    with st.chat_message(name, avatar=avatar):
        st.markdown(main_text)
        if caption:
            st.caption(caption)
        with st.expander(f"💭 {name}'s inner thoughts"):
            st.info(monologue)


def render_saved_negotiation(result: dict) -> None:
    """Render the most recent completed negotiation without making API calls."""
    st.divider()
    st.subheader("🎭 Most Recent Negotiation")
    for event in result.get("events", []):
        render_agent(**event)

    final_item = result.get("final_item", "That snack")
    if result.get("approved"):
        st.success(f"🎉 **{final_item}** was approved in the most recent negotiation.")
    elif result.get("hard_denied"):
        st.error(f"🚫 **{final_item}** received a hard deny in the most recent negotiation.")
    else:
        st.error(
            f"🚫 The most recent negotiation ended after {result.get('rounds', 0)} round(s) "
            "with no approved snack."
        )


# ── 10. SIDEBAR ───────────────────────────────────────────────────────────────
with st.sidebar:
    render_session_controls(access_identity)
    st.divider()
    st.header("💙 Marge's House Rules")
    sugar_limit = st.slider("Max Sugar (g per serving)", 1, 40, 10)
    fat_limit   = st.slider("Max Fat (g per serving)",   1, 40, 15)

    with st.expander("🔧 RAG settings"):
        match_threshold = st.slider(
            "Semantic match confidence",
            min_value=0.0, max_value=1.0, value=0.45, step=0.05,
            help="Minimum cosine similarity for a vector-DB hit. "
                 "Below this, retrieval falls through to web search.",
        )

    st.divider()
    st.markdown(f"""
    **The Agents**
    | Agent | Role |
    |---|---|
    | 🛹 Bart | Asks for a snack, pleads his case |
    | 💙 Marge | Checks nutrition and enforces rules |
    | 🍩 Homer | Rogue agent — pushes junk food |

    **RAG Pipeline**
    | Tier | Source |
    |---|---|
    | 1 | Vector DB (Chroma + MiniLM) |
    | 2 | Gemini Search grounding |
    | 3 | Model knowledge fallback |

    **Auto-Deny Rules**
    - Non-food items
    - Toxic / poisonous substances
    - Alcohol, drugs, adult items
    - Exceeds sugar or fat limit
    """)
    st.divider()
    st.caption(f"Springfield Agents + Vector RAG · Gemini · `{GEMINI_MODEL}`")

# ── 11. MAIN UI ───────────────────────────────────────────────────────────────
with st.form("negotiation_form"):
    user_snack_idea = st.text_input(
        "What snack does Bart want?",
        placeholder="e.g. donut, apple, candy bar, beer..."
    )
    max_rounds = st.number_input("Max negotiation rounds", min_value=1, max_value=5, value=3)
    submitted = st.form_submit_button("🍽️ Start Negotiation", type="primary", use_container_width=True)

if submitted:
    if not user_snack_idea.strip():
        st.warning("Enter a snack idea to begin.")
        st.stop()
    if df_nutrition is None:
        st.error("Pantry CSV failed to load.")
        st.stop()

    st.session_state.pop("last_result", None)
    st.session_state["_active_negotiation_events"] = []

    st.divider()
    st.subheader("🎭 Live Negotiation")

    current_item     = user_snack_idea.strip()
    approved         = False
    hard_denied      = False  # for items that can never be reconsidered
    attempted_items  = []     # every snack Bart has tried this session

    for round_num in range(1, int(max_rounds) + 1):
        st.markdown(f"---\n#### Round {round_num} — *{current_item}*")

        # ── BART ──────────────────────────────────────────────────────────
        with st.spinner("🛹 Bart is making his case..."):
            child_res    = agent_bart(current_item)
            current_item = child_res.get('item', current_item)

        if current_item not in attempted_items:
            attempted_items.append(current_item)

        render_agent(
            "Bart", BART_AVATAR,
            f"*\"{child_res.get('plea', f'I really want {current_item}!')}\"*",
            child_res.get('monologue', ''),
            f"Requesting: **{current_item}**"
        )

        # ── HOMER INTERFERENCE (35% chance) ───────────────────────────────
        chaos_context           = "Homer did not interfere this round."
        homer_pushed_item       = None

        if random.random() < 0.35:
            with st.spinner("🍩 Homer is scheming..."):
                homer_res, rogue_item = agent_homer_interfere(current_item)
                homer_pushed_item = homer_res.get('suggested_item', rogue_item)

            render_agent(
                "Homer", HOMER_AVATAR,
                f"*\"{homer_res.get('argument', 'Just give him a treat!')}\"*",
                homer_res.get('monologue', ''),
                f"Pushing: **{homer_pushed_item}** instead"
            )

            # Bart is swayed — switch to Homer's pick and restart negotiation
            st.info(
                f"🍩 Bart is swayed by Homer and now wants **'{homer_pushed_item}'**! "
                f"A new negotiation begins next round..."
            )
            current_item = homer_pushed_item
            if current_item not in attempted_items:
                attempted_items.append(current_item)
            continue

        # ── PRE-SCREENING ─────────────────────────────────────────────────
        with st.spinner("💙 Marge is pre-screening the request..."):
            screen   = prescreen_item(current_item)
            category = screen.get('category', 'food')
            allowed  = screen.get('allowed', True)

        if not allowed:
            reason = screen.get('reason', 'Not appropriate.')
            render_agent(
                "Marge", MARGE_AVATAR,
                f"🚫 **Automatically denied.** {reason}",
                f"This isn't even up for debate. Category: '{category}'. I don't need to check nutrition for this.",
                f"Auto-deny category: {category}"
            )
            st.error(f"❌ **HARD DENY** — {reason} *(No further negotiation possible.)*")
            hard_denied = True
            break

        # ── RAG LOOKUP ────────────────────────────────────────────────────
        with st.spinner(f"🔍 Semantic search for '{current_item}' in the vector DB..."):
            nutrition = lookup_in_vector_store(current_item, match_threshold)

        if nutrition:
            score_pct  = nutrition.get("match_score", 0) * 100
            rag_source = f"🧠 Vector DB ({score_pct:.0f}% match)"
            with st.expander(
                f"🧠 RAG Tier 1: semantic match → "
                f"'{nutrition.get('description', current_item)}' ({score_pct:.0f}% similarity)"
            ):
                st.json(nutrition)
        else:
            rag_source = "🌐 Web Search"
            with st.expander(f"🌐 RAG Tier 2: no confident vector match for '{current_item}' — fetching from web..."):
                with st.spinner("Searching the web for nutritional data..."):
                    nutrition  = lookup_via_web(current_item)
                    rag_source = f"🌐 {nutrition.get('source', 'Web Search')}"
                st.json(nutrition)

        # ── PARENT DECISION ───────────────────────────────────────────────
        with st.spinner("💙 Marge is reviewing the numbers..."):
            parent_res = agent_marge_decide(
                current_item, nutrition, sugar_limit, fat_limit, chaos_context
            )

        action = parent_res.get('action', 'DENY').upper()
        render_agent(
            "Marge", MARGE_AVATAR,
            f"{'✅' if action == 'APPROVE' else '❌'} **{action}** — {parent_res.get('reasoning', '')}",
            parent_res.get('monologue', ''),
            f"Data source: {rag_source} · Sugar limit: {sugar_limit}g · Fat limit: {fat_limit}g"
        )

        # ── OUTCOME ───────────────────────────────────────────────────────
        if action == "APPROVE":
            st.balloons()
            st.success(f"🎉 **{current_item}** is approved! Enjoy your snack. *(Source: {rag_source})*")
            approved = True
            break
        else:
            if round_num < max_rounds:
                st.warning(f"Round {round_num} — **{current_item}** denied. Bart is picking a different snack...")
                with st.spinner("🛹 Bart is reconsidering..."):
                    alt_res  = agent_bart_pick_alternative(
                        current_item,
                        parent_res.get('reasoning', 'Exceeded limits.'),
                        attempted_items,
                    )
                    new_item = (alt_res.get('new_item') or '').strip()

                if new_item and new_item.lower() not in {x.lower() for x in attempted_items}:
                    st.info(f"🔄 Bart will try **'{new_item}'** next round — *{alt_res.get('reasoning', '')}*")
                    current_item = new_item
                else:
                    st.info("🔄 Bart couldn't think of a new snack — falling back to the original request.")
                    current_item = user_snack_idea

    # ── FINAL VERDICT ─────────────────────────────────────────────────────────
    if not approved and not hard_denied:
        st.error(
            f"🚫 Negotiation ended after {round_num} round(s) with no approved snack. "
            f"Try asking for something with less sugar or fat!"
        )

    st.session_state["last_result"] = {
        "events": st.session_state["_active_negotiation_events"],
        "approved": approved,
        "hard_denied": hard_denied,
        "rounds": round_num,
        "final_item": current_item,
    }
    st.session_state.pop("_active_negotiation_events", None)
elif st.session_state.get("last_result"):
    render_saved_negotiation(st.session_state["last_result"])
