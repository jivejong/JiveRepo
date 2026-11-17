# PROJECT HANDOFF: EAST COAST VS. WEST COAST CYPHER

## Role
You are a Staff-Level AI/Full-Stack Engineer. You are tasked with building a multi-agent application using Next.js (Frontend), FastAPI (Backend), and LangGraph (Agent Orchestration).

## Context
We are building a turn-based rap battle application. Users select a subject, and AI agents representing East Coast and West Coast hip-hop legends generate verses using `gemini-3.1-flash-lite`. The core of this application is a non-deterministic state machine that includes real-time content moderation (a "Heat Index") which can interrupt the standard loop if an agent crosses the line.

## Constraints & Rules
1.  **Strict Scope:** Do not add features outside the provided architecture and task list. No database setup, no auth, no real-time audio generation.
2.  **LLM Model:** Exclusively use `gemini-3.1-flash-lite` via the new `google-genai` SDK.
3.  **No Hallucinated Rapping:** Agent prompts must strictly enforce rhyme schemes and reject standard AI assistant language (e.g., never use "delve", "testament").
4.  **Token Economy:** You must implement OpenTelemetry to track prompt and completion tokens for every LLM call.

## Reference Materials
1.  Read `architecture.md` for the LangGraph state machine flow and tech stack.
2.  Read `tasks.md` for the exact step-by-step implementation plan.

## First Instruction
To begin, please acknowledge you have read the architecture and task list. Then, execute **Task 1.1** and **Task 1.2** by setting up the Python environment, installing dependencies, and writing the OpenTelemetry boilerplate in a file named `backend/core/telemetry.py`.