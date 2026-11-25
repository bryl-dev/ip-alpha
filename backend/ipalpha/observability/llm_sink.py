"""Persist LLM call events and optionally forward them to Langfuse."""

from typing import Any

from sqlalchemy import insert

from ipalpha.config import Settings, get_settings
from ipalpha.db.models import LlmCall
from ipalpha.db.session import session_scope
from ipalpha.llm_events import LlmCallEvent


def llm_cost_usd(event: LlmCallEvent, settings: Settings | None = None) -> float:
    """Hypothetical USD cost from token counts and configured price per million tokens."""
    s = settings or get_settings()
    return (
        event.prompt_tokens * s.llm_price_in_per_mtok
        + event.completion_tokens * s.llm_price_out_per_mtok
    ) / 1e6


def gpu_seconds(event: LlmCallEvent) -> float:
    return event.latency_ms / 1000.0


class DbLlmSink:
    def __init__(self, service: str) -> None:
        self.service = service

    def __call__(self, event: LlmCallEvent) -> None:
        settings = get_settings()
        row: dict[str, Any] = {
            "service": self.service,
            "kind": event.kind,
            "model": event.model,
            "node": event.node,
            "schema_name": event.schema_name,
            "status": event.status,
            "attempts": event.attempts,
            "prompt_tokens": event.prompt_tokens,
            "completion_tokens": event.completion_tokens,
            "latency_ms": event.latency_ms,
            "gpu_seconds": gpu_seconds(event),
            "cost_usd": llm_cost_usd(event, settings),
            "error": event.error,
        }
        with session_scope() as session:
            session.execute(insert(LlmCall).values(**row))


class LangfuseSink:
    """Best-effort Langfuse generation span; swallows all errors."""

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._enabled = bool(
            settings.langfuse_public_key and settings.langfuse_secret_key and settings.langfuse_host
        )
        self._client: Any = None
        if self._enabled:
            try:
                from langfuse import Langfuse  # type: ignore[import-untyped]

                self._client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    base_url=settings.langfuse_host,
                )
            except Exception:
                self._enabled = False

    def __call__(self, event: LlmCallEvent) -> None:
        if not self._enabled or self._client is None:
            return
        try:
            cost = llm_cost_usd(event, self._settings)
            total_tokens = event.prompt_tokens + event.completion_tokens
            meta = {
                "node": event.node,
                "kind": event.kind,
                "status": event.status,
                "attempts": event.attempts,
                "schema_name": event.schema_name,
            }
            if event.error:
                meta["error"] = event.error[:2000]
            span = self._client.start_observation(
                name=event.node or "llm",
                as_type="generation",
                model=event.model,
                metadata=meta,
                usage_details={
                    "prompt_tokens": event.prompt_tokens,
                    "completion_tokens": event.completion_tokens,
                    "total_tokens": total_tokens,
                },
                cost_details={"total_cost": cost},
            )
            span.end()
            self._client.flush()
        except Exception:
            pass
