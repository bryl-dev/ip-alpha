from types import SimpleNamespace

import pytest

from ipalpha.ingestion import ratelimit
from ipalpha.ingestion.ratelimit import RateLimitTimeout, TokenBucket, limiter


class FakeClock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(time=clock.time, sleep=clock.sleep))
    return clock


def test_burst_passes_immediately(fake_redis, clock: FakeClock):
    bucket = TokenBucket(fake_redis, "test", rate_per_s=1.0, burst=3)
    for _ in range(3):
        bucket.acquire()
    assert clock.sleeps == []


def test_next_call_waits_one_over_rate(fake_redis, clock: FakeClock):
    bucket = TokenBucket(fake_redis, "test", rate_per_s=2.0, burst=1)
    bucket.acquire()
    bucket.acquire()
    assert clock.sleeps == [pytest.approx(0.5, abs=0.002)]


def test_tokens_refill_with_elapsed_time(fake_redis, clock: FakeClock):
    bucket = TokenBucket(fake_redis, "test", rate_per_s=1.0, burst=2)
    bucket.acquire()
    bucket.acquire()
    clock.now += 2.0
    bucket.acquire()
    bucket.acquire()
    assert clock.sleeps == []


def test_buckets_with_the_same_key_share_tokens(fake_redis, clock: FakeClock):
    TokenBucket(fake_redis, "shared", rate_per_s=1.0, burst=1).acquire()
    TokenBucket(fake_redis, "shared", rate_per_s=1.0, burst=1).acquire()
    TokenBucket(fake_redis, "other", rate_per_s=1.0, burst=1).acquire()
    assert clock.sleeps == [pytest.approx(1.0, abs=0.002)]
    assert fake_redis.exists("ratelimit:shared", "ratelimit:other") == 2


def test_timeout_raises(fake_redis, clock: FakeClock):
    bucket = TokenBucket(fake_redis, "test", rate_per_s=0.1, burst=1)
    bucket.acquire()
    with pytest.raises(RateLimitTimeout):
        bucket.acquire(timeout_s=5.0)
    assert clock.sleeps == []


def test_rejects_requests_larger_than_the_burst(fake_redis):
    with pytest.raises(ValueError):
        TokenBucket(fake_redis, "test", rate_per_s=1.0, burst=2).acquire(tokens=3)


def test_limiter_uses_configured_source_limits(fake_redis):
    bucket = limiter("oricon")
    assert (bucket.key, bucket.rate_per_s, bucket.burst) == ("ratelimit:oricon", 0.2, 1)
    with pytest.raises(KeyError):
        limiter("unconfigured")
