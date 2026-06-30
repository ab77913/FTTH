"""Simple in-process rate limiting for auth endpoints."""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window limiter keyed by client identifier."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max(1, max_requests)
        self.window_seconds = max(1.0, window_seconds)
        self._events: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            return True

    def retry_after_seconds(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            bucket = self._events.get(key)
            if not bucket:
                return 1
            oldest = bucket[0]
        wait = self.window_seconds - (now - oldest)
        return max(1, int(wait) + 1)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_login_limiter = SlidingWindowRateLimiter(
    max_requests=_env_int("FTTH_LOGIN_RATE_LIMIT", 10),
    window_seconds=float(_env_int("FTTH_LOGIN_RATE_WINDOW", 60)),
)


def check_login_rate_limit(client_key: str) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds)."""
    if _login_limiter.allow(client_key):
        return True, 0
    return False, _login_limiter.retry_after_seconds(client_key)


def check_auth_action_rate_limit(action: str, client_key: str) -> tuple[bool, int]:
    """Rate limit sensitive auth actions (login, forgot-password, etc.)."""
    return check_login_rate_limit(f"{action}:{client_key}")
