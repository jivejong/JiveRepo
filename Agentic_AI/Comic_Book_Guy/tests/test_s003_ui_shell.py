from html.parser import HTMLParser

from fastapi.testclient import TestClient

import app as cbg_app

client = TestClient(cbg_app.app)


class _DomInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _fetch_dom():
    res = client.get("/")
    assert res.status_code == 200
    parser = _DomInspector()
    parser.feed(res.text)
    return res.text, parser.tags


def test_r009_r006_crt_and_springfield_svg_without_file_input():
    html, tags = _fetch_dom()
    assert any(tag == "svg" and attrs.get("id") == "springfield-bg" for tag, attrs in tags)
    assert any(attrs.get("id") == "crt-monitor" for _, attrs in tags)
    assert not any(tag == "input" and attrs.get("type") == "file" for tag, attrs in tags)
    assert "crt-screen" in html


def test_r010_rate_card_header_and_three_starter_chips():
    html, tags = _fetch_dom()
    headers = [attrs for tag, attrs in tags if tag == "a" and attrs.get("id") == "rate-card-header"]
    assert len(headers) == 1 and headers[0].get("href") == "https://www.linkedin.com/company/cbg-retcon/"
    assert "$300/hr" in html and "$500/hr" in html
    chips = [attrs for tag, attrs in tags if tag == "button" and "starter-chip" in attrs.get("class", "")]
    assert len(chips) == 3 and all(chip.get("data-prompt") for chip in chips)
