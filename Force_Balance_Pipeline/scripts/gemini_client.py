"""Minimal Gemini REST client (doc 05: an explicit User-Agent on every outbound call).

Gemini is called over its generateContent REST endpoint through http_request(), not through the
Google SDK, so the project User-Agent is set explicitly by the same code path as every other
outbound request. Standard library only.

The request shape is built in one place, build_request(), so a change in Google's REST fields is a
one-function fix. Temperature is deliberately never sent: enrichment uses the model's default
(1.0), per Google's Gemini 3 guidance (doc 08).

The response is checked for finishReason STOP. A reply cut off by maxOutputTokens (which includes
thinking tokens) is an error, never a partial result.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

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


def build_request(system_prompt, user_prompt, schema, thinking_level, max_output_tokens=None):
    """The generateContent request body. No temperature: the model default (1.0) applies.

    maxOutputTokens counts thinking tokens as well as the answer, so real runs must leave headroom
    for thinking (scripts/enrich_common.py and the enrich scripts set it).

    Structured output is generationConfig.responseMimeType + generationConfig.responseJsonSchema.
    Evidence from scripts/gemini_probe.py against gemini-3.1-flash-lite (2026-09-24): that pair was
    accepted (step 6), while generationConfig.responseFormat.text.{mimeType, schema}, the shape
    shown on some Google docs pages, was rejected (step 7) with exactly:
        Invalid value at 'generation_config.response_format.text.mime_type'
        (type.googleapis.com/google.ai.generativelanguage.v1beta.TextResponseFormat.MimeType),
        "application/json"
    That names the field's type as an enum, which suggests the API does define responseFormat but
    expects an enum value rather than the MIME string. It was not pursued: responseJsonSchema works.
    thinkingLevel under generationConfig.thinkingConfig was accepted for minimal and low
    (steps 3 and 4). maxOutputTokens of 65536 was accepted (step 20).

    Exact array-length constraints (minItems/maxItems) are rejected with a generic 400: alone at 59
    items, and at 17 items when combined with a 17-value enum (steps 12 and 15-18). Schemas
    therefore carry no minItems/maxItems, and the callers enforce the exact count and id set in
    code after the response (enrich_common.check_exact_ids).
    """
    config = {
        "thinkingConfig": {"thinkingLevel": thinking_level},
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
    }
    if max_output_tokens:
        config["maxOutputTokens"] = int(max_output_tokens)
    return {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": config,
    }


def parse_response(raw):
    """Turn a raw generateContent response body (bytes) into (parsed JSON, usageMetadata).

    Public so a saved raw response can be re-analysed later without calling the API again.
    """
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
        note = (" The output limit was reached; thinking tokens count toward it."
                if finish == "MAX_TOKENS" else "")
        raise GeminiError(f"finishReason {finish!r} (expected STOP).{note}")
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    try:
        return json.loads(text), data.get("usageMetadata", {})
    except ValueError as e:
        raise GeminiError(f"response was not valid JSON ({e}): {text[:300]!r}")


def generate_json(api_key, model, body, raw_path=None):
    """POST one generateContent request; return (parsed JSON, usageMetadata). Retries transient errors.

    With raw_path, the exact response body is written there before it is parsed, so a response that
    later fails parsing or validation can still be inspected and re-analysed without another call.
    """
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
            if raw_path is not None:
                Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
                Path(raw_path).write_bytes(raw)
            return parse_response(raw)
        if status is not None and status not in RETRY_STATUS:
            break
        if attempt < MAX_ATTEMPTS:
            time.sleep(5 * attempt)
    raise GeminiError(f"HTTP {status}: {raw[:600].decode('utf-8', 'replace')}")
