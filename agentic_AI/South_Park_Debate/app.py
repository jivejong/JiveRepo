import streamlit as st
from groq import Groq
import random
import time
from typing import Tuple

# OpenTelemetry Imports
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
st.set_page_config(page_title="South Park: Town Hall Debate", page_icon="🎤", layout="wide")

MODEL_NAME = "openai/gpt-oss-20b"

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

tracer, otel_exporter = setup_opentelemetry()

# ==========================================
# CLIENT INIT & CORE LLM FUNCTIONS
# ==========================================
@st.cache_resource
def get_groq_client() -> Groq:
    try:
        api_key = st.secrets["GROQ_API_KEY"]
        return Groq(api_key=api_key)
    except KeyError:
        st.error("⚠️ `GROQ_API_KEY` is missing in `.streamlit/secrets.toml`.")
        st.stop()

def generate_response(client: Groq, character: str, prompt: str, temp: float, role_type: str = "combatant") -> Tuple[str, int]:
    if role_type == "judge":
        system_instruction = JUDGES[character]
    elif role_type == "moderator":
        system_instruction = MODERATOR_PROMPT
    elif role_type == "announcer":
        system_instruction = ANNOUNCER_PROMPT
    else:
        system_instruction = PERSONAS[character]
    
    with tracer.start_as_current_span(f"llm_call_{character.replace(' ', '_')}") as span:
        start_time = time.time()
        
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ],
                temperature=temp
            )
            
            latency = time.time() - start_time
            text_response = response.choices[0].message.content
            
            if not text_response or not text_response.strip():
                finish_reason = getattr(response.choices[0], "finish_reason", "unknown")
                text_response = f"*(API Issue)* Generation halted. Reason: {finish_reason}."
                
            if hasattr(response, "usage") and response.usage is not None:
                prompt_tokens = getattr(response.usage, "prompt_tokens", 0)
                completion_tokens = getattr(response.usage, "completion_tokens", 0)
                total_tokens = getattr(response.usage, "total_tokens", 0)
            else:
                prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
            
            span.set_attribute("agent.name", character)
            span.set_attribute("agent.role", role_type)
            span.set_attribute("metrics.latency_sec", round(latency, 2))
            span.set_attribute("metrics.prompt_tokens", prompt_tokens)
            span.set_attribute("metrics.completion_tokens", completion_tokens)
            span.set_attribute("metrics.total_tokens", total_tokens)
            
        except Exception as e:
            text_response = f"*(API Error)*: {str(e)}"
            total_tokens = 0
            span.record_exception(e)
            
        return text_response, total_tokens

