"""The control topic force/control/probe-01 (doc 04): the messages an operator sends to inject a named signature or to force a mode.

    {"inject": "spike", "sector_id": "tatooine", "signature": "sith_presence"}      optional ramp, hold, decay (default 2, 4, 3)
    {"mode": "DISCONNECTED" | "STEALTH" | "CONNECTED", "for_seconds": 2700}          for_seconds optional (until changed)

A message is validated in full before anything happens. Only the broker's ACL keeps anyone else from publishing to the topic.
`veiled_presence` is not injectable in Phase 3 (it needs channels_present < 3; see the OPEN note in doc 03).
"""
import json

from forcesim.signatures import PRODUCIBLE, SUSTAINED_SCANS, InjectionRefused, check_injection

from .modes import FORCEABLE

DEFAULT_RAMP, DEFAULT_HOLD, DEFAULT_DECAY = 2, 4, 3
MAX_SCANS = 96                       # a day of scans is more than any phase of an episode should last
MAX_FORCE_SECONDS = 7 * 86400


class ControlError(ValueError):
    """The message is not acceptable; the text says why (it is logged, never raised into the probe loop)."""


def _int(obj, key, default, low, high):
    value = obj.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ControlError(f"{key} must be an integer from {low} to {high}")
    return value


def parse(payload, sectors):
    """A validated command dict: {"kind": "inject", ...} or {"kind": "mode", ...}. Raises ControlError."""
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        raise ControlError("not JSON") from None
    if not isinstance(obj, dict):
        raise ControlError("not a JSON object")
    if ("inject" in obj) == ("mode" in obj):
        raise ControlError("exactly one of inject or mode")
    if "inject" in obj:
        unknown = set(obj) - {"inject", "sector_id", "signature", "ramp", "hold", "decay"}
        if unknown:
            raise ControlError(f"unknown keys {sorted(unknown)}")
        if obj["inject"] != "spike":
            raise ControlError('inject must be "spike"')
        by_id = {s.sector_id: s for s in sectors}
        sector = by_id.get(obj.get("sector_id"))
        if sector is None:
            raise ControlError(f"sector_id {obj.get('sector_id')!r} is not a sector")
        signature = obj.get("signature")
        if signature not in PRODUCIBLE:
            raise ControlError(f"signature must be one of {list(PRODUCIBLE)}")
        try:
            check_injection(sector, signature)
        except InjectionRefused as e:
            raise ControlError(str(e)) from None
        return {"kind": "inject", "sector_id": sector.sector_id, "signature": signature,
                "ramp": _int(obj, "ramp", DEFAULT_RAMP, 1, MAX_SCANS),
                "hold": _int(obj, "hold", DEFAULT_HOLD, SUSTAINED_SCANS, MAX_SCANS),
                "decay": _int(obj, "decay", DEFAULT_DECAY, 1, MAX_SCANS)}
    unknown = set(obj) - {"mode", "for_seconds"}
    if unknown:
        raise ControlError(f"unknown keys {sorted(unknown)}")
    if obj["mode"] not in FORCEABLE:
        raise ControlError(f"mode must be one of {list(FORCEABLE)}")
    seconds = _int(obj, "for_seconds", None, 1, MAX_FORCE_SECONDS) if "for_seconds" in obj else None
    return {"kind": "mode", "mode": obj["mode"], "for_seconds": seconds}
