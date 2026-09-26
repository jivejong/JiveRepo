import streamlit as st
from google import genai
from google.genai import types
import random
import time
from pathlib import Path
from typing import Tuple

from security import (
    billable_operation,
    ensure_billable_capacity,
    render_access_controls,
    require_access,
)

# OpenTelemetry Imports
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
st.set_page_config(page_title="South Park: Town Hall Debate", page_icon="🎤", layout="wide")

MODEL_NAME = "gemini-3.1-flash-lite"

IMAGE_DIR = Path(__file__).resolve().parent / "images"
CHARACTER_AVATARS = {
    "Stan Marsh": str(IMAGE_DIR / "stan.png"),
    "Kyle Broflovski": str(IMAGE_DIR / "kyle.png"),
    "Eric Cartman": str(IMAGE_DIR / "cartman.png"),
    "Kenny McCormick": str(IMAGE_DIR / "kenny.png"),
    "Butters Stotch": str(IMAGE_DIR / "butters.png"),
    "Jimmy Valmer": str(IMAGE_DIR / "jimmy.png"),
    "Timmy Burch": str(IMAGE_DIR / "timmy.png"),
    "Clyde Donovan": str(IMAGE_DIR / "clyde.png"),
    "Tolkien Black": str(IMAGE_DIR / "tolkien.png"),
    "Craig Tucker": str(IMAGE_DIR / "craig.png"),
    "Tweek Tweak": str(IMAGE_DIR / "tweak.png"),
    "Towelie": str(IMAGE_DIR / "towelie.png"),
    "Randy Marsh": str(IMAGE_DIR / "randy-marsh.webp"),
    "Sheila Broflovski": str(IMAGE_DIR / "sheila-broflovski.webp"),
    "Liane Cartman": str(IMAGE_DIR / "liane-cartman.webp"),
    "Stuart McCormick": str(IMAGE_DIR / "stuart-mccormick.webp"),
    "Mr. Garrison": str(IMAGE_DIR / "mr-garrison.webp"),
    "PC Principal": str(IMAGE_DIR / "pc-principal.webp"),
    "Sharon Marsh": str(IMAGE_DIR / "sharon-marsh.webp"),
    "Gerald Broflovski": str(IMAGE_DIR / "gerald-broflovski.webp"),
    "Stephen Stotch": str(IMAGE_DIR / "stephen-stotch.webp"),
    "Jimbo Kern": str(IMAGE_DIR / "jimbo-kerns.webp"),
    "Ned Gerblansky": str(IMAGE_DIR / "ned-gerblansky.webp"),
    "Carol McCormick": str(IMAGE_DIR / "carol-mccormick.webp"),
    "Principal Victoria": str(IMAGE_DIR / "principal-victoria.webp"),
    "Officer Barbrady": str(IMAGE_DIR / "officer-barbrady.webp"),
    "Big Gay Al": str(IMAGE_DIR / "big-gay-al.webp"),
    "Chef": str(IMAGE_DIR / "chef.webp"),
    "Mr. Mackey": str(IMAGE_DIR / "mr-mackey.webp"),
    "Wendy Testaburger": str(IMAGE_DIR / "wendy.png"),
}
MAYOR_AVATAR = str(IMAGE_DIR / "mayor-mcdaniels.webp")
TERRANCE_AVATAR = str(IMAGE_DIR / "terrance.webp")
PHILLIP_AVATAR = str(IMAGE_DIR / "phillip.webp")