# ==========================================
# UI & APPLICATION FLOW
# ==========================================
def main():
    st.title("🎤 South Park: Town Hall Debate")
    st.markdown(f"A multi-agent LLM debate powered by Groq and `{MODEL_NAME}`.")
    
    client = get_groq_client()
    
    with st.sidebar:
        st.header("⚙️ Match Configuration")
        
        temperature = st.slider("Agent Temperature (Creativity/Chaos)", min_value=0.0, max_value=1.0, value=0.7, step=0.1)
        rounds = st.slider("Number of Rounds", min_value=1, max_value=5, value=2)
        
        category = st.radio("Select Roster", ["Children", "Adults"])
        
        child_roster = [
            "Stan Marsh", "Kyle Broflovski", "Eric Cartman", "Kenny McCormick", 
            "Butters Stotch", "Jimmy Valmer", "Timmy Burch", "Clyde Donovan", 
            "Tolkien Black", "Craig Tucker", "Tweek Tweak", "Towelie"
        ]
        
        adult_roster = [
            "Randy Marsh", "Sheila Broflovski", "Liane Cartman", "Stuart McCormick",
            "Mr. Garrison", "PC Principal", "Sharon Marsh", "Gerald Broflovski",
            "Stephen Stotch", "Jimbo Kern", "Ned Gerblansky", "Carol McCormick",
            "Principal Victoria", "Officer Barbrady", "Big Gay Al"
        ]
        
        roster = child_roster if category == "Children" else adult_roster
        
        col1, col2 = st.columns(2)
        with col1:
            opp1 = st.selectbox("Opponent 1 (FOR)", roster, index=2)
        with col2:
            opp2 = st.selectbox("Opponent 2 (AGAINST)", roster, index=0)
            
        if opp1 == opp2:
            st.error("Opponents must be different!")
            st.stop()
            
        start_battle = st.button("⚖️ Generate Topic & Start Debate", use_container_width=True, type="primary")

    if start_battle:
        otel_exporter.clear()
        total_session_tokens = 0
        
        st.header("🏛️ The Town Hall Arena")
        
        with st.spinner("Mayor McDaniels is thinking of a topic..."):
            topic, tokens = generate_response(client, "Moderator", "Give us today's debate topic.", temperature, role_type="moderator")
            total_session_tokens += tokens
            
        st.success(f"**TODAY'S TOPIC:** {topic}")
        
        stances = {opp1: "FOR (Affirmative)", opp2: "AGAINST (Negative)"}
        st.write(f"**{opp1}** will be arguing **{stances[opp1]}**.")
        st.write(f"**{opp2}** will be arguing **{stances[opp2]}**.")
        st.divider()
        
        st.subheader("🪙 Coin Flip for Opening Statement")
        coin_toss = random.choice([opp1, opp2])
        first_agent = coin_toss
        second_agent = opp2 if first_agent == opp1 else opp1
        st.info(f"The coin landed on heads! **{first_agent}** gets the opening statement.")
        
        transcript = f"Topic: {topic}\n{opp1} is FOR. {opp2} is AGAINST.\n\n"
        
        st.subheader("🔥 The Debate")
        
        for r in range(1, rounds + 1):
            st.markdown(f"#### Round {r}")
            
            for current_agent, opponent in [(first_agent, second_agent), (second_agent, first_agent)]:
                with st.spinner(f"{current_agent} is preparing their argument..."):
                    prompt = (
                        f"The debate topic is: '{topic}'.\n"
                        f"Your stance is: {stances[current_agent]}.\n"
                        f"Your opponent {opponent} is arguing {stances[opponent]}.\n"
                        f"Here is the transcript so far:\n{transcript}\n\n"
                        f"Deliver your next debate argument. Stay strictly in character."
                    )
                    
                    argument, tokens = generate_response(client, current_agent, prompt, temperature, role_type="combatant")
                    total_session_tokens += tokens
                    transcript += f"[{current_agent}]: {argument}\n"
                    
                    with st.chat_message("user" if current_agent == first_agent else "assistant"):
                        st.markdown(f"**{current_agent}**: {argument}")
            st.divider()

        st.subheader("⚖️ The Judges' Verdict")
        
        judge_tabs = st.tabs(list(JUDGES.keys()))
        judge_evaluations = [] 
        
        for idx, judge in enumerate(JUDGES.keys()):
            with judge_tabs[idx]:
                with st.spinner(f"{judge} is evaluating the debate..."):
                    prompt = (
                        f"The debate between {opp1} and {opp2} on the topic '{topic}' has ended. "
                        f"Here is the transcript:\n{transcript}\n\n"
                        f"Provide your evaluation of their logic and pick a winner."
                    )
                    eval_text, tokens = generate_response(client, judge, prompt, temperature, role_type="judge")
                    total_session_tokens += tokens
                    judge_evaluations.append(f"[{judge}'s Verdict]: {eval_text}")
                    
                    with st.chat_message("assistant"):
                        st.markdown(f"**{judge}**: {eval_text}")

        st.divider()
        st.subheader("🏆 The Final Verdict")
        
        with st.spinner("Terrance and Phillip are reviewing the judges' scorecards..."):
            judges_combined = "\n\n".join(judge_evaluations)
            tp_prompt = (
                f"The debate was between {opp1} and {opp2}.\n"
                f"Here is what the judges decided:\n{judges_combined}\n\n"
                f"Based on the judges' scores, declare the ultimate winner!"
            )
            
            announcer_text, tokens = generate_response(client, "Terrance & Phillip", tp_prompt, temperature, role_type="announcer")
            total_session_tokens += tokens
            
        with st.chat_message("assistant"):
            st.markdown(f"**Terrance & Phillip 🇨🇦**: {announcer_text}")


        st.divider()
        st.subheader("📊 OpenTelemetry Trace & Stats")
        
        spans = otel_exporter.get_finished_spans()
        telemetry_data = []
        for span in spans:
            attrs = span.attributes
            if attrs:
                telemetry_data.append({
                    "Agent": attrs.get("agent.name"),
                    "Role": attrs.get("agent.role"),
                    "Latency (s)": attrs.get("metrics.latency_sec"),
                    "Prompt Tokens": attrs.get("metrics.prompt_tokens"),
                    "Completion Tokens": attrs.get("metrics.completion_tokens"),
                    "Total Tokens": attrs.get("metrics.total_tokens"),
                })
        
        if telemetry_data:
            st.dataframe(telemetry_data, use_container_width=True)
            st.metric(label="Total Session Tokens Used", value=total_session_tokens)
            st.success(f"Debate completed using `{MODEL_NAME}` in {rounds} rounds.")

if __name__ == "__main__":
    main()