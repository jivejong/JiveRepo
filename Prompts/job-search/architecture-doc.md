Analyze this codebase and generate a formal `ARCHITECTURE.md` file following production-grade software design standards.
Structure the document with the following sections:

1. High-Level Overview: The core domain, design philosophy, and primary responsibilities of this service.
2. Component Boundaries & Ingress/Egress: How data/requests enter the system, which modules own state, and how boundaries are enforced.
3. Concurrency, State & Data Lineage: How asynchronous jobs, threading, distributed state, or persistence are safely managed.
4. Failure Domains & Resilience: Circuit breakers, dead-letter queues, retries, idempotent handling, or graceful degradation paths.
5. Scaling Limits & Trade-Offs: The mathematical or resource ceilings of this current architecture (memory, I/O, network) and what would need to change to scale by 10x.

Base the documentation strictly on the actual code provided. If an edge case is unhandled, explicitly call it out as a known architectural constraint rather than hallucinating features.
