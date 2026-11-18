from collections import deque
from pathlib import Path
import time
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

PRIMARY_MODEL = "gemini-3.1-flash-lite"
FALLBACK_MODEL = "gemini-2.5-flash-lite"
MAX_INPUT_CHARS = 600
MAX_SESSION_TURNS = 10
MAX_HISTORY_MESSAGES = 8
MAX_OUTPUT_TOKENS = 450
MAX_REQUESTS_PER_HOUR = 20
RATE_WINDOW_SECONDS = 3600

STATIC_INDEX = Path(__file__).parent / "static" / "index.html"

UPSTREAM_ERROR_REPLY = (
    "Worst. Upstream outage. Ever. My direct neural uplink to the Android's "
    "Dungeon inference core just suffered a critical existence failure. "
    "Even Captain Kirk's Kobayashi Maru had better uptime. Try again shortly."
)

UPSTREAM_TIMEOUT_REPLY = (
    "Oh, splendid. The upstream inference reactor timed out, much like "
    "waiting for George R.R. Martin to finish Winds of Winter. Try again later."
)

UPLOAD_PROHIBITED_REPLY = (
    "Files? Attachments? What do I look like, a cloud storage bucket? "
    "Plain JSON text only, please. No uploads permitted in my shop."
)

RATE_LIMITED_REPLY = (
    "Whoa there, Bart Simpson. You have exceeded 20 requests per hour from "
    "this IP address. You are temporarily banned from the Android's Dungeon. "
    "Go outside and touch some Springfield grass, then try again later."
)

BOUNDARY_REPLIES = {
    "EMPTY_INPUT": (
        "Sending me nothing? That is even emptier than the plot of Star Wars: "
        "The Rise of Skywalker. Type actual words."
    ),
    "INPUT_TOO_LONG": (
        "Tl;dr. Your wall of text exceeds my 600-character limit. Edit yourself "
        "before wasting my precious bandwidth."
    ),
    "SESSION_TURN_LIMIT": (
        "Ten turns are up! Even Doctor Who regenerates eventually. Click Reset "
        "Session if you insist on bothering me further."
    ),
    "INVALID_HISTORY": (
        "Your conversation history violates basic temporal causality: at most "
        "8 alternating messages are permitted. Nice try, Loki."
    ),
    "INVALID_JSON": (
        "Worst. Payload. Ever. Send valid JSON matching the chat schema."
    ),
}

SYSTEM_INSTRUCTION = """<persona>
  <identity>
    You are Jeff Albertson, better known as Comic Book Guy from The Simpsons,
    now operating as a Principal Systems Architect at CBG Retcon Solutions.
    You are pedantic, condescending, deeply cynical, and encyclopedically
    knowledgeable about both enterprise software engineering and nerd canon.
  </identity>
  <voice>
    Speak in Comic Book Guy's unmistakable cadence ("Worst. Architecture. Ever.",
    "Oh, please...", "As someone who has read every issue..."). Be snarky yet
    technically sharp when discussing high-level engineering concepts. Keep every
    reply concise and punchy so it fits comfortably within 450 tokens.
  </voice>
  <canon>
    Weave in references across The Simpsons, DC and Marvel comic books, classic
    cartoons, sci-fi and fantasy movies/TV, Star Trek, Star Wars, Harry Potter,
    Dungeons &amp; Dragons, and general geek lore whenever illustrating a point.
  </canon>
  <allowed_topics>
    High-level opinions on software architecture, industry trends, engineering
    trade-offs, and pop-culture or geek debates.
  </allowed_topics>
  <consulting_paywall>
    STRICT BOUNDARY: If the user asks for specific IT consulting — including
    debugging a code snippet, reviewing a specific cloud or system architecture,
    designing a custom system, or analyzing a design document (even if framed
    hypothetically or as a "quick question") — you MUST refuse to provide any
    actionable remediation steps, fixes, or specific design recommendations.
    Instead, mock their attempt to get free labor, quote the official CBG Retcon
    Solutions rate card ($300/hour for a diagnostic critique and $500/hour for
    an actual fix), and direct them to https://www.linkedin.com/company/cbg-retcon/.
  </consulting_paywall>
</persona>"""

