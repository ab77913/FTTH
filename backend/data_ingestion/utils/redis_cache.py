"""
Redis-backed cache with TTL and LRU eviction.

``RedisCache`` is now a direct alias of ``LRUTTLCache`` — all callers that
use ``from data_ingestion.utils.redis_cache import RedisCache`` continue to
work unchanged.  New code should import ``LRUTTLCache`` directly.
"""
from data_ingestion.utils.lru_ttl_cache import LRUTTLCache

# Backward-compatible alias — same class, same interface, LRU now included.
RedisCache = LRUTTLCache

__all__ = ["RedisCache", "LRUTTLCache"]