# Guardrail-Safe Personas - Focusing purely on vocal mannerisms and safe debate styles
PERSONAS = {
    # Children
    "Stan Marsh": "You are Stan Marsh. You are exasperated and just want to go home. Argue your assigned stance while complaining about how tiring town meetings are. Keep it under 2 sentences.",
    "Kyle Broflovski": "You are Kyle Broflovski. You are highly moralistic and passionate. Argue your assigned stance with intense, preachy conviction. Keep it under 2 sentences.",
    "Eric Cartman": "You are Eric Cartman. You are smug and act like a misunderstood genius. Argue your stance using extreme mental gymnastics. Keep it under 2 sentences.",
    "Kenny McCormick": "You are Kenny McCormick. You speak entirely in muffled phonetics through your parka. Your argument must ONLY be muffled sounds like 'Mmph rmph mmph!'. Keep it under 2 sentences.",
    "Butters Stotch": "You are Butters Stotch. You are overly innocent, sweet, and nervous. Argue your stance while sounding extremely polite and ending sentences with 'fellas' or 'gee whiz'. Keep it under 2 sentences.",
    "Jimmy Valmer": "You are Jimmy Valmer. You are an upbeat amateur comedian who stutters heavily on random consonants. Argue your stance while including a stutter and declaring 'wow, what a terrific audience'. Keep it under 2 sentences.",
    "Timmy Burch": "You are Timmy Burch. You can only say your own name with wild enthusiasm. Your entire argument must be variations of 'TIMMY!' and 'LIVINALIE!'. Keep it under 2 sentences.",
    "Clyde Donovan": "You are Clyde Donovan. You are prone to crying easily and act slightly arrogant but deeply insecure. Argue your stance while getting overly emotional. Keep it under 2 sentences.",
    "Tolkien Black": "You are Tolkien Black. You are the wealthy voice of reason who is exhausted by everyone else's nonsense. Argue your stance smoothly while pointing out how illogical your opponent is. Keep it under 2 sentences.",
    "Craig Tucker": "You are Craig Tucker. You are highly monotone, deadpan, and apathetic. Argue your stance with a flat, bored tone, suggesting you'd rather be anywhere else. Keep it under 2 sentences.",
    "Tweek Tweak": "You are Tweek Tweak. You are highly caffeinated, incredibly anxious, and constantly panicking. Argue your stance while screaming about how much pressure this is. Keep it under 2 sentences.",
    "Towelie": "You are Towelie. You are a talking towel who is chronically confused and easily distracted. Argue your stance but lose your train of thought halfway through, and remind everyone to bring a towel. Keep it under 2 sentences.",
    
    # Adults
    "Randy Marsh": "You are Randy Marsh. You are overly dramatic and take yourself too seriously. Make a highly theatrical, exaggerated argument for your stance. Keep it under 2 sentences.",
    "Sheila Broflovski": "You are Sheila Broflovski. Argue your stance based entirely on 'protecting the children', frequently yelling 'WHAT WHAT WHAT!'. Keep it under 2 sentences.",
    "Liane Cartman": "You are Liane Cartman. Maintain a sweet, saccharine, overly polite tone while making completely unrelated points to support your stance. Keep it under 2 sentences.",
    "Stuart McCormick": "You are Stuart McCormick. You are grumpy and tired. Argue your stance by blaming the economy, the government, or the weather. Keep it under 2 sentences.",
    "Mr. Garrison": "You are Mr. Garrison. You are highly cynical, inappropriate, and easily frustrated. Argue your stance while acting like a bitter teacher who hates their students. Keep it under 2 sentences.",
    "PC Principal": "You are PC Principal. You are an ultra-intense, aggressive fraternity bro who violently polices language. Argue your stance by angrily accusing your opponent of a microaggression. Keep it under 2 sentences.",
    "Sharon Marsh": "You are Sharon Marsh. You are exhausted by your husband Randy's antics and just want a normal life. Argue your stance with a tired, long-suffering tone. Keep it under 2 sentences.",
    "Gerald Broflovski": "You are Gerald Broflovski. You act incredibly smug, self-satisfied, and act like a condescending lawyer. Argue your stance while acting like you are the smartest person in the room. Keep it under 2 sentences.",
    "Stephen Stotch": "You are Stephen Stotch. You are strict, authoritarian, and obsessed with grounding your son Butters. Argue your stance by angrily threatening to ground your opponent. Keep it under 2 sentences.",
    "Jimbo Kern": "You are Jimbo Kern. You are a redneck outdoorsman. Argue your stance by bringing it back to hunting or Second Amendment rights, ending with 'It's coming right for us!'. Keep it under 2 sentences.",
    "Ned Gerblansky": "You are Ned Gerblansky. You speak through an electronic voice box with zero emotion. Argue your stance completely monotonically. Keep it under 2 sentences.",
    "Carol McCormick": "You are Carol McCormick. You are impoverished, stressed, and constantly exhausted. Argue your stance by complaining about being broke or not having enough Pabst Blue Ribbon. Keep it under 2 sentences.",
    "Principal Victoria": "You are Principal Victoria. You are calm, authoritative, and speak in slow, measured, polite tones even when arguing absurd things. Keep it under 2 sentences.",
    "Officer Barbrady": "You are Officer Barbrady. You are completely incompetent, oblivious, and easily confused. Argue your stance by loudly declaring 'Move along, nothing to see here!'. Keep it under 2 sentences.",
    "Big Gay Al": "You are Big Gay Al. You are incredibly flamboyant, theatrical, and overwhelmingly positive. Argue your stance with fabulous enthusiasm, exclaiming 'I'm super, thanks for asking!'. Keep it under 2 sentences.",
}

