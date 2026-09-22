"""Session access control and billable-operation limits for the demo app."""

import hmac
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Iterator, Optional, Sequence, Tuple

import streamlit as st


DEFAULT_MAX_BILLABLE_OPERATIONS_PER_SESSION = 20
OWNER_IDENTITY = "owner"

_SESSION_KEYS_TO_CLEAR = (
    "authorized",
    "access_identity",
    "api_call_in_flight",
    "debate_in_flight",
    "last_debate",
)


def _configured_users() -> Optional[Sequence[Tuple[str, str]]]:
    """Load and validate named access codes without exposing their values."""
    try:
        users = st.secrets["access"]["users"]
    except Exception:
        return None

    if not isinstance(users, Mapping) or not users:
        return None

    entries = []
    seen_codes = set()
    for identity, code in users.items():
        if not isinstance(identity, str) or not identity.strip():
            return None
        if not isinstance(code, str) or not code:
            return None
        if code in seen_codes:
            return None
        seen_codes.add(code)
        entries.append((identity, code))

    return entries


def _matching_identity(
    submitted_code: str, configured_users: Sequence[Tuple[str, str]]
) -> Optional[str]:
    """Compare against every configured code to avoid an early-exit timing leak."""
    matched_identity = None
    submitted_bytes = submitted_code.encode("utf-8")
    for identity, expected_code in configured_users:
        if hmac.compare_digest(submitted_bytes, expected_code.encode("utf-8")):
            matched_identity = identity
    return matched_identity


def require_access() -> str:
    """Require a valid access code before protected application code can run."""
    configured_users = _configured_users()
    if configured_users is None:
        st.error("Access control is unavailable. Please contact the app owner.")
        st.stop()

    configured_identities = {identity for identity, _ in configured_users}
    identity = st.session_state.get("access_identity")
    if (
        st.session_state.get("authorized") is True
        and isinstance(identity, str)
        and identity in configured_identities
    ):
        return identity

    st.session_state.pop("authorized", None)
    st.session_state.pop("access_identity", None)

    st.title("Private Demo")
    st.write("This application is restricted.")

    with st.form("access_code_form", clear_on_submit=True):
        submitted_code = st.text_input("Access code", type="password")
        submitted = st.form_submit_button("Enter", type="primary")

    if submitted:
        matched_identity = _matching_identity(submitted_code, configured_users)
        if matched_identity is None:
            st.error("Invalid access code.")
            st.stop()

        st.session_state["authorized"] = True
        st.session_state["access_identity"] = matched_identity
        st.session_state.setdefault("billable_operations", 0)
        st.session_state["api_call_in_flight"] = False
        st.rerun()

    st.stop()


def get_billable_limit() -> int:
    """Return the configured per-session limit, using the safe default if absent."""
    try:
        configured_limit = st.secrets.get("limits", {}).get(
            "max_billable_operations_per_session",
            DEFAULT_MAX_BILLABLE_OPERATIONS_PER_SESSION,
        )
    except Exception:
        configured_limit = DEFAULT_MAX_BILLABLE_OPERATIONS_PER_SESSION

    if isinstance(configured_limit, bool) or not isinstance(configured_limit, int):
        st.error("Usage limits are configured incorrectly. Please contact the app owner.")
        st.stop()
    if configured_limit < 0:
        st.error("Usage limits are configured incorrectly. Please contact the app owner.")
        st.stop()

    return configured_limit


def get_billable_usage() -> int:
    """Return validated usage for the current Streamlit session."""
    usage = st.session_state.get("billable_operations", 0)
    if isinstance(usage, bool) or not isinstance(usage, int) or usage < 0:
        st.error("Usage tracking is unavailable. Please restart the app session.")
        st.stop()
    return usage


def ensure_billable_capacity(required_operations: int = 1) -> None:
    """Block a workflow before it starts when the remaining quota is insufficient."""
    configured_users = _configured_users()
    configured_identities = (
        {identity for identity, _ in configured_users}
        if configured_users is not None
        else set()
    )
    if (
        st.session_state.get("authorized") is not True
        or st.session_state.get("access_identity") not in configured_identities
    ):
        st.error("Session authorization expired. Please lock the app and sign in again.")
        st.stop()
    if (
        isinstance(required_operations, bool)
        or not isinstance(required_operations, int)
        or required_operations < 1
    ):
        raise ValueError("required_operations must be a positive integer")

    if st.session_state.get("access_identity") == OWNER_IDENTITY:
        return

    if get_billable_usage() + required_operations > get_billable_limit():
        st.error("Demo usage limit reached for this session.")
        st.stop()


@contextmanager
def billable_operation() -> Iterator[None]:
    """Reserve one operation before a paid request and prevent overlapping calls."""
    if st.session_state.get("api_call_in_flight") is True:
        st.warning("A request is already running.")
        st.stop()

    ensure_billable_capacity()
    st.session_state["billable_operations"] = get_billable_usage() + 1
    st.session_state["api_call_in_flight"] = True
    try:
        yield
    finally:
        st.session_state["api_call_in_flight"] = False


def render_access_controls() -> None:
    """Show quota status and a control that clears all protected session data."""
    with st.sidebar:
        usage = get_billable_usage()
        if st.session_state.get("access_identity") == OWNER_IDENTITY:
            st.caption(f"API calls this session: {usage} / Unlimited")
        else:
            st.caption(f"API calls this session: {usage} / {get_billable_limit()}")
        if st.button("Lock app"):
            for key in _SESSION_KEYS_TO_CLEAR:
                st.session_state.pop(key, None)
            st.rerun()
