"""ULIDs for gold.deployment.deployment_id (doc 03 "gold.deployment": deployment_id is a ULID).

Same algorithm as intake/intake_core.new_ulid: a 48-bit millisecond timestamp and 80 random bits as 26 Crockford base32 characters.
"""
import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(now_ms, rand):
    """A ULID from a millisecond timestamp and exactly 10 random bytes."""
    if len(rand) != 10:
        raise ValueError("rand must be 10 bytes")
    if not 0 <= now_ms < (1 << 48):
        raise ValueError("now_ms must fit in 48 bits")
    value = (now_ms << 80) | int.from_bytes(rand, "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def ulid_now():
    return new_ulid(int(time.time() * 1000), os.urandom(10))
