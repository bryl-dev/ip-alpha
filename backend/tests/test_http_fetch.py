from types import SimpleNamespace

import httpx
import pytest
import respx

from ipalpha.config import get_settings
from ipalpha.ingestion import http, steam
from ipalpha.ingestion.http import get_json, get_text

URL = "https://api.example.test/item"


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setattr(http, "time", SimpleNamespace(sleep=calls.append))
    return calls


@respx.mock
def test_429_with_retry_after_is_retried(sleeps: list[float]):
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with httpx.Client() as client:
        assert get_json(client, URL, source="jikan") == {"ok": True}
    assert route.call_count == 2
    assert sleeps == [1.0]


@respx.mock
def test_transport_errors_are_retried_with_backoff(sleeps: list[float]):
    route = respx.get(URL).mock(
        side_effect=[httpx.ConnectError("down"), httpx.Response(200, json=[1])]
    )
    with httpx.Client() as client:
        assert get_json(client, URL, source="jikan") == [1]
    assert route.call_count == 2
    assert sleeps == [1.0]


@respx.mock
def test_404_is_not_retried(sleeps: list[float]):
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        get_json(client, URL, source="jikan")
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
def test_gives_up_after_max_retries(monkeypatch, sleeps: list[float]):
    acquired: list[str] = []
    monkeypatch.setattr(
        http, "limiter", lambda source: SimpleNamespace(acquire=lambda: acquired.append(source))
    )
    route = respx.get(URL).mock(return_value=httpx.Response(503))
    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        get_json(client, URL, source="jikan")
    assert route.call_count == get_settings().http_max_retries
    assert acquired == ["jikan"] * route.call_count
    assert sleeps == [1.0, 2.0, 4.0]


@respx.mock
def test_cache_hit_skips_the_network(fake_redis, sleeps: list[float]):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"n": 1}))
    with httpx.Client() as client:
        first = get_json(client, URL, source="jikan", params={"b": 2, "a": 1}, cache_ttl_s=60)
        second = get_json(client, URL, source="jikan", params={"a": 1, "b": 2}, cache_ttl_s=60)
    assert first == second == {"n": 1}
    assert route.call_count == 1
    [key] = fake_redis.keys("http:jikan:*")
    assert 0 < fake_redis.ttl(key) <= 60


@respx.mock
def test_only_2xx_bodies_are_cached(fake_redis, sleeps: list[float]):
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(404), httpx.Response(200, text="<html>ok</html>")]
    )
    with httpx.Client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            get_text(client, URL, source="oricon", cache_ttl_s=60)
        assert fake_redis.keys("http:*") == []
        assert get_text(client, URL, source="oricon", cache_ttl_s=60) == "<html>ok</html>"
        assert get_text(client, URL, source="oricon", cache_ttl_s=60) == "<html>ok</html>"
    assert route.call_count == 2


@respx.mock
def test_no_cache_without_ttl_or_when_disabled(fake_redis, monkeypatch, sleeps: list[float]):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    with httpx.Client() as client:
        get_json(client, URL, source="jikan")
        get_json(client, URL, source="jikan")
        disabled = get_settings().model_copy(update={"http_cache_enabled": False})
        monkeypatch.setattr(http, "get_settings", lambda: disabled)
        get_json(client, URL, source="jikan", cache_ttl_s=60)
    assert route.call_count == 3
    assert fake_redis.keys("http:*") == []


@respx.mock
def test_steam_reviews_fall_back_to_steamspy_on_non_json(sleeps: list[float]):
    respx.get("https://store.steampowered.com/appreviews/1245620").mock(
        return_value=httpx.Response(200, text="<html>Steam store</html>")
    )
    spy = respx.get(steam.STEAMSPY_URL).mock(
        return_value=httpx.Response(200, json={"positive": 90, "negative": 10})
    )
    with httpx.Client() as client:
        result = steam.fetch_review_summary(client, 1245620)
    assert result == ("steamspy", {"review_total": 100.0, "review_positive_ratio": 0.9})
    assert spy.calls.last.request.url.params["appid"] == "1245620"
