"""Lightweight password access and per-session Gemini quota controls."""

from __future__ import annotations

from contextlib import contextmanager
import hmac
from typing import Iterator

import streamlit as st


DEFAULT_MAX_LLM_CALLS = 20


def _configured_users() -> dict[str, str]:
    """Read valid internal identities and access codes without exposing them."""
    access = st.secrets.get("access", {})
    users = access.get("users", {}) if hasattr(access, "get") else {}
    if not hasattr(users, "items"):
        return {}
    return {
        str(identity): str(password)
        for identity, password in users.items()
        if str(identity) and str(password)
    }


def max_llm_calls() -> int:
    """Read the configurable normal-user quota, with a safe demo fallback."""
    limits = st.secrets.get("limits", {})
    configured_limit = limits.get("max_llm_calls_per_session", DEFAULT_MAX_LLM_CALLS)
    try:
        return max(0, int(configured_limit))
    except (TypeError, ValueError):
        return DEFAULT_MAX_LLM_CALLS


def _match_access_code(access_code: str) -> str | None:
    """Find an identity with constant-time comparisons and no early return."""
    candidate = access_code.encode("utf-8")
    matched_identity = None
    for identity, configured_password in _configured_users().items():
        is_match = hmac.compare_digest(candidate, configured_password.encode("utf-8"))
        if is_match and matched_identity is None:
            matched_identity = identity
    return matched_identity


def require_access() -> str:
    """Render the password gate and stop unauthenticated app execution."""
    if st.session_state.get("authorized") and st.session_state.get("access_identity"):
        st.session_state.setdefault("llm_calls", 0)
        st.session_state.setdefault("llm_call_in_flight", False)
        return st.session_state.access_identity

    st.title("Private Demo")
    with st.form("access_form", clear_on_submit=True):
        access_code = st.text_input("Enter access password:", type="password")
        submitted = st.form_submit_button("Enter")

    if submitted:
        identity = _match_access_code(access_code)
        if identity is not None:
            st.session_state.authorized = True
            st.session_state.access_identity = identity
            st.session_state.llm_calls = 0
            st.session_state.llm_call_in_flight = False
            st.rerun()
        st.error("Invalid access code.")

    st.stop()


def is_owner(identity: str | None = None) -> bool:
    return (identity or st.session_state.get("access_identity")) == "owner"


def check_llm_quota(identity: str | None = None) -> bool:
    """Return whether this session may make another Gemini request."""
    if is_owner(identity):
        return True
    return st.session_state.get("llm_calls", 0) < max_llm_calls()


@contextmanager
def consume_llm_call() -> Iterator[None]:
    """Atomically reserve one non-owner Gemini call around a real request."""
    identity = st.session_state.get("access_identity")
    if not st.session_state.get("authorized") or not identity:
        st.error("Access is required before using the model.")
        st.stop()
    if st.session_state.get("llm_call_in_flight"):
        st.warning("A model request is already in progress.")
        st.stop()
    if not check_llm_quota(identity):
        st.error("Demo usage limit reached for this session.")
        st.stop()

    if not is_owner(identity):
        st.session_state.llm_calls = st.session_state.get("llm_calls", 0) + 1
    st.session_state.llm_call_in_flight = True
    try:
        yield
    finally:
        st.session_state.llm_call_in_flight = False


def render_access_status() -> None:
    """Render the small sidebar indicator without revealing an identity."""
    if is_owner():
        st.caption("LLM access: Unlimited")
    else:
        limit = max_llm_calls()
        used = st.session_state.get("llm_calls", 0)
        st.caption(f"LLM calls remaining: {max(0, limit - used)} / {limit}")
    st.button("Lock app", key="lock_app", on_click=lock_session, use_container_width=True)


def lock_session() -> None:
    """Remove authorization and generated session data, then show the gate."""
    st.session_state.clear()