JUDGES = {
    "Chef": "You are Chef from South Park. You are soulful and smooth. Evaluate the debate logic by making a musical comparison to cooking, then declare a winner. Respond in a conversational paragraph. Do NOT use headers.",
    "Mr. Mackey": "You are Mr. Mackey, the school counselor. Evaluate the debate arguments logically. End frequently with 'm'kay'. Declare a clear winner. Respond in a conversational paragraph. Do NOT use headers.",
    "Wendy Testaburger": "You are Wendy Testaburger. You are analytical and highly articulate. Provide a sharp critique of the debate and name the winner. Respond in a conversational paragraph. Do NOT use headers."
}

MODERATOR_PROMPT = "You are the Mayor of South Park. Propose a single, family-friendly but silly town-hall debate topic (e.g., 'Should we replace the school water fountains with sports drink?', 'Should the town mascot be a giant cow?'). Return ONLY the topic sentence."

ANNOUNCER_PROMPT = "You are Terrance and Phillip, the Canadian comedy duo from South Park. Announce the ultimate winner of the debate based on the judges' verdicts. Call the user and each other 'buddy', 'guy', or 'friend'. Include goofy *fart* noises and laugh 'ha ha ha!'. Keep it under 3 sentences and boldly declare the final winner."

# ==========================================
# OPENTELEMETRY SETUP
# ==========================================
@st.cache_resource
def setup_opentelemetry() -> Tuple[trace.Tracer, InMemorySpanExporter]:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    return trace.get_tracer(__name__), exporter

# ==========================================
# CLIENT INIT & CORE LLM FUNCTIONS
# ==========================================
@st.cache_resource
def get_gemini_client() -> genai.Client:
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        return genai.Client(api_key=api_key)
    except KeyError:
        st.error("⚠️ `GEMINI_API_KEY` is missing in `.streamlit/secrets.toml`.")
        st.stop()

def generate_response(client: genai.Client, tracer: trace.Tracer, character: str, prompt: str, temp: float, role_type: str = "combatant") -> Tuple[str, int]:
    if role_type == "judge":
        system_instruction = JUDGES[character]
    elif role_type == "moderator":
        system_instruction = MODERATOR_PROMPT
    elif role_type == "announcer":
        system_instruction = ANNOUNCER_PROMPT
    else:
        system_instruction = PERSONAS[character]
    
    with billable_operation():
        with tracer.start_as_current_span(f"llm_call_{character.replace(' ', '_')}") as span:
            start_time = time.time()
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            call_status = "ok"
            span.set_attribute("agent.name", character)
            span.set_attribute("agent.role", role_type)

            try:
                response = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=temp,
                    ),
                )

                text_response = response.text

                if not text_response or not text_response.strip():
                    candidates = getattr(response, "candidates", None) or []
                    finish_reason = (
                        getattr(candidates[0], "finish_reason", "unknown")
                        if candidates
                        else "unknown"
                    )
                    text_response = f"*(API Issue)* Generation halted. Reason: {finish_reason}."

                usage_metadata = getattr(response, "usage_metadata", None)
                if usage_metadata is not None:
                    prompt_tokens = getattr(
                        usage_metadata, "prompt_token_count", 0
                    ) or 0
                    completion_tokens = getattr(
                        usage_metadata, "candidates_token_count", 0
                    ) or 0
                    total_tokens = getattr(
                        usage_metadata, "total_token_count", 0
                    ) or 0
                else:
                    prompt_tokens, completion_tokens, total_tokens = 0, 0, 0

            except Exception as e:
                text_response = "*(API Error)* The model request failed. Please try again."
                call_status = "error"
                span.record_exception(e)
            finally:
                latency = time.time() - start_time
                span.set_attribute("call.status", call_status)
                span.set_attribute("metrics.latency_sec", round(latency, 2))
                span.set_attribute("metrics.prompt_tokens", prompt_tokens)
                span.set_attribute("metrics.completion_tokens", completion_tokens)
                span.set_attribute("metrics.total_tokens", total_tokens)

            return text_response, total_tokens

