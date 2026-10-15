"""Signature names, the signature -> specialty map and the evidence kind of an incident.

Implements doc 03 "Signature classification" (the matched-specialty column) and doc 06 "get_disturbance_context"
(evidence_kind). This is a copy of doc 03's table: tests/test_signatures.py checks it against the doc and against
edge/forcesim/signatures.py. The agent does not import from edge.
"""

SIGNATURES = (
    "sith_presence",
    "dark_adept",
    "nexus_awakening",
    "force_drain",
    "kyber_cache",
    "civil_unrest",
    "veiled_presence",
    "unclassified",
)

# "unclassified" is deliberately absent: it has no specialty rule (doc 06, "Constraint layer").
SPECIALTY = {
    "sith_presence": "combat",
    "dark_adept": "combat",
    "nexus_awakening": "investigation",
    "force_drain": "investigation",
    "kyber_cache": "diplomacy",
    "civil_unrest": "diplomacy",
    "veiled_presence": "stealth",
}


def evidence_kind(is_report_sourced, report_description):
    """"report" for a report-sourced incident, "probe_with_report" for a probe incident a report escalated, else "probe"."""
    if is_report_sourced:
        return "report"
    if report_description is not None:
        return "probe_with_report"
    return "probe"
