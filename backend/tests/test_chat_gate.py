from contextlib import nullcontext

from fastapi.testclient import TestClient

from ipalpha.api.main import app
from ipalpha.api.routers import chat as chat_router
from ipalpha.config import Settings


def _fail_llm():
    raise AssertionError("the LLM must not be touched when chat is disabled")


def test_chat_disabled_returns_503(monkeypatch):
    monkeypatch.setattr(chat_router, "get_settings", lambda: Settings(chat_enabled=False))
    monkeypatch.setattr(chat_router, "get_llm", _fail_llm)

    resp = TestClient(app).post("/chat", json={"message": "Why is Kadokawa up today?"})

    assert resp.status_code == 503
    assert resp.json() == {"detail": "Chat is disabled on this deployment."}


def test_chat_enabled_streams(monkeypatch):
    monkeypatch.setattr(chat_router, "get_settings", lambda: Settings(chat_enabled=True))
    monkeypatch.setattr(chat_router, "get_llm", lambda: object())
    monkeypatch.setattr(chat_router, "session_scope", lambda: nullcontext(None))
    monkeypatch.setattr(chat_router, "run_chat", lambda *_: iter([{"type": "done"}]))

    resp = TestClient(app).post("/chat", json={"message": "Why is Kadokawa up today?"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert 'data: {"type": "done"}' in resp.text