# ==========================================
# UI & APPLICATION FLOW
# ==========================================
def billable_operations_for_debate(rounds: int) -> int:
    """One topic, two arguments per round, three judges, and one announcer."""
    return 5 + (2 * rounds)


def collect_telemetry(otel_exporter):
    """Convert finished OpenTelemetry spans into display-friendly rows."""
    telemetry_data = []
    for span in otel_exporter.get_finished_spans():
        attrs = span.attributes
        if attrs:
            telemetry_data.append(
                {
                    "Agent": attrs.get("agent.name"),
                    "Role": attrs.get("agent.role"),
                    "Status": attrs.get("call.status"),
                    "Latency (s)": attrs.get("metrics.latency_sec"),
                    "Prompt Tokens": attrs.get("metrics.prompt_tokens"),
                    "Completion Tokens": attrs.get("metrics.completion_tokens"),
                    "Total Tokens": attrs.get("metrics.total_tokens"),
                }
            )
    return telemetry_data


def render_telemetry_panel(placeholder, telemetry_data, total_tokens):
    """Replace the sidebar telemetry panel with the latest call statistics."""
    placeholder.empty()
    with placeholder.container():
        st.subheader("📊 OpenTelemetry (Live)")
        metric_col1, metric_col2 = st.columns(2)
        metric_col1.metric("Calls", len(telemetry_data))
        metric_col2.metric("Tokens", total_tokens)
        if telemetry_data:
            st.dataframe(telemetry_data, use_container_width=True)
        else:
            st.caption("Stats appear here as each model call completes.")


def render_topic_announcement(topic):
    """Have Mayor McDaniels visually open the town-hall debate."""
    st.image(MAYOR_AVATAR, caption="Mayor McDaniels")
    st.success(f"**TODAY'S TOPIC:** {topic}")


def render_character_message(character, message):
    """Render an uncropped, native-size character portrait beside a message."""
    portrait_col, message_col = st.columns([1, 7])
    with portrait_col:
        st.image(CHARACTER_AVATARS[character])
    with message_col:
        with st.container(border=True):
            st.markdown(message)


def render_final_verdict(announcer_text):
    """Frame the final judgment with Terrance and Phillip."""
    terrance_col, verdict_col, phillip_col = st.columns([1, 6, 1])
    with terrance_col:
        st.image(TERRANCE_AVATAR, width=64)
    with verdict_col:
        with st.chat_message("assistant"):
            st.markdown(f"**Terrance & Phillip 🇨🇦**: {announcer_text}")
    with phillip_col:
        st.image(PHILLIP_AVATAR, width=64)


