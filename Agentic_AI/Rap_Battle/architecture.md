# East Coast vs. West Coast Cypher - Architecture Document

## 1. System Overview
The application is a multi-agent, turn-based hip-hop cypher driven by a graph-based state machine. It features distinct LLM personas battling each other, moderated by a "Heat Index" circuit breaker, with real-time token telemetry and synced frontend audio playback.

## 2. Technology Stack
*   **Backend / Orchestration:** Python, LangGraph (State Machine), FastAPI (API & Streaming)
*   **LLM Engine:** Google Gemini 3.1 Flash Lite (via `google-genai` SDK)
*   **Frontend:** Next.js, React, Tailwind CSS, Framer Motion, GSAP
*   **Observability:** OpenTelemetry (OTel) Python SDK (Meters and Traces)
*   **Audio:** Pre-rendered static MP3s triggered by state changes

## 3. Agent Topology
1.  **Moderator (Flavor Flav):** Initializes the battle, announces the subject.
2.  **East Coast MC:** Boom-bap style, dense multisyllabic rhymes (temperature: 0.4).
3.  **West Coast MC:** G-Funk style, smooth conversational flow (temperature: 0.7).
4.  **Heat Evaluator (Hidden):** Runs on every verse. Evaluates aggression/threats (1-10).
5.  **Security (Big Tone):** Triggered if Heat Evaluator returns `crossed_the_line == true`. Terminates cypher.
6.  **Judges (Run-DMC):** Evaluates the final transcript if max rounds are reached without a security breach.

## 4. State Machine (LangGraph) Definition
**State Object (`CypherState`):**
*   `subject`: string
*   `east_mc`: string
*   `west_mc`: string
*   `round_count`: int
*   `max_rounds`: int
*   `transcript`: list of strings
*   `heat_score`: int
*   `crossed_the_line`: boolean
*   `status`: string ("active", "security_shutdown", "finished")

**Node Flow:**
[Start] -> [Moderator Intro] -> [Coin Toss] -> [MC 1 Turn]
[MC 1 Turn] -> [Heat Check]
[Heat Check] -> (If Crossed Line) -> [Security] -> [END]
[Heat Check] -> (If Safe) -> [MC 2 Turn]
[MC 2 Turn] -> [Heat Check]
[Heat Check] -> (If Crossed Line) -> [Security] -> [END]
[Heat Check] -> (If Safe & Max Rounds Reached) -> [Judges] -> [END]
[Heat Check] -> (If Safe & Rounds < Max) -> [MC 1 Turn]

## 5. Observability (OTel)
*   Wrap LangGraph node executions in OTel spans.
*   Capture custom attributes: `agent.name`, `agent.role`, `metrics.prompt_tokens`, `metrics.completion_tokens`, `metrics.latency_sec`.