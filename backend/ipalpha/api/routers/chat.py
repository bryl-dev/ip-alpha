import json
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ipalpha.agents.chat_graph import run_chat
from ipalpha.api.schemas import ChatRequest
from ipalpha.config import get_settings
from ipalpha.db.session import session_scope
from ipalpha.llm import LLM_UNAVAILABLE, get_llm

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def sse(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, default=str, ensure_ascii=False)}\n\n"


def _events(req: ChatRequest) -> Iterator[str]:
    llm = get_llm()
    history = [t.model_dump() for t in req.history]
    try:
        # The response outlives the request's dependencies, so the stream owns its session.
        with session_scope() as session:
            for event in run_chat(session, llm, req.message, history):
                yield sse(event)
    except LLM_UNAVAILABLE:
        log.warning("chat: language model unreachable at %s", llm.base_url)
        yield sse({"type": "error", "text": "The language model server is unreachable."})
    except Exception:
        log.exception("chat failed")
        yield sse({"type": "error", "text": "Something went wrong while answering."})


@router.post("/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    """Server-sent events: status, sources, token (repeated), citations, done (or error)."""
    if not get_settings().chat_enabled:
        raise HTTPException(status_code=503, detail="Chat is disabled on this deployment.")
    return StreamingResponse(
        _events(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
