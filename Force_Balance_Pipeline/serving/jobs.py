"""Job 1's latest successful run, for the publish gate (doc 05 "Serving (Phase 7)": "Gate").

The lookup lives in agent.jobs, shared with the agent's poll gate; this module re-exports it so the gate's callers and tests keep their import.
"""
from agent.jobs import JobsApiError, latest_successful_run  # noqa: F401
