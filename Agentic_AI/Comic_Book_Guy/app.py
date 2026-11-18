from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

PRIMARY_MODEL = "gemini-3.1-flash-lite"
FALLBACK_MODEL = "gemini-2.5-flash-lite"
MAX_OUTPUT_TOKENS = 450

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

app = FastAPI(title="CBG Retcon Solutions Bot")


class HistoryMessage(BaseModel):
    role: Literal["user", "model"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[HistoryMessage] = []
    turn: int = 1


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
def chat_endpoint(payload: ChatRequest) -> JSONResponse:
    history_dicts = [item.model_dump() for item in payload.history]
    try:
        reply = generate_cbg_reply(payload.message, history_dicts)
    except TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"reply": UPSTREAM_TIMEOUT_REPLY, "error": True, "code": "UPSTREAM_TIMEOUT"},
        )
    except Exception:
        return JSONResponse(
            status_code=502,
            content={"reply": UPSTREAM_ERROR_REPLY, "error": True, "code": "UPSTREAM_ERROR"},
        )
    return JSONResponse(
        status_code=200,
        content={"reply": reply, "error": False, "code": "OK"},
    )
