"""
LRU + TTL Redis cache.

Implements Least-Recently-Used eviction on top of per-key TTL using a
Redis Sorted Set as the access-order index.

Architecture layer: C — Knowledge & Retrieval.

Redis key layout per namespace ``ns``:
  ``ns:{key}``      — STRING value with EX (the actual cached data)
  ``ns:__lru__``    — ZSET keyed by item key, score = float epoch of last access

Access semantics:
  get()  → refreshes the ZSET score (promotes to MRU end of the index).
  set()  → evicts least-recently-used items when size ≥ max_size, then writes.
  TTL applies to individual data keys; the ZSET TTL is set to 2× item TTL
  so the index outlives the items it tracks.

Fallback (Redis unavailable):
  A thread-safe ``collections.OrderedDict`` is used instead.
  LRU semantics are preserved via ``move_to_end()`` on access and
  ``popitem(last=False)`` for eviction.  No TTL is enforced in fallback mode.
"""
from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

_LRU_ZSET_SUFFIX = ":__lru__"


class LRUTTLCache:
    """Redis-backed LRU cache with per-item TTL and configurable max size.

    Parameters
    ----------
    namespace:
        Key prefix used in Redis (e.g. ``"ftth:rgc"``).  All data keys live
        under ``{namespace}:{user_key}``; the LRU index lives at
        ``{namespace}:__lru__``.
    ttl:
        Per-item TTL in seconds (default 30 days = 2 592 000 s).
    max_size:
        Maximum number of entries before LRU eviction kicks in (default 10 000).
    redis_url:
        Override the Redis URL from application settings.
    """

    DEFAULT_MAX_SIZE: int = 10_000

    def __init__(
        self,
        namespace: str,
        ttl: int = 2_592_000,
        max_size: int = DEFAULT_MAX_SIZE,
        redis_url: str | None = None,
    ) -> None:
        self._ns = namespace
        self._ttl = ttl
        self._max_size = max(1, max_size)
        self._lru_key = f"{namespace}{_LRU_ZSET_SUFFIX}"

        self._client = None
        self._available = False
        self._fallback: OrderedDict[str, Any] = OrderedDict()
        self._last_lru_score = 0.0
        self._connect(redis_url)

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect(self, redis_url: str | None) -> None:
        try:
            import redis as _redis
            from data_ingestion.config.settings import get_settings

            url = redis_url or get_settings().redis_url
            client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
            client.ping()
            self._client = client
            self._available = True
            logger.debug("LRUTTLCache[%s]: connected (max_size=%d, ttl=%ds)", self._ns, self._max_size, self._ttl)
        except Exception as exc:
            logger.warning(
                "LRUTTLCache[%s]: Redis unavailable (%s); using OrderedDict fallback",
                self._ns, exc,
            )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _data_key(self, key: str) -> str:
        return f"{self._ns}:{key}"

    def _try(self, fn, *args, **kwargs):
        """Execute *fn* against Redis; swallow any errors and return None."""
        if not self._available or self._client is None:
            return None
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.debug("LRUTTLCache[%s] Redis op failed: %s", self._ns, exc)
            return None

    def _next_lru_score(self) -> float:
        now = time.time()
        last = getattr(self, "_last_lru_score", 0.0)
        if now <= last:
            now = last + 0.000001
        self._last_lru_score = now
        return now

    def _refresh_lru(self, key: str) -> None:
        """Update the ZSET score for *key* to now — promotes it to MRU position."""
        self._try(self._client.zadd, self._lru_key, {key: self._next_lru_score()})

    def _evict_lru_if_needed(self) -> None:
        """Evict the least-recently-used items when the ZSET size ≥ max_size."""
        size = self._try(self._client.zcard, self._lru_key) or 0
        if size < self._max_size:
            return
        overflow = size - self._max_size + 1  # +1 makes room for the incoming item
        # ZRANGE with default order returns lowest-score (oldest) members first
        victims = self._try(self._client.zrange, self._lru_key, 0, overflow - 1) or []
        if not victims:
            return
        data_keys = [self._data_key(v) for v in victims]
        self._try(self._client.delete, *data_keys)
        self._try(self._client.zrem, self._lru_key, *victims)
        logger.debug("LRUTTLCache[%s]: evicted %d LRU item(s)", self._ns, len(victims))

    def _clean_lru_index(self) -> None:
        """Remove TTL-expired entries from the ZSET (lazy cleanup for size())."""
        all_keys = self._try(self._client.zrange, self._lru_key, 0, -1) or []
        expired = [k for k in all_keys if not self._try(self._client.exists, self._data_key(k))]
        if expired:
            self._try(self._client.zrem, self._lru_key, *expired)

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        """Return the cached value and refresh its LRU position, or ``None``."""
        if self._available:
            raw = self._try(self._client.get, self._data_key(key))
            if raw is not None:
                self._refresh_lru(key)          # promote to MRU
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
            # Key expired — remove stale entry from the LRU index
            self._try(self._client.zrem, self._lru_key, key)
            return None

        # ── OrderedDict fallback ──────────────────────────────────────────
        if key in self._fallback:
            self._fallback.move_to_end(key)     # promote to MRU
            return self._fallback[key]
        return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store *value* under *key* with TTL and LRU tracking."""
        effective_ttl = ttl if ttl is not None else self._ttl

        if self._available:
            if not self.exists(key):
                self._evict_lru_if_needed()
            # Always JSON-encode so numeric strings survive the round-trip as strings.
            serialised = json.dumps(value)
            self._try(self._client.setex, self._data_key(key), effective_ttl, serialised)
            self._refresh_lru(key)
            # Keep ZSET alive at least as long as the longest-lived item
            self._try(self._client.expire, self._lru_key, effective_ttl * 2)
            return

        # ── OrderedDict fallback ──────────────────────────────────────────
        if key in self._fallback:
            self._fallback.move_to_end(key)     # re-insert as MRU
        self._fallback[key] = value
        while len(self._fallback) > self._max_size:
            self._fallback.popitem(last=False)  # evict LRU (first item)

    def delete(self, key: str) -> None:
        """Remove *key* from the cache and the LRU index."""
        if self._available:
            self._try(self._client.delete, self._data_key(key))
            self._try(self._client.zrem, self._lru_key, key)
        else:
            self._fallback.pop(key, None)

    def exists(self, key: str) -> bool:
        """Return True if *key* is present (and not TTL-expired)."""
        if self._available:
            return bool(self._try(self._client.exists, self._data_key(key)))
        return key in self._fallback

    def size(self) -> int:
        """Return the count of live (non-expired) entries in this namespace."""
        if self._available:
            self._clean_lru_index()             # purge expired keys from index
            return self._try(self._client.zcard, self._lru_key) or 0
        return len(self._fallback)

    def lru_order(self) -> list[str]:
        """Return keys in LRU order (oldest → most recently used) — diagnostics."""
        if self._available:
            return self._try(self._client.zrange, self._lru_key, 0, -1) or []
        return list(self._fallback.keys())

    def load_dict(self) -> dict[str, Any]:
        """Return all cached entries as a plain dict (bulk read helper)."""
        if not self._available:
            return dict(self._fallback)
        pattern = f"{self._ns}:*"
        result: dict[str, Any] = {}
        prefix_len = len(self._ns) + 1
        try:
            for full_key in self._client.scan_iter(pattern, count=500):
                if full_key == self._lru_key:   # skip the internal ZSET
                    continue
                raw = self._client.get(full_key)
                if raw is not None:
                    short_key = full_key[prefix_len:]
                    try:
                        result[short_key] = json.loads(raw)
                    except json.JSONDecodeError:
                        result[short_key] = raw
        except Exception as exc:
            logger.warning("LRUTTLCache[%s] scan failed: %s", self._ns, exc)
        return result

    def save_dict(self, data: dict[str, Any]) -> None:
        """Bulk-write a plain dict (pipelined for Redis; sequential for fallback)."""
        if not self._available:
            for k, v in data.items():
                self.set(k, v)                  # each set() handles eviction
            return
        try:
            pipe = self._client.pipeline(transaction=False)
            for k, v in data.items():
                pipe.setex(self._data_key(k), self._ttl, json.dumps(v))
                pipe.zadd(self._lru_key, {k: self._next_lru_score()})
            pipe.expire(self._lru_key, self._ttl * 2)
            pipe.execute()
        except Exception as exc:
            logger.warning("LRUTTLCache[%s] bulk save failed: %s", self._ns, exc)
            for k, v in data.items():
                self._fallback[k] = v
        # Post-bulk eviction (pipeline bypasses the per-set eviction check)
        self._evict_lru_if_needed()

    @property
    def available(self) -> bool:
        """True if Redis is connected; False if running on fallback dict."""
        return self._available
