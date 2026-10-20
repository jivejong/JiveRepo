"""Test helper: a fake transport for files_api.http_request (the `opener` argument). It never touches the network.

handler(method, url, body_or_None) returns (status, json-able object or raw bytes). Every request is recorded in .calls.
"""
import json


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data) if request.data else None
        self.calls.append((request.get_method(), request.full_url))
        status, obj = self.handler(request.get_method(), request.full_url, body)
        raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
        return FakeResponse(status, raw)
