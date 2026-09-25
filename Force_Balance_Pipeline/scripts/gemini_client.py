"""Minimal Gemini REST client (doc 05: an explicit User-Agent on every outbound call).

Gemini is called over its generateContent REST endpoint through http_request(), not through the
Google SDK, so the project User-Agent is set explicitly by the same code path as every other
outbound request. Standard library only.

The request shape is built in one place, build_request(), so a change in Google's REST fields is a
one-function fix. Temperature is deliberately never sent: enrichment uses the model's default
(1.0), per Google's Gemini 3 guidance (doc 08).
"""
import json
import time
import urllib.error
import urllib.request

# Same string as scripts/check_platform.py and scripts/fetch_swapi.py (doc 04).
UA = "Force_Balance_Pipeline/0.1 (+https://github.com/jivejong/JiveRepo/tree/main/Force_Balance_Pipeline)"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class GeminiError(RuntimeError):
    """A Gemini call failed; the message says why and never contains the API key."""


def http_request(method, url, *, headers=None, data=None, timeout=180):
    """The only outbound HTTP path in this module. Always sends the explicit User-Agent."""
    hdrs = {"User-Agent": UA}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        if e.code == 403 and b"1010" in body:
            raise GeminiError("403 'error code: 1010' is a Cloudflare client-fingerprint block, "
                              "not blocked egress")
        return e.code, body


# Two ways to ask for schema-constrained JSON on generateContent. Google's sources disagree on
# which is current, so both are supported and the first --trial settles it:
#   json_schema      generationConfig.responseMimeType + generationConfig.responseJsonSchema
#                    (named in the public generative_service.proto)
#   response_format  generationConfig.responseFormat.text.{mimeType, schema}
#                    (shown on Google's current generate-content docs pages)
STRUCTURED_STYLES = ("json_schema", "response_format")
DEFAULT_STRUCTURED_STYLE = "json_schema"


def build_request(system_prompt, user_prompt, schema, thinking_level,
                  structured_style=DEFAULT_STRUCTURED_STYLE):
    """The generateContent request body. No temperature: the model default (1.0) applies."""
    config = {"thinkingConfig": {"thinkingLevel": thinking_level}}
    if structured_style == "json_schema":
        config["responseMimeType"] = "application/json"
        config["responseJsonSchema"] = schema
    elif structured_style == "response_format":
        config["responseFormat"] = {"text": {"mimeType": "application/json", "schema": schema}}
    else:
        raise ValueError(f"unknown structured style {structured_style!r}")
    return {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": config,
    }


def _parse(raw):
    data = json.loads(raw)
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise GeminiError(f"prompt blocked: {block}")
    candidates = data.get("candidates") or []
    if not candidates:
        raise GeminiError(f"no candidates in response: {raw[:300].decode('utf-8', 'replace')}")
    cand = candidates[0]
    finish = cand.get("finishReason")
    if finish != "STOP":
        raise GeminiError(f"finishReason {finish!r} (expected STOP)")
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    try:
        return json.loads(text), data.get("usageMetadata", {})
    except ValueError as e:
        raise GeminiError(f"response was not valid JSON ({e}): {text[:300]!r}")


def generate_json(api_key, model, body):
    """POST one generateContent request; return (parsed JSON, usageMetadata). Retries transient errors."""
    url = f"{API_ROOT}/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = json.dumps(body).encode("utf-8")
    status, raw = None, b""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            status, raw = http_request("POST", url, headers=headers, data=payload)
        except urllib.error.URLError as e:
            status, raw = None, str(e.reason).encode()
        if status == 200:
            return _parse(raw)
        if status is not None and status not in RETRY_STATUS:
            break
        if attempt < MAX_ATTEMPTS:
            time.sleep(5 * attempt)
    raise GeminiError(f"HTTP {status}: {raw[:600].decode('utf-8', 'replace')}")
