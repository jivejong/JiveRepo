"""Layer 2 of the two-layer behavior model (docs/03): one deterministic quirk
per villain, drawn from character lore.

This is the one place slug-specific behavior is legitimate — signatures are
per-villain *by definition*, and they exist precisely because twelve villains
can't be separated on six stats alone (Riddler/Two-Face sit at stat-distance
18.5). Layer 1 (behavior.py) stays derived; this layer is the fingerprint.

A `Signature` transforms an attempt's request specs (the list of
(method, path) pairs) and carries a couple of flags the session/identity
layers read. `signature_for(slug)` dispatches; an unknown slug gets the
no-op default, so any character in the dataset still runs.

The disambiguating features (docs/02) map directly here:
  riddle_param_count          -> Riddler   (riddle= on every request)
  exact_duplicate_path_pairs  -> Two-Face  (every request issued twice)
  distinct_source_ips         -> Penguin   (IP rotation, via RunIdentity)
  error_ratio (high)          -> Scarecrow (seeks error-producing paths)
  inter_request burst         -> Harley    (erratic bursts vs Joker's steadier)
  absurd HTTP methods         -> Joker
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Absurd methods for Joker — a fixed pool the honeypot logs via its 405
# re-dispatch path (it accepts any method).
_ABSURD_METHODS = ("BREW", "PROPFIND", "WHACK", "MEOW", "SMITE")

# Riddle-ish query values; the exact text doesn't matter, only that the
# `riddle=` parameter is present and countable.
_RIDDLES = ("gnir", "tab", "elzzup", "amgine", "xnihps", "ko`oj")

Spec = tuple[str, str]


def _with_query(path: str, key: str, value: str) -> str:
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}{key}={value}"


@dataclass
class Signature:
    rotate_ip: bool = False  # Penguin: henchmen -> distinct_source_ips
    burstiness: float = 0.0  # Harley: extra timing variance on top of jitter
    # Mister Freeze holds connections open (docs/03): a server-side response
    # delay, delivered via X-Sim-Delay-Ms, so his response_time_ms is high and
    # his sessions run long because each request is slow to return — his
    # duration comes from holding, not from grinding volume like Croc.
    response_delay_ms: float = 0.0
    # Clean operator: stops as soon as the stage machine resolves (win or
    # stall) rather than grinding to its request budget. This is a signature
    # property, not derivable from stats — Ra's al Ghul "then exits", Catwoman
    # is "minimal footprint... clean exit" (docs/03). Everyone else fills their
    # durability/speed-derived budget (Croc grinds, Freeze holds, Joker and
    # Harley persist chaotically through their high durability).
    clean_operator: bool = False

    def transform(self, specs: list[Spec], rng: random.Random) -> list[Spec]:
        """Default: pass the specs through unchanged."""
        return specs


class _Riddler(Signature):
    def transform(self, specs: list[Spec], rng: random.Random) -> list[Spec]:
        # Every request carries a ?riddle= parameter (docs/03).
        return [(m, _with_query(p, "riddle", rng.choice(_RIDDLES))) for m, p in specs]


class _TwoFace(Signature):
    def transform(self, specs: list[Spec], rng: random.Random) -> list[Spec]:
        # Every request issued exactly twice — distinct events (distinct
        # event_id), which dedupe must NOT collapse (docs/03). Drives
        # exact_duplicate_path_pairs.
        doubled: list[Spec] = []
        for spec in specs:
            doubled.append(spec)
            doubled.append(spec)
        return doubled


class _Joker(Signature):
    def transform(self, specs: list[Spec], rng: random.Random) -> list[Spec]:
        # Occasional absurd HTTP methods (docs/03). ~1 in 4 requests.
        out: list[Spec] = []
        for method, path in specs:
            if rng.random() < 0.25:
                out.append((rng.choice(_ABSURD_METHODS), path))
            else:
                out.append((method, path))
        return out


class _Scarecrow(Signature):
    def transform(self, specs: list[Spec], rng: random.Random) -> list[Spec]:
        # Deliberately probes paths that produce errors (docs/03), driving a
        # high 4xx ratio. Adds an error-seeking request alongside each spec.
        out: list[Spec] = []
        for spec in specs:
            out.append(spec)
            out.append(("GET", f"/admin/{rng.randint(1000, 9999)}"))  # 404
        return out


class _Bane(Signature):
    def transform(self, specs: list[Spec], rng: random.Random) -> list[Spec]:
        # Hammers one endpoint (docs/03: "hammers that single endpoint with
        # large repeated bodies"). Collapse every request to the deepest path
        # in the specs — same request count, but a very low path_entropy
        # fingerprint nothing else produces. (Large bodies come from Layer 1
        # strength; volume from durability. The signature is the concentration,
        # so it doesn't need to also inflate count past Croc's grind.)
        if not specs:
            return specs
        method, path = specs[-1]
        return [(method, path) for _ in specs]


def signature_for(slug: str) -> Signature:
    if slug == "558-riddler":
        return _Riddler()
    if slug == "678-two-face":
        return _TwoFace()
    if slug == "370-joker":
        return _Joker()
    if slug == "576-scarecrow":
        return _Scarecrow()
    if slug == "60-bane":
        return _Bane()
    if slug == "514-penguin":
        return Signature(rotate_ip=True)
    if slug == "309-harley-quinn":
        return Signature(burstiness=0.8)
    if slug == "457-mister-freeze":
        return Signature(response_delay_ms=250.0)  # holds connections open
    if slug == "538-ras-al-ghul":
        return Signature(clean_operator=True)  # straight to tier 4, then exits
    if slug == "165-catwoman":
        return Signature(clean_operator=True)  # minimal footprint, clean exit
    return Signature()
