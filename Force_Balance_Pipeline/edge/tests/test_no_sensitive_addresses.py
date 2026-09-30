"""Guards against LAN addresses (RFC 1918), and, once local literals are provided, a real hostname or machine ID,
leaking into any git-tracked file under Force_Balance_Pipeline/. RFC 1918 addresses are pattern-matched, so this check
runs unconditionally; the placeholder tokens (`<DESKTOP_IP>`, `<PI_IP>`, ...) never match a dotted-quad and need no
special-casing. This module's own PatternTests fixtures are deliberately RFC1918-shaped example addresses (to prove the
matcher works), so this file exempts itself, by exact path, from its own repo-wide scan -- see AddressGuardTests below.

Hostname/machine-id literals are read from an untracked, per-developer file
(`~/.force_balance_pipeline/sensitive-strings.txt`) rather than hard-coded here, since a real one must never appear in a
committed test either. Format: one literal per line, blank lines and lines starting with `#` ignored, for example:

    # ~/.force_balance_pipeline/sensitive-strings.txt
    <the-pi-hostname>
    <the-pi-machine-id>

If that file is absent, that half of the guard is skipped with a visible reason, not silently passed."""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELF_PATH = Path(__file__).resolve()
SENSITIVE_STRINGS_FILE = Path.home() / ".force_balance_pipeline" / "sensitive-strings.txt"

# RFC 1918 private ranges: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16.
RFC1918_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})\b"
)

# Docker's own default bridge gateway is a fixed, non-configurable address, not a LAN address of ours -- the repo's own
# docs cite it as evidence that Docker's port relay masks every real client address behind it (docs/05, docs/PHASE3-RESULTS,
# edge/tests/test_infra_phase3.py). Allowed by exact value only, not by range, so a different 172.17.x address still flags.
ALLOWED_ADDRESSES = frozenset({"172.17.0.1"})


def find_addresses(text):
    """Every RFC 1918 dotted-quad match in `text`, excluding ALLOWED_ADDRESSES (empty if none)."""
    return [a for a in RFC1918_RE.findall(text) if a not in ALLOWED_ADDRESSES]


def find_literals(text, literals):
    """Every literal from `literals` that appears as a substring of `text`."""
    return [l for l in literals if l in text]


def tracked_text_files():
    """(path, text) for every git-tracked file under Force_Balance_Pipeline/ that can be read as text; a binary file
    (an image, a frozen snapshot's non-text asset) is skipped, not scanned."""
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    for rel in out.stdout.splitlines():
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue


class PatternTests(unittest.TestCase):
    """Direct tests of the matching logic, independent of the repo walk below."""

    def test_rfc1918_ranges_match(self):
        for address in ("10.0.0.1", "10.255.255.255", "172.16.0.1", "172.31.255.255", "192.168.1.42",
                        "192.168.255.255"):
            with self.subTest(address=address):
                self.assertEqual(find_addresses(f"see {address} here"), [address])

    def test_adjacent_non_rfc1918_ranges_do_not_match(self):
        for address in ("172.15.0.1", "172.32.0.1", "11.0.0.1", "192.169.0.1", "8.8.8.8"):
            with self.subTest(address=address):
                self.assertEqual(find_addresses(f"see {address} here"), [])

    def test_placeholders_never_match(self):
        self.assertEqual(find_addresses("bound to <DESKTOP_IP> and reachable from <PI_IP>"), [])

    def test_dockers_own_bridge_gateway_is_allowed_by_exact_value_only(self):
        self.assertEqual(find_addresses("every client shows as 172.17.0.1"), [])
        self.assertEqual(find_addresses("a different container gateway 172.17.0.2"), ["172.17.0.2"])

    def test_a_known_literal_is_found_as_a_substring(self):
        self.assertEqual(find_literals("host example-host-01 said hello", ["example-host-01", "other"]),
                         ["example-host-01"])

    def test_no_known_literal_present_finds_nothing(self):
        self.assertEqual(find_literals("nothing sensitive here", ["example-host-01"]), [])


class AddressGuardTests(unittest.TestCase):
    def test_no_rfc1918_address_in_a_tracked_file(self):
        offenders = []
        for path, text in tracked_text_files():
            if path.resolve() == SELF_PATH:
                continue  # this file's own PatternTests fixtures are deliberately RFC1918-shaped, not a leak
            for address in find_addresses(text):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {address}")
        self.assertEqual(offenders, [])

    def test_the_self_exemption_is_this_file_only_not_a_blanket_pass(self):
        """Proves the skip above is doing real work: read directly (bypassing the skip), this file's own fixtures
        do contain RFC1918 addresses, so the exemption is by exact path, not because nothing would ever match."""
        self.assertTrue(find_addresses(SELF_PATH.read_text(encoding="utf-8")),
                        "expected this file's own PatternTests fixtures to contain RFC1918 addresses")


class HostnameAndMachineIdGuardTests(unittest.TestCase):
    def setUp(self):
        if not SENSITIVE_STRINGS_FILE.exists():
            self.skipTest(f"{SENSITIVE_STRINGS_FILE} not found -- hostname/machine-id literals are not known to "
                          "this run, so this check is skipped. See docs/05-platform-setup.md for the file format.")
        self.literals = [l.strip() for l in SENSITIVE_STRINGS_FILE.read_text(encoding="utf-8").splitlines()
                         if l.strip() and not l.strip().startswith("#")]
        if not self.literals:
            self.skipTest(f"{SENSITIVE_STRINGS_FILE} exists but lists no literals")

    def test_no_known_literal_in_a_tracked_file(self):
        offenders = []
        for path, text in tracked_text_files():
            for literal in find_literals(text, self.literals):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {literal}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
