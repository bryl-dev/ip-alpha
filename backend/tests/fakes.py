import zlib
from collections.abc import Callable, Iterator, Sequence
from typing import Literal

import numpy as np
from pydantic import BaseModel

from ipalpha.db.models import EMBEDDING_DIM
from ipalpha.llm import Completion, Message
from ipalpha.llm_events import LlmCallEvent, current_node, emit


def trigram_vector(text: str) -> list[float]:
    """Deterministic stand-in for an embedding: similar strings get similar vectors."""
    vec = np.zeros(EMBEDDING_DIM)
    padded = f"  {text.casefold()}  "
    for i in range(len(padded) - 2):
        vec[zlib.crc32(padded[i : i + 3].encode()) % EMBEDDING_DIM] += 1
    norm = np.linalg.norm(vec)
    return (vec / norm if norm else vec).tolist()


class FakeLLM:
    chat_model = "fake-chat"
    embedding_model = "fake-embed"
    base_url = "fake://llm"

    def __init__(
        self,
        responders: dict[type[BaseModel], Callable[[list[Message]], BaseModel]] | None = None,
        reply: Callable[[list[Message]], str] = lambda _: "",
    ):
        self.responders = responders or {}
        self.reply = reply
        self.calls: list[tuple[type[BaseModel], list[Message]]] = []

    def _emit(
        self, kind: Literal["structured", "stream", "embed"], schema_name: str | None = None
    ) -> None:
        model = self.embedding_model if kind == "embed" else self.chat_model
        emit(
            LlmCallEvent(
                kind=kind,
                model=model,
                node=current_node(),
                schema_name=schema_name,
                status="ok",
                attempts=1,
                prompt_tokens=6,
                completion_tokens=4 if kind != "embed" else 0,
                latency_ms=1,
            )
        )

    def structured[T: BaseModel](
        self, messages: Sequence[Message], schema: type[T], **_: object
    ) -> Completion[T]:
        self.calls.append((schema, list(messages)))
        value = self.responders[schema](list(messages))
        assert isinstance(value, schema)
        self._emit("structured", schema.__name__)
        return Completion(
            value, tokens=10, latency_ms=1, attempts=1, prompt_tokens=6, completion_tokens=4
        )

    def stream(self, messages: Sequence[Message], **_: object) -> Iterator[str]:
        text = self.reply(list(messages))
        for i in range(0, len(text), 8):
            yield text[i : i + 8]
        self._emit("stream")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if texts:
            self._emit("embed")
        return [trigram_vector(t) for t in texts]
