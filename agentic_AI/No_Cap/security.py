"""Small, session-scoped access and LLM-usage helpers for the demo."""

from __future__ import annotations

import hmac
from collections.abc import Mapping

import streamlit as st


DEFAULT_MAX_LLM_CALLS = 20
OWNER_IDENTITY = "owner"


class UsageLimitReached(Exception):
    """Raised before a non-owner would exceed the session LLM allowance."""


def _mapping(value: object) -> Mapping[str, object]:
    """Return a secrets section as a mapping without exposing configuration."""
    return value if isinstance(value, Mapping) else {}


def _configured_users() -> Mapping[str, object]:
    access = _mapping(st.secrets.get("access", {}))
    return _mapping(access.get("users", {}))


def _matching_identity(access_code: str) -> str | None:
    """Return the identity for an access code, comparing every configured code."""
    submitted = access_code.encode("utf-8")
    matched_identity: str | None = None

    for identity, configured_code in _configured_users().items():
        if not isinstance(identity, str) or not isinstance(configured_code, str):
            continue
        is_match = hmac.compare_digest(submitted, configured_code.encode("utf-8"))
        if is_match and matched_identity is None:
            matched_identity = identity

    return matched_identity


def max_llm_calls_per_session() -> int:
    """Read the non-owner session limit, falling back safely to 20 calls."""
    limits = _mapping(st.secrets.get("limits", {}))
    try:
        return max(0, int(limits.get("max_llm_calls_per_session", DEFAULT_MAX_LLM_CALLS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_LLM_CALLS


def require_access() -> str:
    """Render the password gate and stop execution until the session is authorized."""
    identity = st.session_state.get("access_identity")
    if st.session_state.get("authorized") is True and isinstance(identity, str):
        st.session_state.setdefault("llm_calls", 0)
        st.session_state.setdefault("llm_call_in_flight", False)
        return identity

    st.title("Private Demo")
    with st.form("access_form", clear_on_submit=True):
        access_code = st.text_input("Enter access password", type="password")
        submitted = st.form_submit_button("Enter")

    if submitted:
        identity = _matching_identity(access_code)
        if identity is not None:
            st.session_state["authorized"] = True
            st.session_state["access_identity"] = identity
            st.session_state["llm_calls"] = 0
            st.session_state["llm_call_in_flight"] = False
            st.rerun()
        else:
            st.error("Invalid access code.")

    st.stop()


def is_owner(identity: str) -> bool:
    return identity == OWNER_IDENTITY


def consume_llm_call(identity: str) -> None:
    """Reserve one actual inference call, unless the authorized user is owner."""
    if is_owner(identity):
        return

    used_calls = int(st.session_state.get("llm_calls", 0))
    if used_calls >= max_llm_calls_per_session():
        raise UsageLimitReached
    st.session_state["llm_calls"] = used_calls + 1


def end_session() -> None:
    """Remove authorization and user-generated state without touching cached resources."""
    for key in (
        "authorized",
        "access_identity",
        "llm_calls",
        "llm_call_in_flight",
        "result",
        "slang_term",
        "trigger_search",
    ):
        st.session_state.pop(key, None)
    st.query_params.clear()
