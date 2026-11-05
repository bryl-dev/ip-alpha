from functools import lru_cache

import redis

from ipalpha.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=False)


def redis_ok() -> bool:
    try:
        client = redis.Redis.from_url(
            get_settings().redis_url, socket_timeout=1, socket_connect_timeout=1
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False
