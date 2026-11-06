"""The one place that talks to a language model. Any OpenAI-compatible server works (Ollama by
default, or Groq / OpenRouter / OpenAI by changing LLM_BASE_URL)."""

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ipalpha.config import get_settings
from ipalpha.llm_events import LlmCallEvent, current_node, emit

# Connection problems mean the model server is down, so callers should stop rather than
# dead-letter every remaining item.
LLM_UNAVAILABLE = (APIConnectionError, APITimeoutError)
# Worth retrying with backoff before giving up on a call.
LLM_TRANSIENT = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
RETRY_WAIT = wait_exponential(multiplier=1, max=20)

Message = dict[str, str]
Status = Literal["ok", "repair_failed", "unavailable", "error"]


class StructuredOutputError(RuntimeError):
    """The model kept returning output that fails the schema after all repair attempts."""


@dataclass(frozen=True)
class Completion[T: BaseModel]:
    value: T
    tokens: int
    latency_ms: int
    attempts: int
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """model_json_schema() plus the additionalProperties/required flags OpenAI enforces
    when json_schema.strict is true (including nested $defs)."""
    spec = schema.model_json_schema()
    for node in (spec, *spec.get("$defs", {}).values()):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
    return spec


def extract_json(content: str) -> str:
    """The first balanced {...} object in `content`, ignoring Markdown fences and any prose
    around it. Returns the stripped input when there is no object, so validation reports it."""
    text = content.strip()
    start = text.find("{")
    if start < 0:
        return text
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _brief(exc: ValidationError) -> str:
    return "\n".join(
        f"- {'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
        for err in exc.errors()[:8]
    )


def _failure_status(exc: Exception) -> Status:
    return "unavailable" if isinstance(exc, LLM_UNAVAILABLE) else "error"


def _usage(usage: Any) -> tuple[int, int, int]:
    """(prompt, completion, total) tokens; servers don't always fill in every field."""
    if usage is None:
        return 0, 0, 0
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    return prompt, completion, getattr(usage, "total_tokens", 0) or prompt + completion


class LLM:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        chat_model: str,
        embedding_model: str,
        timeout_s: float,
        max_retries: int | None = None,
        client: Any = None,
    ):
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.base_url = base_url
        self.max_retries = (
            max_retries if max_retries is not None else get_settings().llm_max_retries
        )
        # Retries happen in _call, with backoff, so the client itself doesn't retry.
        self.client = client or OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=0
        )

    def _call[R](self, fn: Callable[..., R], **kwargs: Any) -> R:
        retrying = Retrying(
            stop=stop_after_attempt(self.max_retries),
            wait=RETRY_WAIT,
            retry=retry_if_exception_type(LLM_TRANSIENT),
            reraise=True,
        )
        return retrying(fn, **kwargs)

    def _emit(
        self,
        kind: Literal["structured", "stream", "embed"],
        model: str,
        status: Status,
        start: float,
        *,
        schema_name: str | None = None,
        attempts: int = 1,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        error: str | None = None,
    ) -> None:
        emit(
            LlmCallEvent(
                kind=kind,
                model=model,
                node=current_node(),
                schema_name=schema_name,
                status=status,
                attempts=attempts,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=int((time.perf_counter() - start) * 1000),
                error=error,
            )
        )

    def structured[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        max_repairs: int = 2,
        temperature: float = 0.0,
    ) -> Completion[T]:
        """Ask for JSON matching `schema`; on a validation failure, show the model its errors
        and ask again, up to `max_repairs` times."""
        msgs = list(messages)
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": _strict_schema(schema),
                "strict": True,
            },
        }
        prompt_tokens = completion_tokens = tokens = 0
        start = time.perf_counter()
        error: ValidationError | None = None
        attempt = 0

        def done(status: Status, err: str | None = None) -> None:
            self._emit(
                "structured",
                self.chat_model,
                status,
                start,
                schema_name=schema.__name__,
                attempts=attempt,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error=err,
            )

        for attempt in range(1, max_repairs + 2):
            try:
                resp = self._call(
                    self.client.chat.completions.create,
                    model=self.chat_model,
                    messages=msgs,
                    temperature=temperature,
                    response_format=response_format,
                )
            except Exception as exc:
                done(_failure_status(exc), repr(exc))
                raise
            p, c, t = _usage(resp.usage)
            prompt_tokens += p
            completion_tokens += c
            tokens += t
            content = resp.choices[0].message.content or ""
            try:
                value = schema.model_validate_json(extract_json(content))
            except ValidationError as exc:
                error = exc
                msgs += [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "That JSON failed validation:\n"
                        f"{_brief(exc)}\nReturn the corrected JSON only.",
                    },
                ]
                continue
            done("ok")
            return Completion(
                value,
                tokens=tokens,
                latency_ms=int((time.perf_counter() - start) * 1000),
                attempts=attempt,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        message = f"{schema.__name__}: {error}"
        done("repair_failed", message)
        raise StructuredOutputError(message)

    def stream(self, messages: Sequence[Message], *, temperature: float = 0.2) -> Iterator[str]:
        start = time.perf_counter()
        prompt_tokens = completion_tokens = 0
        try:
            chunks = self._call(
                self.client.chat.completions.create,
                model=self.chat_model,
                messages=list(messages),
                temperature=temperature,
                stream=True,
            )
            for chunk in chunks:
                if getattr(chunk, "usage", None):
                    prompt_tokens, completion_tokens, _ = _usage(chunk.usage)
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as exc:
            self._emit(
                "stream",
                self.chat_model,
                _failure_status(exc),
                start,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error=repr(exc),
            )
            raise
        self._emit(
            "stream",
            self.chat_model,
            "ok",
            start,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        start = time.perf_counter()
        try:
            resp = self._call(
                self.client.embeddings.create,
                model=self.embedding_model,
                input=list(texts),
                encoding_format="float",
            )
        except Exception as exc:
            self._emit("embed", self.embedding_model, _failure_status(exc), start, error=repr(exc))
            raise
        prompt_tokens, _, _ = _usage(getattr(resp, "usage", None))
        self._emit("embed", self.embedding_model, "ok", start, prompt_tokens=prompt_tokens)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


@lru_cache
def get_llm() -> LLM:
    s = get_settings()
    return LLM(
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        chat_model=s.llm_model,
        embedding_model=s.embedding_model,
        timeout_s=s.llm_timeout_s,
        max_retries=s.llm_max_retries,
    )
