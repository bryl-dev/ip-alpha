"""Per-source token buckets kept in Redis, so every process calling a source shares one limit."""

import time

import redis

from ipalpha.config import get_settings
from ipalpha.infra import redis as redis_infra

# Refills the bucket for the elapsed time, then takes the tokens if there are enough.
# Returns 0 on success, otherwise the milliseconds until enough tokens will have accrued.
_ACQUIRE = """
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = burst
  ts = now
end
tokens = math.min(burst, tokens + math.max(0, now - ts) * rate)
local wait_ms = 0
if tokens >= requested then
  tokens = tokens - requested
else
  wait_ms = math.ceil((requested - tokens) / rate * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], math.ceil(burst / rate * 1000) + 1000)
return wait_ms
"""


class RateLimitTimeout(RuntimeError):
    pass


class TokenBucket:
    def __init__(self, client: redis.Redis, key: str, rate_per_s: float, burst: int):
        if rate_per_s <= 0 or burst < 1:
            raise ValueError(f"invalid rate limit for {key}: {rate_per_s}/s, burst {burst}")
        self.key = f"ratelimit:{key}"
        self.rate_per_s = rate_per_s
        self.burst = burst
        self._script = client.register_script(_ACQUIRE)

    def acquire(self, tokens: int = 1, timeout_s: float = 60.0) -> None:
        """Blocks until tokens are available; atomic via a Lua script on key
        f"ratelimit:{key}" (hash fields: tokens, ts). Raises RateLimitTimeout."""
        if tokens > self.burst:
            raise ValueError(f"{self.key}: {tokens} tokens exceeds the burst of {self.burst}")
        deadline = time.time() + timeout_s
        while True:
            args = [self.rate_per_s, self.burst, time.time(), tokens]
            wait_s = int(self._script(keys=[self.key], args=args)) / 1000
            if wait_s <= 0:
                return
            if time.time() + wait_s > deadline:
                raise RateLimitTimeout(f"{self.key}: no token available within {timeout_s}s")
            time.sleep(wait_s)


def limiter(source: str) -> TokenBucket:
    rate_per_s, burst = get_settings().rate_limits[source]
    return TokenBucket(redis_infra.get_redis(), source, rate_per_s, burst)
