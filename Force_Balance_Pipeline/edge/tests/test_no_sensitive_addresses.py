"""Guards against LAN addresses (RFC 1918), and, once a local list is provided, a real machine ID, leaking into any
git-tracked file under Force_Balance_Pipeline/. RFC 1918 addresses are pattern-matched, so this check runs
unconditionally; the placeholder tokens (`<DESKTOP_IP>`, `<PI_IP>`, ...) never match a dotted-quad and need no
special-casing. This module's own PatternTests/ScanTests fixtures are deliberately RFC1918-shaped example addresses (to
prove the matcher works), so the RFC 1918 check exempts this file, by exact path, from its own repo-wide scan --
AddressGuardTests below. The machine-id check is never self-exempt: no fixture value should legitimately collide with a
real machine ID, so if one ever did, that would be worth seeing.

Machine-id literals are read from an untracked, per-developer file (`~/.force_balance_pipeline/sensitive-strings.txt`)
rather than hard-coded here, since a real one must never appear in a committed test either. Format: one literal per
line, blank lines and lines starting with `#` ignored, for example:

    # ~/.force_balance_pipeline/sensitive-strings.txt
    <the-pi-machine-id>

If that file is absent, this check is skipped with a visible reason, not silently passed. (Hostname is not treated as
sensitive -- decided explicitly, not an oversight.)"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


def literal_matcher(forbidden):
    """A matcher (see scan()) that finds every string in `forbidden` present as a substring of the text."""
    def _match(text):
        return [f for f in forbidden if f in text]
    return _match


def scan(files, matcher, exempt_self=False):
    """Run `matcher(text) -> [matches]` over every (path, text) pair in `files` (any iterable, real or synthetic).
    Returns [(path, match), ...], empty if clean. When exempt_self is True, SELF_PATH (this module's own file) is
    skipped before matching -- only the RFC 1918 check uses this; the machine-id check never does, see the module
    docstring."""
    offenders = []
    for path, text in files:
        if exempt_self and path.resolve() == SELF_PATH:
            continue
        for match in matcher(text):
            offenders.append((path, match))
    return offenders


def rfc1918_offenders(files):
    """RFC 1918 addresses in `files`. Self-exempt: this module's own PatternTests/ScanTests/OffenderFunctionTests
    fixtures are deliberately RFC1918-shaped example addresses, not a leak. What AddressGuardTests actually calls."""
    return scan(files, find_addresses, exempt_self=True)


def machine_id_offenders(files, strings):
    """Every string in `strings` found in `files`. Never self-exempt: no fixture value should legitimately collide
    with a real machine ID, so if one ever did in this file, that would be worth seeing. What MachineIdGuardTests
    actually calls."""
    return scan(files, literal_matcher(strings), exempt_self=False)


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
    """Direct tests of the matching logic, independent of scan() and the repo walk below."""

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
        matcher = literal_matcher(["fake-machine-id-01", "other"])
        self.assertEqual(matcher("host reported fake-machine-id-01 said hello"), ["fake-machine-id-01"])

    def test_no_known_literal_present_finds_nothing(self):
        self.assertEqual(literal_matcher(["fake-machine-id-01"])("nothing sensitive here"), [])


class ScanTests(unittest.TestCase):
    """scan() itself, using temp files and fake values only -- never the real repo tree or a real sensitive value."""

    def test_a_forbidden_string_in_a_normal_file_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "some_doc.md"
            path.write_text("mentions fake-machine-id-01 here", encoding="utf-8")
            files = [(path, path.read_text(encoding="utf-8"))]
            self.assertEqual(scan(files, literal_matcher(["fake-machine-id-01"])), [(path, "fake-machine-id-01")])

    def test_the_same_string_in_the_guards_own_file_is_still_caught(self):
        """The literal/machine-id check is never self-exempt (module docstring): a forbidden literal appearing in
        this very file's text would still be flagged."""
        files = [(SELF_PATH, "mentions fake-machine-id-01 here")]
        self.assertEqual(scan(files, literal_matcher(["fake-machine-id-01"]), exempt_self=False),
                         [(SELF_PATH, "fake-machine-id-01")])

    def test_an_rfc1918_fixture_in_the_guards_own_file_is_exempt(self):
        """The RFC 1918 check IS self-exempt (AddressGuardTests): the same synthetic text, scanned with
        exempt_self=True, produces nothing; scanned with exempt_self=False, it is caught -- proving the exemption
        is doing real work, not that the address just doesn't match."""
        files = [(SELF_PATH, "example address 10.1.2.3 here")]
        self.assertEqual(scan(files, find_addresses, exempt_self=True), [])
        self.assertEqual(scan(files, find_addresses, exempt_self=False), [(SELF_PATH, "10.1.2.3")])


class OffenderFunctionTests(unittest.TestCase):
    """rfc1918_offenders() and machine_id_offenders() -- what AddressGuardTests and MachineIdGuardTests actually
    call, as opposed to scan() with parameters chosen by the test (ScanTests above) -- tested directly, with temp
    files plus this module's own path in the file list, fake values only."""

    def test_rfc1918_offenders_skips_this_files_own_path_but_not_others(self):
        with tempfile.TemporaryDirectory() as d:
            other = Path(d) / "some_doc.md"
            other.write_text("a fake address 10.1.2.3 here", encoding="utf-8")
            files = [(SELF_PATH, "example address 10.1.2.3 here"), (other, other.read_text(encoding="utf-8"))]
            offenders = rfc1918_offenders(files)
        self.assertEqual(offenders, [(other, "10.1.2.3")])

    def test_machine_id_offenders_never_exempts_this_file(self):
        """A fake machine id can't be written into a real copy at SELF_PATH's own name, so this asserts on the call
        machine_id_offenders makes to scan() instead: it must always pass exempt_self=False."""
        with mock.patch(f"{__name__}.scan", wraps=scan) as spy:
            machine_id_offenders([(SELF_PATH, "irrelevant text")], ["fake-machine-id-01"])
        spy.assert_called_once()
        self.assertEqual(spy.call_args.kwargs.get("exempt_self"), False)


class AddressGuardTests(unittest.TestCase):
    def test_no_rfc1918_address_in_a_tracked_file(self):
        offenders = rfc1918_offenders(tracked_text_files())
        self.assertEqual(offenders, [], [f"{p.relative_to(ROOT).as_posix()}: {a}" for p, a in offenders])


class MachineIdGuardTests(unittest.TestCase):
    def setUp(self):
        if not SENSITIVE_STRINGS_FILE.exists():
            self.skipTest(f"{SENSITIVE_STRINGS_FILE} not found -- no machine-id literals are known to this run, "
                          "so this check is skipped. See this module's docstring for the file format.")
        self.literals = [l.strip() for l in SENSITIVE_STRINGS_FILE.read_text(encoding="utf-8").splitlines()
                         if l.strip() and not l.strip().startswith("#")]
        if not self.literals:
            self.skipTest(f"{SENSITIVE_STRINGS_FILE} exists but lists no literals")

    def test_no_known_machine_id_in_a_tracked_file(self):
        offenders = machine_id_offenders(tracked_text_files(), self.literals)
        self.assertEqual(offenders, [], [f"{p.relative_to(ROOT).as_posix()}: {m}" for p, m in offenders])


if __name__ == "__main__":
    unittest.main()
