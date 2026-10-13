"""Fixed-window rate limiter backed by Redis, with an in-process fallback.

The fallback keeps the API usable (and tests hermetic) when Redis is unreachable;
it is per-process, so limits are looser with several API workers.
"""

from __future__ import annotations

import threading
import time

import redis

from src.config import get_settings
from src.utils.logging_config import get_logger

log = get_logger()


class RateLimiter:
    def __init__(self) -> None:
        self._client: redis.Redis | None = None
        self._redis_disabled_until = 0.0
        self._memory: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def _redis(self) -> redis.Redis | None:
        if time.monotonic() < self._redis_disabled_until:
            return None
        if self._client is None:
            self._client = redis.Redis.from_url(
                get_settings().redis_db_url(2), socket_connect_timeout=0.5, socket_timeout=0.5
            )
        return self._client

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Count one hit; return True if the caller is still within the limit."""
        if limit <= 0:
            return True
        client = self._redis()
        if client is not None:
            try:
                pipe = client.pipeline()
                pipe.incr(key)
                pipe.expire(key, window_seconds, nx=True)
                count, _ = pipe.execute()
                return int(count) <= limit
            except redis.RedisError:
                log.warning("[rate-limit] Redis unreachable, falling back to in-process counters")
                self._redis_disabled_until = time.monotonic() + 30
        return self._hit_memory(key, limit, window_seconds)

    def reset(self, key: str) -> None:
        client = self._redis()
        if client is not None:
            try:
                client.delete(key)
            except redis.RedisError:
                pass
        with self._lock:
            self._memory.pop(key, None)

    def _hit_memory(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            count, started = self._memory.get(key, (0, now))
            if now - started >= window_seconds:
                count, started = 0, now
            count += 1
            self._memory[key] = (count, started)
            return count <= limit

    def clear_memory(self) -> None:
        with self._lock:
            self._memory.clear()


limiter = RateLimiter()
