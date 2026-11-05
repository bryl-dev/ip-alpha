import zlib
from collections.abc import Callable, Iterator, Sequence

import numpy as np
from pydantic import BaseModel

from ipalpha.db.models import EMBEDDING_DIM
from ipalpha.llm import Completion, Message


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

    def structured[T: BaseModel](
        self, messages: Sequence[Message], schema: type[T], **_: object
    ) -> Completion[T]:
        self.calls.append((schema, list(messages)))
        value = self.responders[schema](list(messages))
        assert isinstance(value, schema)
        return Completion(value, tokens=10, latency_ms=1, attempts=1)

    def stream(self, messages: Sequence[Message], **_: object) -> Iterator[str]:
        text = self.reply(list(messages))
        for i in range(0, len(text), 8):
            yield text[i : i + 8]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [trigram_vector(t) for t in texts]
