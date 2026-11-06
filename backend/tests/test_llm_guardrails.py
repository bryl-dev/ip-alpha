import json
from types import SimpleNamespace

import pytest
from httpx import Request
from openai import APITimeoutError
from pydantic import BaseModel
from tenacity import wait_none

from ipalpha import llm as llm_mod
from ipalpha.llm import LLM, StructuredOutputError, extract_json
from ipalpha.llm_events import LlmCallEvent, register_sink


class _Out(BaseModel):
    n: int


class _ScriptedClient:
    def __init__(self, contents: list[str] | None = None, errors: list[Exception] | None = None):
        self.contents = list(contents or [])
        self.errors = list(errors or [])
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.embeddings = SimpleNamespace(create=self._unused)

    def _unused(self, **_: object) -> None:
        raise AssertionError("embeddings.create should not be called")

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        content = self.contents.pop(0)
        usage = SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage
        )


def _llm(client: _ScriptedClient) -> LLM:
    return LLM(
        base_url="x",
        api_key="x",
        chat_model="m",
        embedding_model="e",
        timeout_s=1,
        max_retries=3,
        client=client,
    )


def _timeout() -> APITimeoutError:
    return APITimeoutError(request=Request("POST", "http://llm.test/v1/chat"))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(llm_mod, "RETRY_WAIT", wait_none())


def test_extract_json_strips_fences_and_prose():
    blob = 'Here you go:\n```json\n{"n": 1, "note": "a } brace"}\n```\nThanks.'
    assert json.loads(extract_json(blob)) == {"n": 1, "note": "a } brace"}
    assert extract_json("not an object") == "not an object"
    assert extract_json("{unterminated") == "{unterminated"


def test_structured_repairs_and_emits_ok():
    events: list[LlmCallEvent] = []
    register_sink(events.append)
    good = json.dumps({"n": 2})
    client = _ScriptedClient([json.dumps({"n": "bad"}), f"prefix {good} suffix"])
    result = _llm(client).structured([{"role": "user", "content": "hi"}], _Out)

    assert result.value.n == 2 and result.attempts == 2
    assert result.prompt_tokens == 6 and result.completion_tokens == 8
    schema = client.requests[0]["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["schema"]["additionalProperties"] is False
    assert events[-1].status == "ok" and events[-1].schema_name == "_Out"
    assert events[-1].kind == "structured" and events[-1].attempts == 2


def test_structured_emits_repair_failed():
    events: list[LlmCallEvent] = []
    register_sink(events.append)
    client = _ScriptedClient(["not json"] * 3)
    with pytest.raises(StructuredOutputError):
        _llm(client).structured([{"role": "user", "content": "hi"}], _Out)
    assert len(client.requests) == 3
    assert events[-1].status == "repair_failed" and events[-1].attempts == 3
    assert events[-1].error is not None


def test_structured_retries_timeout_then_succeeds():
    events: list[LlmCallEvent] = []
    register_sink(events.append)
    client = _ScriptedClient(contents=[json.dumps({"n": 1})], errors=[_timeout()])
    result = _llm(client).structured([{"role": "user", "content": "hi"}], _Out)
    assert result.value.n == 1 and result.attempts == 1
    assert len(client.requests) == 2
    assert events[-1].status == "ok"


def test_structured_emits_unavailable_after_retries():
    events: list[LlmCallEvent] = []
    register_sink(events.append)
    client = _ScriptedClient(errors=[_timeout(), _timeout(), _timeout()])
    with pytest.raises(APITimeoutError):
        _llm(client).structured([{"role": "user", "content": "hi"}], _Out)
    assert len(client.requests) == 3
    assert events[-1].status == "unavailable" and events[-1].kind == "structured"
