"""Gemini generateContent client for the agent's function-calling loop (doc 06 "Runtime", "Failure handling").

Every request goes through files_api.http_request, with the API key in the x-goog-api-key header (never the URL), the same
endpoint and blocked-reason handling as intake/intake_core.py. No temperature is ever sent. A transport error, HTTP 429 or 5xx
is retried once after 10 s; a MAX_TOKENS, non-STOP or malformed answer is retried once; a blocked prompt or answer raises
ModelBlocked. Opener, sleep and monotonic are injected. Error messages never contain the key or the request body.
"""
import json
import re
import sys

from agent.config import ROOT
from agent.tools import FUNCTION_DECLARATIONS
from agent.prompt import SYSTEM_PROMPT

sys.path.insert(0, str(ROOT / "ingest" / "bridge"))      # files_api: the one outbound HTTP path

import files_api  # noqa: E402

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
BLOCKED_FINISH_REASONS = ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII")
THINKING_LEVELS = ("minimal", "low", "medium", "high")
TRANSPORT_RETRY_SECONDS = 10
REQUEST_TIMEOUT_SECONDS = 60
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class GeminiUnavailable(Exception):
    """The service could not give a usable answer. The caller leaves the incident unprocessed and writes nothing."""


class ModelBlocked(Exception):
    """The model refused to assess the input (promptFeedback.blockReason or a blocked finishReason)."""


class _BadResponse(Exception):
    pass


class GeminiClient:
    def __init__(self, api_key, model, *, opener, sleep, monotonic, thinking_level="low", max_output_tokens=2048):
        if not isinstance(model, str) or not _MODEL_RE.match(model):
            raise ValueError("model is not a plain model name")
        if thinking_level not in THINKING_LEVELS:
            raise ValueError(f"thinking_level must be one of {THINKING_LEVELS}")
        self.model = model
        self._api_key = api_key
        self._opener = opener
        self._sleep = sleep
        self._monotonic = monotonic
        self._thinking_level = thinking_level
        self._max_output_tokens = max_output_tokens

    def request_body(self, contents):
        return {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": contents,
            "tools": [{"functionDeclarations": FUNCTION_DECLARATIONS}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
            "generationConfig": {
                "thinkingConfig": {"thinkingLevel": self._thinking_level},
                "maxOutputTokens": self._max_output_tokens,
            },
        }

    def _post(self, payload):
        """(status, body) for one request, or None for a transport error."""
        url = f"{API_ROOT}/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}
        try:
            return files_api.http_request("POST", url, headers=headers, data=payload, timeout=REQUEST_TIMEOUT_SECONDS,
                                          opener=self._opener)
        except files_api.TransportError:
            return None

    def generate(self, contents):
        """The parsed response of one successful call. Raises ModelBlocked or GeminiUnavailable."""
        payload = json.dumps(self.request_body(contents)).encode("utf-8")
        transport_retried = False
        response_retried = False
        while True:
            result = self._post(payload)
            if result is None or result[0] == 429 or result[0] >= 500:
                if transport_retried:
                    raise GeminiUnavailable("the Gemini API was unavailable twice in a row")
                transport_retried = True
                self._sleep(TRANSPORT_RETRY_SECONDS)
                continue
            status, raw = result
            if status != 200:
                raise GeminiUnavailable(f"HTTP {status}: {_error_message(raw)}")
            try:
                return self._check(raw)
            except _BadResponse as e:
                if response_retried:
                    raise GeminiUnavailable(f"unusable answer twice in a row: {e}") from None
                response_retried = True

    @staticmethod
    def _check(raw):
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            raise _BadResponse("the response is not JSON") from None
        if not isinstance(data, dict):
            raise _BadResponse("the response is not a JSON object")
        feedback = data.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise ModelBlocked("promptFeedback.blockReason")
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise _BadResponse("no candidates in the response")
        finish = candidates[0].get("finishReason")
        if finish in BLOCKED_FINISH_REASONS:
            raise ModelBlocked("finishReason " + str(finish))
        if finish != "STOP":
            raise _BadResponse(f"finishReason {str(finish)[:30]!r}, expected 'STOP'")
        content = candidates[0].get("content")
        if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
            raise _BadResponse("the candidate has no content parts")
        return data


def _error_message(raw):
    """Google's own error message from an error body (it never contains the key), cut to 300 characters."""
    try:
        message = json.loads(raw)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return "no error message"
    return str(message)[:300]