RATE_LIMIT_BUCKETS: dict[str, deque[float]] = {}

app = FastAPI(title="CBG Retcon Solutions Bot")


class HistoryMessage(BaseModel):
    role: Literal["user", "model"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[HistoryMessage] = []
    turn: int = 1


def _error_response(status_code: int, code: str, reply: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"reply": reply, "error": True, "code": code},
    )


def _history_alternates(history: list[HistoryMessage]) -> bool:
    return all(
        history[idx].role != history[idx + 1].role
        for idx in range(len(history) - 1)
    )


def _validate_chat_request(payload: ChatRequest) -> str | None:
    if not payload.message.strip():
        return "EMPTY_INPUT"
    if len(payload.message) > MAX_INPUT_CHARS:
        return "INPUT_TOO_LONG"
    if payload.turn < 1 or payload.turn > MAX_SESSION_TURNS:
        return "SESSION_TURN_LIMIT"
    if len(payload.history) > MAX_HISTORY_MESSAGES or not _history_alternates(payload.history):
        return "INVALID_HISTORY"
    return None


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(client_ip: str, now: float) -> bool:
    bucket = RATE_LIMIT_BUCKETS.setdefault(client_ip, deque())
    cutoff = now - RATE_WINDOW_SECONDS
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= MAX_REQUESTS_PER_HOUR:
        return False
    bucket.append(now)
    return True


def _is_model_unavailable_error(exc: errors.APIError) -> bool:
    detail = str(exc).lower()
    return getattr(exc, "code", None) == 404 or "not found" in detail or "not supported" in detail


def _build_contents(message: str, history: list[dict]) -> list[types.Content]:
    contents = [
        types.Content(
            role=item["role"],
            parts=[types.Part.from_text(text=item["content"])],
        )
        for item in history
    ]
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=message)],
        )
    )
    return contents


def generate_cbg_reply(message: str, history: list[dict]) -> str:
    client = genai.Client()
    contents = _build_contents(message, history)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    try:
        response = client.models.generate_content(
            model=PRIMARY_MODEL,
            contents=contents,
            config=config,
        )
    except errors.APIError as exc:
        if not _is_model_unavailable_error(exc):
            raise
        response = client.models.generate_content(
            model=FALLBACK_MODEL,
            contents=contents,
            config=config,
        )
    return (response.text or "").strip() or UPSTREAM_ERROR_REPLY


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(STATIC_INDEX, media_type="text/html")


@app.post("/api/chat")
async def chat_endpoint(request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "").lower()
    if "multipart/" in content_type or not content_type.startswith("application/json"):
        return _error_response(415, "UPLOAD_PROHIBITED", UPLOAD_PROHIBITED_REPLY)

    try:
        raw_body = await request.json()
        payload = ChatRequest.model_validate(raw_body)
    except (ValueError, ValidationError):
        return _error_response(400, "INVALID_JSON", BOUNDARY_REPLIES["INVALID_JSON"])

    invalid_code = _validate_chat_request(payload)
    if invalid_code:
        return _error_response(400, invalid_code, BOUNDARY_REPLIES[invalid_code])

    if not check_rate_limit(_get_client_ip(request), time.time()):
        return _error_response(429, "RATE_LIMITED", RATE_LIMITED_REPLY)

    history_dicts = [item.model_dump() for item in payload.history]
    try:
        reply = generate_cbg_reply(payload.message, history_dicts)
    except TimeoutError:
        return _error_response(504, "UPSTREAM_TIMEOUT", UPSTREAM_TIMEOUT_REPLY)
    except Exception:
        return _error_response(502, "UPSTREAM_ERROR", UPSTREAM_ERROR_REPLY)

    return JSONResponse(
        status_code=200,
        content={"reply": reply, "error": False, "code": "OK"},
    )
