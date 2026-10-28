"""Shared test support for the agent: import paths, the repo root, and the fakes (opener, clock) the data-layer tests use.

Same approach as intake/tests/_support.py: the paths are inserted here and the tests import this module first.
"""
import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))                 # the agent package: `import agent.constraints`
sys.path.insert(0, str(ROOT / "scripts"))     # build_starship_seed and starship_roster
sys.path.insert(0, str(ROOT / "ingest" / "bridge"))   # files_api, imported only to build fake transport errors

HOST = "https://example.invalid"
TOKEN = "dapi-NOT-A-REAL-TOKEN-91c4"
WAREHOUSE = "0123456789abcdef"


class Response:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Records every request and returns scripted answers in order. An item is (status, dict/str/bytes) or an Exception to
    raise. With repeat_last the last item is returned for every further request."""

    def __init__(self, script, repeat_last=False):
        self.script = list(script)
        self.repeat_last = repeat_last
        self.requests = []

    def __call__(self, request, timeout=None, **kwargs):          # kwargs: files_api.default_opener also passes context=
        self.requests.append({
            "method": request.get_method(),
            "url": request.full_url,
            "headers": {k.lower(): v for k, v in request.header_items()},
            "body": json.loads(request.data) if request.data else None,
        })
        if not self.script:
            raise AssertionError("the fake opener has no scripted answer left")
        item = self.script[0] if (self.repeat_last and len(self.script) == 1) else self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        return Response(status, body)


class FakeClock:
    """sleep advances monotonic, so a polling loop sees time pass without waiting."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def url_error():
    return urllib.error.URLError("connection reset")


def succeeded(columns, rows, statement_id="st-1"):
    """A SUCCEEDED statement response; columns is [(name, type_name)]."""
    return {"statement_id": statement_id, "status": {"state": "SUCCEEDED"},
            "manifest": {"schema": {"columns": [{"name": n, "type_name": t} for n, t in columns]}},
            "result": {"data_array": rows}}


def in_state(state, statement_id="st-1"):
    return {"statement_id": statement_id, "status": {"state": state}}


def failed(code, message, statement_id="st-1"):
    return {"statement_id": statement_id, "status": {"state": "FAILED", "error": {"error_code": code, "message": message}}}
