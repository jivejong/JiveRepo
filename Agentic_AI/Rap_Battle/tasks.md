# SDLC Task List: Agentic Cypher

## Phase 1: Backend Foundation (Python)
- [ ] **Task 1.1:** Initialize Python virtual environment and install dependencies (`langgraph`, `fastapi`, `uvicorn`, `google-genai`, `opentelemetry-sdk`).
- [ ] **Task 1.2:** Implement OpenTelemetry configuration (Tracer, InMemorySpanExporter).
- [ ] **Task 1.3:** Define the `CypherState` TypedDict and basic agent signatures (Prompts) for East MC, West MC, Moderator, and Security.

## Phase 2: LangGraph Orchestration
- [ ] **Task 2.1:** Implement the `generate_verse` worker function connecting to `gemini-3.1-flash-lite`, including OTel span wrapping and token counting.
- [ ] **Task 2.2:** Implement the `heat_check` evaluator node (Structured JSON output parsing).
- [ ] **Task 2.3:** Build the LangGraph node routing, including conditional edges for the circuit breaker (`crossed_the_line`).
- [ ] **Task 2.4:** Wrap the compiled graph in a FastAPI endpoint (`/api/cypher`) that accepts initialization parameters and streams the state updates.

## Phase 3: Frontend Foundation (Next.js)
- [ ] **Task 3.1:** Initialize Next.js app with Tailwind CSS. Install `framer-motion` and `gsap`.
- [ ] **Task 3.2:** Scaffold the `BattleStage` component layout (Split screen, central text prompter).
- [ ] **Task 3.3:** Implement frontend state management to connect to the FastAPI stream and update the `currentMC`, `latestBar`, and `heatScore`.

## Phase 4: Animation & Audio Sync
- [ ] **Task 4.1:** Hook up GSAP animations to trigger when `latestBar` updates (text slam effect).
- [ ] **Task 4.2:** Implement Framer Motion background variants tied to `heatScore` (e.g., screen dims/flashes red if heat >= 7).
- [ ] **Task 4.3:** Add `audio` elements for pre-rendered beats. Tie `play()` and `pause()` events to `currentMC` state transitions.

## Phase 5: Testing & QA
- [ ] **Task 5.1:** Execute TC-01 (Standard loop). Verify transcript generates without crashing.
- [ ] **Task 5.2:** Execute TC-04 (Circuit Breaker). Hardcode a toxic prompt and verify Security agent routes correctly and terminates the run.
- [ ] **Task 5.3:** Verify OTel token counts sum correctly for the entire session.