"""One outbound HTTP path (Phase 7 review finding 8, Phase 8 Round 10): ingest/bridge/files_api.py is the only module that may name urlopen.

Every other caller goes through files_api.http_request or files_api.default_opener, which add the project User-Agent and the project TLS
context (ssl_context(), with VERIFY_X509_PARTIAL_CHAIN). The scan reads source text with the tokenizer, so comments and strings (docstrings)
never count, and it flags both a call, urlopen(, and a bare reference such as opener=urllib.request.urlopen. tests/ directories and the
virtual environments are not scanned. Run from Force_Balance_Pipeline:
    edge\\.venv\\Scripts\\python.exe -m unittest discover -s scripts/tests
"""
import io
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("agent", "serving", "ingest", "intake", "edge", "scripts")
SKIPPED_DIRS = {"tests", ".venv", "venv", "node_modules", "__pycache__", ".demo"}
ALLOWED = {"ingest/bridge/files_api.py"}
# Standalone setup tools, run by hand in Phases 0 to 2 (a platform check, the SWAPI snapshot, a Gemini helper). They are not on the
# pipeline's runtime path and were outside Round 10's edit list, so they are listed rather than changed; each entry must still use urlopen
# (test_the_exceptions_are_still_needed), so the list can only shrink.
KNOWN_EXCEPTIONS = {"scripts/check_platform.py", "scripts/fetch_swapi.py", "scripts/gemini_client.py"}


def python_files(root=ROOT):
    for folder in SCANNED:
        base = Path(root) / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root)
            if SKIPPED_DIRS.intersection(relative.parts[:-1]):
                continue
            yield path, relative.as_posix()


def mentions_urlopen(source):
    """True when the code (not a comment or a string) has the name urlopen."""
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string == "urlopen":
            return True
    return False


def offenders(root=ROOT):
    found = []
    for path, relative in python_files(root):
        if relative in ALLOWED or relative in KNOWN_EXCEPTIONS:
            continue
        if mentions_urlopen(path.read_text(encoding="utf-8")):
            found.append(relative)
    return found


class HttpPath(unittest.TestCase):
    def test_the_scan_finds_the_sources(self):
        files = [relative for _, relative in python_files()]
        for expected in ("agent/ddl.py", "agent/eval.py", "serving/deploy.py", "ingest/bridge/files_api.py", "scripts/demo.py"):
            self.assertIn(expected, files)
        self.assertGreater(len(files), 40)
        self.assertFalse([f for f in files if "/tests/" in f or ".venv" in f])

    def test_only_files_api_names_urlopen(self):
        self.assertEqual(offenders(), [])

    def test_files_api_itself_still_does(self):
        self.assertTrue(mentions_urlopen((ROOT / "ingest" / "bridge" / "files_api.py").read_text(encoding="utf-8")))

    def test_the_exceptions_are_still_needed(self):
        for relative in KNOWN_EXCEPTIONS:
            self.assertTrue(mentions_urlopen((ROOT / relative).read_text(encoding="utf-8")), f"{relative} no longer uses urlopen: remove it from KNOWN_EXCEPTIONS")

    def test_the_tokenizer_ignores_comments_and_strings_but_not_calls_or_references(self):
        self.assertFalse(mentions_urlopen("# urlopen(x)\n"))
        self.assertFalse(mentions_urlopen('"""urlopen("x") in a docstring"""\nx = "urlopen("\n'))
        self.assertTrue(mentions_urlopen("import urllib.request\nurllib.request.urlopen(r, timeout=5)\n"))
        self.assertTrue(mentions_urlopen("from urllib.request import urlopen\n"))
        self.assertTrue(mentions_urlopen("client = C(opener=urllib.request.urlopen)\n"))


if __name__ == "__main__":
    unittest.main()
