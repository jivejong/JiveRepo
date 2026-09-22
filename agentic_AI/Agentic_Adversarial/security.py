"""Lightweight, session-only access control for the portfolio demo."""

from __future__ import annotations

import hmac
from collections.abc import Mapping

import streamlit as st


DEFAULT_MAX_LLM_CALLS = 20


class UsageLimitReached(RuntimeError):
    """Raised before a non-owner session exceeds its configured LLM quota."""


def _secret_mapping(section: str) -> Mapping:
    value = st.secrets.get(section, {})
    return value if isinstance(value, Mapping) else {}


def _configured_users() -> Mapping:
    users = _secret_mapping("access").get("users", {})
    return users if isinstance(users, Mapping) else {}


def max_llm_calls_per_session() -> int:
    """Return the configured normal-user quota, with a safe documented fallback."""
    configured_limit = _secret_mapping("limits").get(
        "max_llm_calls_per_session", DEFAULT_MAX_LLM_CALLS
    )
    try:
        return max(0, int(configured_limit))
    except (TypeError, ValueError):
        return DEFAULT_MAX_LLM_CALLS


def _match_access_code(access_code: str) -> str | None:
    """Return the matching internal identity without exposing configured users."""
    candidate = access_code.encode("utf-8")
    matched_identity = None
    for identity, configured_code in _configured_users().items():
        configured = str(configured_code).encode("utf-8")
        if hmac.compare_digest(candidate, configured):
            matched_identity = str(identity)
    return matched_identity


def require_access() -> str:
    """Render the password gate and stop unauthenticated visitors."""
    if st.session_state.get("authorized") and st.session_state.get("access_identity"):
        return str(st.session_state["access_identity"])

    st.title("Private Demo")
    with st.form("access_form", clear_on_submit=True):
        access_code = st.text_input("Enter access password:", type="password")
        submitted = st.form_submit_button("Enter")

    if submitted:
        identity = _match_access_code(access_code)
        if identity:
            st.session_state["authorized"] = True
            st.session_state["access_identity"] = identity
            st.session_state["llm_calls"] = 0
            st.session_state["llm_call_in_flight"] = False
            st.rerun()
        st.error("Invalid access code.")

    st.stop()


def check_llm_quota(identity: str) -> None:
    """Block non-owner sessions whose configured quota has been exhausted."""
    if identity == "owner":
        return
    if st.session_state.get("llm_calls", 0) >= max_llm_calls_per_session():
        raise UsageLimitReached("Demo usage limit reached for this session.")


def consume_llm_call(identity: str) -> None:
    """Reserve one non-owner LLM call immediately before the API request."""
    check_llm_quota(identity)
    if identity != "owner":
        st.session_state["llm_calls"] = st.session_state.get("llm_calls", 0) + 1


def render_session_controls(identity: str) -> None:
    """Show quota status and provide a way to return to the password screen."""
    if identity == "owner":
        st.caption("LLM access: Unlimited")
    else:
        maximum = max_llm_calls_per_session()
        remaining = max(0, maximum - st.session_state.get("llm_calls", 0))
        st.caption(f"LLM calls remaining: {remaining} / {maximum}")

    if st.button("End session", key="end_session", use_container_width=True):
        for key in (
            "authorized",
            "access_identity",
            "llm_calls",
            "llm_call_in_flight",
            "last_result",
            "_active_negotiation_events",
        ):
            st.session_state.pop(key, None)
        st.rerun()
