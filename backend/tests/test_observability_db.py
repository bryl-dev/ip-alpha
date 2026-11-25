from contextlib import contextmanager
from typing import Literal

import respx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ipalpha.api.main import app
from ipalpha.config import get_settings
from ipalpha.db.session import get_db
from ipalpha.infra import redis as redis_infra
from ipalpha.llm_events import LlmCallEvent
from ipalpha.observability.llm_sink import DbLlmSink

Status = Literal["ok", "repair_failed", "unavailable", "error"]


@contextmanager
def _session_as_scope(session: Session):
    yield session


@respx.mock
def test_metrics_llm_aggregates(session: Session, monkeypatch):
    monkeypatch.setattr(
        "ipalpha.observability.llm_sink.session_scope",
        lambda: _session_as_scope(session),
    )
    sink = DbLlmSink("test")
    rows: list[tuple[str, int, Status]] = [
        ("analyze", 100, "ok"),
        ("analyze", 200, "repair_failed"),
        ("chat:answer", 50, "ok"),
    ]
    for node, ms, status in rows:
        sink(
            LlmCallEvent(
                kind="structured",
                model="m",
                node=node,
                schema_name=None,
                status=status,
                attempts=1,
                prompt_tokens=10,
                completion_tokens=5,
                latency_ms=ms,
            )
        )
    session.commit()

    app.dependency_overrides[get_db] = lambda: session
    try:
        payload = TestClient(app).get("/metrics/llm?days=7").json()
    finally:
        app.dependency_overrides.clear()

    assert payload["calls"] == 3 and payload["failures"] == 1
    assert payload["prompt_tokens"] == 30 and payload["completion_tokens"] == 15
    by = {row["node"]: row for row in payload["by_node"]}
    assert by["analyze"]["calls"] == 2 and by["analyze"]["failures"] == 1
    assert by["chat:answer"]["calls"] == 1


@respx.mock
def test_health_ready_ok_and_503_without_redis(session: Session, monkeypatch):
    settings = get_settings().model_copy(update={"llm_base_url": "http://ollama.test/v1"})
    monkeypatch.setattr("ipalpha.api.routers.health.get_settings", lambda: settings)
    monkeypatch.setattr(redis_infra, "redis_ok", lambda: True)
    respx.get("http://ollama.test/models").mock(
        return_value=respx.MockResponse(200, json={"data": []})
    )

    app.dependency_overrides[get_db] = lambda: session
    try:
        ok = TestClient(app).get("/health/ready")
        assert ok.status_code == 200
        body = ok.json()
        assert body["status"] == "ok"
        assert body["checks"] == {"db": True, "redis": True, "llm": True}

        monkeypatch.setattr(redis_infra, "redis_ok", lambda: False)
        bad = TestClient(app).get("/health/ready")
        assert bad.status_code == 503
        assert bad.json()["status"] == "degraded" and bad.json()["checks"]["redis"] is False
    finally:
        app.dependency_overrides.clear()