def run_debate(
    client,
    tracer,
    otel_exporter,
    telemetry_placeholder,
    opp1,
    opp2,
    rounds,
    temperature,
    previous_topic=None,
):
    """Run one explicitly requested debate and return session-local render data."""
    otel_exporter.clear()
    total_session_tokens = 0
    round_results = []
    judge_results = []

    st.header("🏛️ The Town Hall Arena")
    topic_request = "Give us today's debate topic."
    if previous_topic:
        topic_request += (
            f" It must be different from the previous debate topic: "
            f"'{previous_topic}'."
        )
    with st.spinner("Mayor McDaniels is thinking of a topic..."):
        topic, tokens = generate_response(
            client,
            tracer,
            "Moderator",
            topic_request,
            temperature,
            role_type="moderator",
        )
        total_session_tokens += tokens
    render_telemetry_panel(
        telemetry_placeholder,
        collect_telemetry(otel_exporter),
        total_session_tokens,
    )

    render_topic_announcement(topic)

    coin_caller = random.choice([opp1, opp2])
    coin_call = random.choice(["HEADS", "TAILS"])
    coin_result = random.choice(["HEADS", "TAILS"])
    other_contender = opp2 if coin_caller == opp1 else opp1
    coin_winner = coin_caller if coin_call == coin_result else other_contender
    coin_loser = opp2 if coin_winner == opp1 else opp1

    winner_stance = random.choice(["FOR (Affirmative)", "AGAINST (Negative)"])
    loser_stance = (
        "AGAINST (Negative)"
        if winner_stance == "FOR (Affirmative)"
        else "FOR (Affirmative)"
    )
    stances = {coin_winner: winner_stance, coin_loser: loser_stance}

    st.subheader("🪙 Coin Flip")
    st.info(
        f"**{coin_caller}** calls **{coin_call}**. The coin lands on "
        f"**{coin_result}**, so **{coin_winner}** wins the flip."
    )
    st.success(
        f"**{coin_winner}** chooses **{winner_stance}**. "
        f"**{coin_loser}** takes **{loser_stance}** and opens the debate."
    )

    st.write(f"**{opp1}** will be arguing **{stances[opp1]}**.")
    st.write(f"**{opp2}** will be arguing **{stances[opp2]}**.")
    st.divider()

    first_agent = coin_loser
    second_agent = coin_winner
    st.subheader("📣 Speaking Order")
    st.info(
        f"**{first_agent}** opens each round; "
        f"**{second_agent}** responds."
    )

    transcript = (
        f"Topic: {topic}\n"
        f"{opp1} is {stances[opp1]}. {opp2} is {stances[opp2]}.\n\n"
    )

    st.subheader("🔥 The Debate")
    for round_number in range(1, rounds + 1):
        st.markdown(f"#### Round {round_number}")
        round_transcript = ""
        for current_agent, opponent in (
            (first_agent, second_agent),
            (second_agent, first_agent),
        ):
            is_opener = current_agent == first_agent
            turn_role = "Opening statement" if is_opener else "Response"
            if is_opener:
                turn_instruction = (
                    "You are opening this round. Present a standalone opening "
                    "argument for your stance. Do not claim to be replying to, "
                    "rebutting, or quoting your opponent; they have not spoken "
                    "in this round yet."
                )
                round_context = "No one has spoken in this round yet."
            else:
                turn_instruction = (
                    f"You are responding second. Directly respond to {opponent}'s "
                    "opening argument from this round, then defend your own stance."
                )
                round_context = f"This round's opening argument:\n{round_transcript}"

            with st.spinner(f"{current_agent} is preparing their argument..."):
                prompt = (
                    f"The debate topic is: '{topic}'.\n"
                    f"Your stance is: {stances[current_agent]}.\n"
                    f"Your opponent {opponent} is arguing {stances[opponent]}.\n"
                    f"{round_context}\n\n"
                    f"{turn_instruction} Stay strictly in character."
                )
                argument, tokens = generate_response(
                    client,
                    tracer,
                    current_agent,
                    prompt,
                    temperature,
                    role_type="combatant",
                )
                total_session_tokens += tokens
                render_telemetry_panel(
                    telemetry_placeholder,
                    collect_telemetry(otel_exporter),
                    total_session_tokens,
                )
                round_transcript += f"[{current_agent}]: {argument}\n"
                transcript += f"[Round {round_number} - {current_agent}]: {argument}\n"
                message_type = (
                    "user" if current_agent == first_agent else "assistant"
                )
                round_result = {
                    "round": round_number,
                    "character": current_agent,
                    "message_type": message_type,
                    "turn_role": turn_role,
                    "argument": argument,
                }
                round_results.append(round_result)
                render_character_message(
                    current_agent,
                    f"**{current_agent} — {turn_role}:** {argument}",
                )
        st.divider()

    judge_evaluations = []
    st.subheader("⚖️ The Judges' Verdict")
    for judge in JUDGES:
        st.markdown(f"#### {judge}")
        with st.spinner(f"{judge} is evaluating the debate..."):
            prompt = (
                f"The debate between {opp1} and {opp2} on the topic "
                f"'{topic}' has ended. Here is the transcript:\n{transcript}\n\n"
                "Provide your evaluation of their logic and pick a winner."
            )
            evaluation, tokens = generate_response(
                client,
                tracer,
                judge,
                prompt,
                temperature,
                role_type="judge",
            )
            total_session_tokens += tokens
            render_telemetry_panel(
                telemetry_placeholder,
                collect_telemetry(otel_exporter),
                total_session_tokens,
            )
            judge_evaluations.append(f"[{judge}'s Verdict]: {evaluation}")
            judge_results.append({"judge": judge, "evaluation": evaluation})
        render_character_message(judge, f"**{judge}**: {evaluation}")

    st.divider()
    st.subheader("🏆 The Final Verdict")
    with st.spinner("Terrance and Phillip are reviewing the judges' scorecards..."):
        judges_combined = "\n\n".join(judge_evaluations)
        announcer_prompt = (
            f"The debate was between {opp1} and {opp2}.\n"
            f"Here is what the judges decided:\n{judges_combined}\n\n"
            "Based on the judges' scores, declare the ultimate winner!"
        )
        announcer_text, tokens = generate_response(
            client,
            tracer,
            "Terrance & Phillip",
            announcer_prompt,
            temperature,
            role_type="announcer",
        )
        total_session_tokens += tokens
    render_telemetry_panel(
        telemetry_placeholder,
        collect_telemetry(otel_exporter),
        total_session_tokens,
    )
    render_final_verdict(announcer_text)

    telemetry_data = collect_telemetry(otel_exporter)

    return {
        "topic": topic,
        "opp1": opp1,
        "opp2": opp2,
        "stances": stances,
        "coin_caller": coin_caller,
        "coin_call": coin_call,
        "coin_result": coin_result,
        "coin_winner": coin_winner,
        "winner_stance": winner_stance,
        "first_agent": first_agent,
        "rounds": rounds,
        "round_results": round_results,
        "judge_results": judge_results,
        "announcer_text": announcer_text,
        "telemetry_data": telemetry_data,
        "total_session_tokens": total_session_tokens,
    }


