Act as a Principal Systems Architect and Technical Sourcing Specialist. Analyze the provided codebase and generate a production-grade `README.md` and repository metadata optimized for two audiences: enterprise AI sourcing platforms (semantic parsers) and senior engineering leaders.
Please output:

1. 5–10 GitHub Topics: Specific, searchable keywords (lowercase with hyphens, e.g., `event-driven`, `distributed-systems`, `kafka-streams`).
2. One-Line Repo Description: A concise, punchy description highlighting the core engineering problem and primary tech stack.
3. Structured `README.md` containing:
   - System Topology: An ASCII or Mermaid.js diagram illustrating data flow, boundaries, and components.
   - Problem Statement & Constraints: Why this exists, constraints handled (e.g., latency, throughput, backpressure, memory limits).
   - Core Architectural Decisions: Explicit trade-offs made (e.g., why chosen pattern X over alternative Y).
   - Quickstart & Reproducibility: Exact terminal commands to install, run tests, and reproduce local benchmarks.
   - Failure Modes & Boundaries: Explicitly document how the system behaves when dependencies fail or queues saturate.

Constraint: Do not use generic tutorial language or corporate boilerplate. Highlight technical constraints, trade-offs, and empirical results.
