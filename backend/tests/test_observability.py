import uuid

import pytest
from fastapi.testclient import TestClient

from ipalpha.api.main import app
from ipalpha.config import Settings
from ipalpha.llm_events import LlmCallEvent, emit, register_sink
from ipalpha.observability.llm_sink import llm_cost_usd


def test_llm_cost_usd():
    s = Settings()
    ev = LlmCallEvent(
        kind="structured",
        model="m",
        node="n",
        schema_name=None,
        status="ok",
        attempts=1,
        prompt_tokens=2_000_000,
        completion_tokens=1_000_000,
        latency_ms=1000,
    )
    assert llm_cost_usd(ev, s) == pytest.approx(2 * 0.15 + 1 * 0.60)


def test_emit_swallows_sink_exception():
    def bad_sink(_event: LlmCallEvent) -> None:
        raise RuntimeError("bad sink")

    register_sink(bad_sink)
    emit(
        LlmCallEvent(
            kind="structured",
            model="m",
            node="x",
            schema_name=None,
            status="ok",
            attempts=1,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
        )
    )


def test_request_id_echo_on_health():
    client = TestClient(app)
    rid = str(uuid.uuid4())
    resp = client.get("/health", headers={"X-Request-ID": rid})
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == rid
    assert resp.json() == {"status": "ok"}