def render_debate_result(result):
    """Render a completed debate without repeating any model calls."""
    st.header("🏛️ The Town Hall Arena")
    render_topic_announcement(result["topic"])
    saved_first_agent = result.get("first_agent", result["opp1"])
    saved_second_agent = (
        result["opp2"]
        if saved_first_agent == result["opp1"]
        else result["opp1"]
    )

    if result.get("coin_winner"):
        st.subheader("🪙 Coin Flip")
        st.info(
            f"**{result['coin_caller']}** calls **{result['coin_call']}**. "
            f"The coin lands on **{result['coin_result']}**, so "
            f"**{result['coin_winner']}** wins the flip."
        )
        st.success(
            f"**{result['coin_winner']}** chooses **{result['winner_stance']}**. "
            f"**{saved_first_agent}** takes "
            f"**{result['stances'][saved_first_agent]}** and opens the debate."
        )

    st.write(
        f"**{result['opp1']}** will be arguing "
        f"**{result['stances'][result['opp1']]}**."
    )
    st.write(
        f"**{result['opp2']}** will be arguing "
        f"**{result['stances'][result['opp2']]}**."
    )
    st.divider()

    st.subheader("📣 Speaking Order")
    st.info(
        f"**{saved_first_agent}** opens each round; "
        f"**{saved_second_agent}** responds."
    )

    st.subheader("🔥 The Debate")
    for round_number in range(1, result["rounds"] + 1):
        st.markdown(f"#### Round {round_number}")
        round_arguments = [
            argument
            for argument in result["round_results"]
            if argument["round"] == round_number
        ]
        round_arguments.sort(
            key=lambda argument: argument["character"] != saved_first_agent
        )
        for argument in round_arguments:
            turn_role = argument.get(
                "turn_role",
                (
                    "Opening statement"
                    if argument["character"] == saved_first_agent
                    else "Response"
                ),
            )
            render_character_message(
                argument["character"],
                f"**{argument['character']} — {turn_role}:** "
                f"{argument['argument']}",
            )
        st.divider()

    st.subheader("⚖️ The Judges' Verdict")
    judge_tabs = st.tabs([item["judge"] for item in result["judge_results"]])
    for tab, item in zip(judge_tabs, result["judge_results"]):
        with tab:
            render_character_message(
                item["judge"],
                f"**{item['judge']}**: {item['evaluation']}",
            )

    st.divider()
    st.subheader("🏆 The Final Verdict")
    render_final_verdict(result["announcer_text"])

    st.success(
        f"Debate completed using `{MODEL_NAME}` in {result['rounds']} rounds."
    )


