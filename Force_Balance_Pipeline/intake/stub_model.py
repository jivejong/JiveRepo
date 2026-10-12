"""A stand-in for the model, for the end-to-end check (e2e_report.py) and the manual browser check (manual_check.py). It replaces
files_api.http_request inside an intake process started with --serve-stub; intake_server.py never imports this file, and no
network call, key or quota is involved.

Two modes:
  fixed   (the e2e) every request gets STUB_ANSWER, whatever the text says.
  varied  (the manual check) the answer follows the description, and a trigger word in the text makes the "model" misbehave so the
          page's handling can be seen:
              [stub:503]      the model is unavailable          -> the server answers 503 with Retry-After 30
              [stub:bad]      the model answers with garbage     -> 503 as well
              [stub:blocked]  the model refuses the text         -> 422 blocked_by_model (the page must not retry)
              [stub:slow]     the model takes 65 s to answer     -> the page's 60 s timeout fires, the retry is answered from the log
              [stub:html]     the rationale is HTML              -> the page must show it as text
"""
import json
import time

STUB_ANSWER = {"relevance_score": 0.95, "midichlorian_ppm": 26400.0, "kyber_resonance": 30.0, "dark_side_activity": 85.0,
               "inference_rationale": "e2e stub: a named dark side figure"}
SLOW_SECONDS = 65
TRIGGERS = ("[stub:503]", "[stub:bad]", "[stub:blocked]", "[stub:slow]", "[stub:html]")
HTML_RATIONALE = '<b>bold</b> <img src=x onerror=alert(1)> &amp; "quotes" and </p><script>alert(2)</script>'
DARK_WORDS = ("vader", "sith", "dark side", "palpatine", "emperor", "kylo", "darth")
FORCE_WORDS = ("jedi", "lightsaber", "kyber", "force", "padawan", "yoda")


def stub_answer_body(answer=None):
    """A generateContent response whose single candidate carries `answer` (default STUB_ANSWER) as its JSON text."""
    return json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(answer or STUB_ANSWER)}]}}]}).encode()


def description_of(request_data):
    """The reporter's text out of the generateContent request the core built; "" if the request is not that shape."""
    try:
        text = json.loads(request_data)["contents"][0]["parts"][0]["text"]
        return str(json.loads(text)["description"])
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def answer_for(description):
    """The varied mode's answer: dark side figures score high, Force talk in between, anything else near zero."""
    lowered = description.lower()
    if any(word in lowered for word in DARK_WORDS):
        return dict(STUB_ANSWER, inference_rationale="manual-check stub: a named dark side figure")
    if any(word in lowered for word in FORCE_WORDS):
        return {"relevance_score": 0.7, "midichlorian_ppm": 9000.0, "kyber_resonance": 70.0, "dark_side_activity": 20.0,
                "inference_rationale": "manual-check stub: Force related, no named dark side figure"}
    return {"relevance_score": 0.04, "midichlorian_ppm": 1500.0, "kyber_resonance": 12.0, "dark_side_activity": 5.0,
            "inference_rationale": "manual-check stub: nothing to do with the Force"}


def make_http(calls_file, *, varied=False, sleep=time.sleep):
    """A files_api.http_request stand-in. Every call is counted (one line in `calls_file`), so a test can see whether the model was asked."""
    def http(method, url, *, headers=None, data=None, timeout=60, opener=None):
        with open(calls_file, "a", encoding="utf-8") as f:
            f.write(url.split("?")[0] + "\n")
        if not varied:
            return 200, stub_answer_body()
        description = description_of(data)
        if "[stub:503]" in description:
            return 503, json.dumps({"error": {"status": "UNAVAILABLE"}}).encode()
        if "[stub:bad]" in description:
            return 200, b"this is not json"
        if "[stub:blocked]" in description:
            return 200, json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()
        if "[stub:slow]" in description:
            sleep(SLOW_SECONDS)
        if "[stub:html]" in description:
            return 200, stub_answer_body(dict(answer_for(description), inference_rationale=HTML_RATIONALE))
        return 200, stub_answer_body(answer_for(description))
    return http
