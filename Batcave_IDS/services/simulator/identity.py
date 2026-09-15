"""Per-run network identity for the simulator: a source IP and user agent
each run presents to the honeypot.

Two problems this solves, one mechanism (per docs/06 Phase 3):

- **Back-to-back runs must not merge.** Distinct runs get distinct source
  IPs, so even before the session cookie carries identity, their traffic is
  visibly different at the sensor. (The cookie is what actually keeps them
  separate now; distinct IPs keep the IP *features* meaningful.)
- **Penguin's henchmen and high-INT UA rotation.** The same identity object
  can rotate IP or UA per request within one run, which the session cookie
  lets stay a single session while `distinct_source_ips` /
  `distinct_user_agents` still count the rotation.

Source IPs come from RFC 5737 TEST-NET-1 (192.0.2.0/24), reserved for
documentation and unmistakably synthetic — no risk of a real address ending
up in the corpus. Delivered via `X-Forwarded-For`, which the honeypot honors
only under HONEYPOT_TRUST_FORWARDED_FOR (on inside compose, off for manual
tests).

Rotation is opt-in per field: Phase 3 wires Penguin to rotate IP and the
high-intelligence villains to rotate UA (signatures.py). Everyone else holds
a fixed per-run identity.
"""

from __future__ import annotations

import random

# A small, recognizable pool. Real UA rotation would draw from a larger set;
# these are enough to drive distinct_user_agents without pretending to be a
# full fingerprint corpus.
_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/121.0",
    "curl/8.5.0",
    "python-requests/2.31.0",
    "Go-http-client/2.0",
)


def _random_test_net_ip(rng: random.Random) -> str:
    # 192.0.2.1 .. 192.0.2.254 — skip .0 (network) and .255 (broadcast).
    return f"192.0.2.{rng.randint(1, 254)}"


class RunIdentity:
    def __init__(
        self,
        rng: random.Random,
        rotate_ip: bool = False,
        rotate_user_agent: bool = False,
    ) -> None:
        self._rng = rng
        self._rotate_ip = rotate_ip
        self._rotate_user_agent = rotate_user_agent
        self._base_ip = _random_test_net_ip(rng)
        self._base_ua = rng.choice(_USER_AGENTS)

    def headers(self) -> dict[str, str]:
        """Identity headers for one request. Fixed per run unless rotation is
        enabled for that field, in which case a fresh value is drawn each call."""
        ip = _random_test_net_ip(self._rng) if self._rotate_ip else self._base_ip
        ua = self._rng.choice(_USER_AGENTS) if self._rotate_user_agent else self._base_ua
        return {"X-Forwarded-For": ip, "User-Agent": ua}
