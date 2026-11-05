"""The one place that talks to a language model. Any OpenAI-compatible server works (Ollama by
default, or Groq / OpenRouter / OpenAI by changing LLM_BASE_URL)."""

import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from ipalpha.config import get_settings

# Connection problems mean the model server is down, so callers should stop rather than
# dead-letter every remaining item.
LLM_UNAVAILABLE = (APIConnectionError, APITimeoutError)

Message = dict[str, str]


class StructuredOutputError(RuntimeError):
    """The model kept returning output that fails the schema after all repair attempts."""


@dataclass(frozen=True)
class Completion[T: BaseModel]:
    value: T
    tokens: int
    latency_ms: int
    attempts: int


def _brief(exc: ValidationError) -> str:
    return "\n".join(
        f"- {'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
        for err in exc.errors()[:8]
    )


class LLM:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        chat_model: str,
        embedding_model: str,
        timeout_s: float,
        client: Any = None,
    ):
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.base_url = base_url
        self.client = client or OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=1
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
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }
        tokens = 0
        start = time.perf_counter()
        error: ValidationError | None = None
        for attempt in range(1, max_repairs + 2):
            resp = self.client.chat.completions.create(
                model=self.chat_model,
                messages=msgs,
                temperature=temperature,
                response_format=response_format,
            )
            tokens += resp.usage.total_tokens if resp.usage else 0
            content = resp.choices[0].message.content or ""
            try:
                value = schema.model_validate_json(content)
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
            latency_ms = int((time.perf_counter() - start) * 1000)
            return Completion(value, tokens, latency_ms, attempt)
        raise StructuredOutputError(f"{schema.__name__}: {error}")

    def stream(self, messages: Sequence[Message], *, temperature: float = 0.2) -> Iterator[str]:
        chunks = self.client.chat.completions.create(
            model=self.chat_model, messages=list(messages), temperature=temperature, stream=True
        )
        for chunk in chunks:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self.client.embeddings.create(
            model=self.embedding_model, input=list(texts), encoding_format="float"
        )
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
    )
