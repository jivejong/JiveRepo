"""Every technique with produces_traffic=true has a request mapping, and
every technique with produces_traffic=false has none — this is the
seed/code consistency the actual HTTP traffic depends on."""

from services.simulator.catalog import load_techniques
from services.simulator.traffic import request_specs_for


def test_every_traffic_producing_technique_has_a_mapping():
    for t in load_techniques():
        specs = request_specs_for(t.technique_id)
        if t.produces_traffic:
            assert specs, f"{t.technique_id} produces_traffic=true but has no request mapping"
        else:
            assert not specs, f"{t.technique_id} produces_traffic=false but has a request mapping"


def test_drive_honeypot_tags_every_request_with_attempt_id(monkeypatch):
    import httpx

    from services.simulator.traffic import drive_honeypot

    sent_headers = []

    def fake_request(self, method, url, headers=None, **kwargs):
        sent_headers.append(headers)
        return httpx.Response(200, request=httpx.Request(method, "http://testserver" + url))

    monkeypatch.setattr(httpx.Client, "request", fake_request)

    with httpx.Client(base_url="http://testserver") as client:
        drive_honeypot(client, "active_scan", attempt_id="abc-123")

    assert len(sent_headers) == 4  # active_scan has 4 request specs
    assert all(h["X-Attempt-Id"] == "abc-123" for h in sent_headers)
