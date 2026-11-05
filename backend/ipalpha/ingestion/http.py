"""Rate-limited, cached, retrying GET helpers shared by the ingestion sources."""

import hashlib
import json
import time
from typing import Any, cast

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ipalpha.config import get_settings
from ipalpha.infra import redis as redis_infra
from ipalpha.ingestion.ratelimit import limiter

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_backoff = wait_exponential(multiplier=1, max=30)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


def _retry_after_s(response: httpx.Response) -> float | None:
    """The Retry-After header in seconds; None when absent or given as an HTTP date."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _wait(state: RetryCallState) -> float:
    exc = state.outcome.exception() if state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = _retry_after_s(exc.response)
        if retry_after is not None:
            return retry_after
    return _backoff(state)


def _fetch(
    client: httpx.Client,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None,
) -> httpx.Response:
    def attempt() -> httpx.Response:
        limiter(source).acquire()
        resp = client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp

    retrying = Retrying(
        stop=stop_after_attempt(get_settings().http_max_retries),
        wait=_wait,
        retry=retry_if_exception(_retryable),
        sleep=time.sleep,
        reraise=True,
    )
    return retrying(attempt)


def _cache_key(
    source: str, url: str, params: dict[str, Any] | None, cache_ttl_s: int | None
) -> str | None:
    if cache_ttl_s is None or not get_settings().http_cache_enabled:
        return None
    raw = url + json.dumps(sorted((params or {}).items()))
    return f"http:{source}:{hashlib.sha1(raw.encode()).hexdigest()}"


def _cache_get(key: str | None) -> str | None:
    if key is None:
        return None
    hit = cast(bytes | None, redis_infra.get_redis().get(key))
    return hit.decode("utf-8") if hit is not None else None


def _cache_set(key: str | None, resp: httpx.Response, cache_ttl_s: int | None) -> None:
    if key is not None and cache_ttl_s is not None and resp.is_success:
        redis_infra.get_redis().setex(key, cache_ttl_s, resp.text.encode("utf-8"))


def get_json(
    client: httpx.Client,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    cache_ttl_s: int | None = None,
) -> Any:
    """Raises httpx.HTTPError once retries are exhausted, and ValueError on a non-JSON body."""
    key = _cache_key(source, url, params, cache_ttl_s)
    if (hit := _cache_get(key)) is not None:
        return json.loads(hit)
    resp = _fetch(client, url, source=source, params=params, headers=headers)
    payload = resp.json()
    _cache_set(key, resp, cache_ttl_s)
    return payload


def get_text(
    client: httpx.Client,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    cache_ttl_s: int | None = None,
) -> str:
    key = _cache_key(source, url, params, cache_ttl_s)
    if (hit := _cache_get(key)) is not None:
        return hit
    resp = _fetch(client, url, source=source, params=params, headers=None)
    _cache_set(key, resp, cache_ttl_s)
    return resp.text
