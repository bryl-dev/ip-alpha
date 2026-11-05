"""LLM call events. `llm.py` emits one per call; observability sinks record them."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LlmCallEvent:
    kind: Literal["structured", "stream", "embed"]
    model: str
    node: str
    schema_name: str | None
    status: Literal["ok", "repair_failed", "unavailable", "error"]
    attempts: int
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    error: str | None = None


_node: ContextVar[str] = ContextVar("llm_node", default="unknown")
_sinks: list[Callable[[LlmCallEvent], None]] = []


@contextmanager
def llm_context(node: str) -> Iterator[None]:
    token = _node.set(node)
    try:
        yield
    finally:
        _node.reset(token)


def current_node() -> str:
    return _node.get()


def register_sink(fn: Callable[[LlmCallEvent], None]) -> None:
    _sinks.append(fn)


def clear_sinks() -> None:
    _sinks.clear()


def emit(event: LlmCallEvent) -> None:
    for sink in list(_sinks):
        try:
            sink(event)
        except Exception:
            log.exception("LLM event sink %r failed", sink)
