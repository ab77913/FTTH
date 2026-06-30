"""
Redis sliding-window rate limiter.

Replaces bare ``time.sleep()`` calls in the agents with a proper
per-resource rate limiter backed by Redis sorted sets.

Falls back to a simple ``time.sleep()`` when Redis is unavailable.

Usage::

    limiter = RateLimiter("nominatim", rate=1, period=1.0)
    limiter.acquire()           # blocks until a slot is available
    response = requests.get(…)  # safe to call

The implementation uses the **sliding window log** algorithm:

    1. Add current timestamp to the sorted set ``ftth:ratelimit:{resource}``.
    2. Remove entries older than *period* seconds.
    3. Count remaining entries; if >= *rate*, sleep until oldest entry
       + *period* and retry.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_KEY_TEMPLATE = "ftth:ratelimit:{resource}"


class RateLimiter:
    """Sliding-window rate limiter backed by Redis."""

    def __init__(
        self,
        resource: str,
        rate: int = 1,
        period: float = 1.0,
        redis_url: str | None = None,
    ) -> None:
        """
        :param resource: Unique label for the rate-limited resource.
        :param rate: Maximum calls allowed within *period*.
        :param period: Window size in seconds.
        :param redis_url: Override the URL from settings.
        """
        self._key = _KEY_TEMPLATE.format(resource=resource)
        self._rate = rate
        self._period = period
        self._client = None
        self._available = False
        self._connect(redis_url)

    def _connect(self, redis_url: str | None) -> None:
        try:
            import redis as _redis
            from data_ingestion.config.settings import get_settings

            url = redis_url or get_settings().redis_url
            client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
            client.ping()
            self._client = client
            self._available = True
            logger.debug("RateLimiter[%s]: Redis connected", self._key)
        except Exception as exc:
            logger.warning(
                "RateLimiter[%s]: Redis unavailable (%s); falling back to time.sleep()",
                self._key,
                exc,
            )

    def acquire(self) -> None:
        """Block until a request slot is available, then return."""
        if self._available and self._client is not None:
            self._redis_acquire()
        else:
            # Dumb fallback: sleep for the full period / rate
            time.sleep(self._period / self._rate)

    def _redis_acquire(self) -> None:
        """Sliding window log via a Redis sorted set."""
        while True:
            now = time.time()
            window_start = now - self._period

            pipe = self._client.pipeline(transaction=True)
            try:
                # Remove expired entries and count current window
                pipe.zremrangebyscore(self._key, 0, window_start)
                pipe.zcard(self._key)
                _, count = pipe.execute()

                if count < self._rate:
                    # Slot available — record this call
                    self._client.zadd(self._key, {str(now): now})
                    self._client.expire(self._key, int(self._period) + 2)
                    return

                # Slot full — find oldest entry to compute wait time
                oldest = self._client.zrange(self._key, 0, 0, withscores=True)
                if oldest:
                    wait = self._period - (now - oldest[0][1])
                    if wait > 0:
                        time.sleep(wait)
                else:
                    time.sleep(self._period / self._rate)
            except Exception as exc:
                logger.warning("RateLimiter[%s]: Redis error (%s); sleeping", self._key, exc)
                time.sleep(self._period / self._rate)
                return
