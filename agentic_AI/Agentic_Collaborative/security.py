"""Lightweight password access and per-session Gemini usage controls."""

import hmac

import streamlit as st


DEFAULT_MAX_LLM_CALLS = 20


class UsageLimitReached(RuntimeError):
    """Raised before a non-owner session exceeds its configured Gemini quota."""


def _configured_users() -> list[tuple[str, str]]:
    """Return valid internal identities and access codes without exposing them."""
    access = st.secrets.get("access", {})
    users = access.get("users", {}) if hasattr(access, "get") else {}
    if not hasattr(users, "items"):
        return []

    return [
        (identity.strip(), password)
        for identity, password in users.items()
        if isinstance(identity, str) and identity.strip() and isinstance(password, str) and password
    ]


def max_llm_calls_per_session() -> int:
    """Read the configurable non-owner quota, with a safe demo fallback."""
    limits = st.secrets.get("limits", {})
    configured_limit = (
        limits.get("max_llm_calls_per_session", DEFAULT_MAX_LLM_CALLS)
        if hasattr(limits, "get")
        else DEFAULT_MAX_LLM_CALLS
    )
    try:
        return max(0, int(configured_limit))
    except (TypeError, ValueError):
        return DEFAULT_MAX_LLM_CALLS


def require_access() -> str:
    """Render the access-code gate and stop unauthenticated sessions."""
    identity = st.session_state.get("access_identity")
    if st.session_state.get("authorized") and isinstance(identity, str):
        st.session_state.setdefault("llm_calls", 0)
        st.session_state.setdefault("llm_call_in_flight", False)
        return identity

    st.title("Private Demo")
    st.caption("Enter access password:")
    with st.form("access_form", clear_on_submit=True):
        access_code = st.text_input("Access password", type="password")
        submitted = st.form_submit_button("Enter")

    if submitted:
        matched_identity = None
        for configured_identity, configured_code in _configured_users():
            # Check every configured code to avoid revealing which identity matched.
            is_match = hmac.compare_digest(access_code, configured_code)
            if is_match and matched_identity is None:
                matched_identity = configured_identity

        if matched_identity is None:
            st.error("Invalid access code.")
        else:
            st.session_state["authorized"] = True
            st.session_state["access_identity"] = matched_identity
            st.session_state["llm_calls"] = 0
            st.session_state["llm_call_in_flight"] = False
            st.rerun()

    st.stop()


def consume_llm_call(identity: str) -> None:
    """Consume one Gemini inference call, unless the authenticated user is owner."""
    if identity == "owner":
        return

    calls_used = int(st.session_state.get("llm_calls", 0))
    if calls_used >= max_llm_calls_per_session():
        raise UsageLimitReached("Demo usage limit reached for this session.")

    st.session_state["llm_calls"] = calls_used + 1


def remaining_llm_calls(identity: str) -> int | None:
    """Return remaining non-owner calls; owners have no session quota."""
    if identity == "owner":
        return None
    return max(0, max_llm_calls_per_session() - int(st.session_state.get("llm_calls", 0)))


def end_session() -> None:
    """Remove access and quota state without touching global resources."""
    for key in ("authorized", "access_identity", "llm_calls", "llm_call_in_flight"):
        st.session_state.pop(key, None)