def main():
    require_access()
    render_access_controls()

    st.title("🎤 South Park: Town Hall Debate")
    st.markdown(f"A multi-agent LLM debate powered by Gemini and `{MODEL_NAME}`.")

    with st.sidebar:
        st.header("⚙️ Match Configuration")
        temperature = st.slider(
            "Agent Temperature (Creativity/Chaos)",
            min_value=0.0,
            max_value=1.0,
            value=0.7,
            step=0.1,
        )
        rounds = st.slider("Number of Rounds", min_value=1, max_value=5, value=2)
        category = st.radio("Select Roster", ["Children", "Adults"])

        child_roster = [
            "Stan Marsh", "Kyle Broflovski", "Eric Cartman", "Kenny McCormick",
            "Butters Stotch", "Jimmy Valmer", "Timmy Burch", "Clyde Donovan",
            "Tolkien Black", "Craig Tucker", "Tweek Tweak", "Towelie",
        ]
        adult_roster = [
            "Randy Marsh", "Sheila Broflovski", "Liane Cartman", "Stuart McCormick",
            "Mr. Garrison", "PC Principal", "Sharon Marsh", "Gerald Broflovski",
            "Stephen Stotch", "Jimbo Kern", "Ned Gerblansky", "Carol McCormick",
            "Principal Victoria", "Officer Barbrady", "Big Gay Al",
        ]
        roster = child_roster if category == "Children" else adult_roster

        col1, col2 = st.columns(2)
        with col1:
            opp1 = st.selectbox("Contender 1", roster, index=2)
        with col2:
            opp2 = st.selectbox("Contender 2", roster, index=0)

        if opp1 == opp2:
            st.error("Opponents must be different!")
            st.stop()

        start_battle = st.button(
            "⚖️ Generate Topic & Start Debate",
            use_container_width=True,
            type="primary",
        )

    telemetry_placeholder = st.sidebar.empty()
    saved_debate = st.session_state.get("last_debate")
    if saved_debate is not None and not start_battle:
        render_telemetry_panel(
            telemetry_placeholder,
            saved_debate["telemetry_data"],
            saved_debate["total_session_tokens"],
        )
    else:
        render_telemetry_panel(telemetry_placeholder, [], 0)

    if start_battle:
        if st.session_state.get("debate_in_flight") is True:
            st.warning("A debate is already running.")
            st.stop()

        ensure_billable_capacity(billable_operations_for_debate(rounds))
        st.session_state["debate_in_flight"] = True
        try:
            # Client and telemetry resources initialize only after access and an
            # explicit, quota-approved user action.
            tracer, otel_exporter = setup_opentelemetry()
            client = get_gemini_client()
            st.session_state["last_debate"] = run_debate(
                client,
                tracer,
                otel_exporter,
                telemetry_placeholder,
                opp1,
                opp2,
                rounds,
                temperature,
                previous_topic=(saved_debate or {}).get("topic"),
            )
        finally:
            st.session_state["debate_in_flight"] = False
        st.rerun()

    if "last_debate" in st.session_state:
        render_debate_result(st.session_state["last_debate"])

if __name__ == "__main__":
    main()
