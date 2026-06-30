"""
Comprehensive tests for LRUTTLCache.

Runs against fakeredis (in-process Redis emulation) when the package is
available, and automatically falls back to testing the OrderedDict fallback
when it is not.  Both code paths are always exercised via the
TestFallbackMode class which explicitly disables Redis.

Run:
    pytest tests/test_lru_ttl_cache.py -v
"""
from __future__ import annotations

from collections import OrderedDict

import pytest

from data_ingestion.utils.lru_ttl_cache import LRUTTLCache

# ── fakeredis fixture ─────────────────────────────────────────────────────────

try:
    import fakeredis
    _FAKEREDIS_AVAILABLE = True
except ImportError:
    _FAKEREDIS_AVAILABLE = False


@pytest.fixture()
def fake_redis_server():
    """Shared fakeredis server instance (keeps data between fixture calls)."""
    if not _FAKEREDIS_AVAILABLE:
        return None
    return fakeredis.FakeServer()


@pytest.fixture()
def redis_cache(fake_redis_server):
    """LRUTTLCache wired to a fakeredis client; max_size=5 for eviction tests."""
    cache = LRUTTLCache.__new__(LRUTTLCache)
    cache._ns = "test:lru"
    cache._ttl = 60
    cache._max_size = 5
    cache._lru_key = "test:lru:__lru__"
    cache._fallback = OrderedDict()

    if _FAKEREDIS_AVAILABLE and fake_redis_server:
        cache._client = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)
        cache._available = True
    else:
        cache._client = None
        cache._available = False

    return cache


# ── Helper ────────────────────────────────────────────────────────────────────

def _backend(cache: LRUTTLCache) -> str:
    return "redis" if cache._available else "fallback"


# ══════════════════════════════════════════════════════════════════════════════
# 1. Basic CRUD
# ══════════════════════════════════════════════════════════════════════════════

class TestBasicOperations:
    def test_set_and_get_dict(self, redis_cache):
        redis_cache.set("k1", {"a": 1, "b": [2, 3]})
        assert redis_cache.get("k1") == {"a": 1, "b": [2, 3]}, f"backend={_backend(redis_cache)}"

    def test_set_and_get_list(self, redis_cache):
        redis_cache.set("list_key", [10, 20, 30])
        assert redis_cache.get("list_key") == [10, 20, 30]

    def test_set_and_get_int(self, redis_cache):
        redis_cache.set("num", 42)
        assert redis_cache.get("num") == 42

    def test_set_and_get_string(self, redis_cache):
        redis_cache.set("s", "hello world")
        assert redis_cache.get("s") == "hello world"

    def test_get_missing_returns_none(self, redis_cache):
        assert redis_cache.get("nonexistent_key_xyz") is None

    def test_overwrite_existing_key(self, redis_cache):
        redis_cache.set("k", "first")
        redis_cache.set("k", "second")
        assert redis_cache.get("k") == "second"

    def test_nested_structure(self, redis_cache):
        data = {"meta": {"nested": {"deep": True}}, "nums": [1, 2, 3]}
        redis_cache.set("nested", data)
        assert redis_cache.get("nested") == data


# ══════════════════════════════════════════════════════════════════════════════
# 2. Delete & Exists
# ══════════════════════════════════════════════════════════════════════════════

class TestDeleteAndExists:
    def test_delete_removes_key(self, redis_cache):
        redis_cache.set("k1", "v1")
        redis_cache.delete("k1")
        assert redis_cache.get("k1") is None

    def test_delete_nonexistent_key_is_noop(self, redis_cache):
        redis_cache.delete("does_not_exist")  # must not raise

    def test_exists_true_for_present_key(self, redis_cache):
        redis_cache.set("k1", "v1")
        assert redis_cache.exists("k1") is True

    def test_exists_false_for_missing_key(self, redis_cache):
        assert redis_cache.exists("missing_xyz") is False

    def test_exists_false_after_delete(self, redis_cache):
        redis_cache.set("k1", "v1")
        redis_cache.delete("k1")
        assert redis_cache.exists("k1") is False

    def test_delete_removes_from_lru_index(self, redis_cache):
        redis_cache.set("k1", "v1")
        redis_cache.delete("k1")
        order = redis_cache.lru_order()
        assert "k1" not in order


# ══════════════════════════════════════════════════════════════════════════════
# 3. LRU Eviction
# ══════════════════════════════════════════════════════════════════════════════

class TestLRUEviction:
    def test_evicts_lru_when_at_capacity(self, redis_cache):
        """With max_size=5: fill to capacity, access k1 (MRU), add k5 → k2 evicted."""
        for i in range(5):
            redis_cache.set(f"k{i}", i)           # k0 k1 k2 k3 k4 → k0=LRU

        redis_cache.get("k0")                     # promote k0 → MRU; k1 becomes LRU

        redis_cache.set("k5", 5)                  # triggers eviction of k1

        assert redis_cache.get("k1") is None, "k1 should be evicted (LRU)"
        assert redis_cache.get("k0") is not None, "k0 was MRU, must survive"
        assert redis_cache.get("k5") is not None, "newly added k5 must survive"

    def test_overwrite_promotes_to_mru(self, redis_cache):
        """Re-setting an existing key should move it to MRU, protecting it from eviction."""
        for i in range(5):
            redis_cache.set(f"k{i}", i)           # k0=LRU ... k4=MRU

        redis_cache.set("k0", 99)                 # re-set k0 → MRU

        redis_cache.set("k5", 5)                  # triggers eviction of k1 (new LRU)

        assert redis_cache.get("k0") == 99, "re-set k0 must survive with updated value"
        assert redis_cache.get("k1") is None, "k1 is now LRU after k0 was promoted"

    def test_get_promotes_to_mru(self, redis_cache):
        """Accessing a key should rescue it from future eviction."""
        for i in range(5):
            redis_cache.set(f"k{i}", i)           # order: k0 ... k4; k0=LRU

        redis_cache.get("k0")                     # k0 is now MRU; k1 is LRU
        redis_cache.get("k0")                     # access again — still MRU

        redis_cache.set("k5", 5)

        assert redis_cache.get("k0") is not None
        assert redis_cache.get("k1") is None

    def test_lru_order_oldest_first(self, redis_cache):
        """lru_order() must return keys from oldest-accessed to most-recently-accessed."""
        redis_cache.set("k1", 1)
        redis_cache.set("k2", 2)
        redis_cache.set("k3", 3)
        redis_cache.get("k1")                     # promote k1 → MRU

        order = redis_cache.lru_order()
        assert order[-1] == "k1", "k1 was accessed last — must be at MRU end"

    def test_eviction_does_not_undercount(self, redis_cache):
        """After filling and evicting, size() must accurately reflect live items."""
        for i in range(5):
            redis_cache.set(f"k{i}", i)
        redis_cache.set("k5", 5)                  # triggers one eviction
        assert redis_cache.size() <= 5

    def test_multiple_evictions(self, redis_cache):
        """Adding many items beyond max_size should evict all overflow."""
        for i in range(10):                        # 5 over max_size
            redis_cache.set(f"big{i}", i)
        assert redis_cache.size() <= 5


# ══════════════════════════════════════════════════════════════════════════════
# 4. Size & LRU order
# ══════════════════════════════════════════════════════════════════════════════

class TestSizeAndOrder:
    def test_size_empty(self, redis_cache):
        assert redis_cache.size() == 0

    def test_size_after_sets(self, redis_cache):
        redis_cache.set("a", 1)
        redis_cache.set("b", 2)
        assert redis_cache.size() == 2

    def test_size_after_delete(self, redis_cache):
        redis_cache.set("a", 1)
        redis_cache.set("b", 2)
        redis_cache.delete("a")
        assert redis_cache.size() == 1

    def test_lru_order_reflects_access_pattern(self, redis_cache):
        redis_cache.set("first", 1)
        redis_cache.set("second", 2)
        redis_cache.set("third", 3)

        redis_cache.get("first")                  # now MRU
        order = redis_cache.lru_order()
        assert order[0] == "second"               # oldest
        assert order[-1] == "first"               # newest

    def test_lru_order_updates_on_set(self, redis_cache):
        redis_cache.set("a", 1)
        redis_cache.set("b", 2)
        redis_cache.set("a", 99)                  # re-set "a" → MRU
        order = redis_cache.lru_order()
        assert order[-1] == "a"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Bulk operations
# ══════════════════════════════════════════════════════════════════════════════

class TestBulkOperations:
    def test_save_and_load_dict(self, redis_cache):
        data = {"city": "New York", "state": "NY", "zip": "10001"}
        redis_cache.save_dict(data)
        loaded = redis_cache.load_dict()
        for k, v in data.items():
            assert loaded[k] == v

    def test_save_dict_overwrites_existing(self, redis_cache):
        redis_cache.set("city", "Old York")
        redis_cache.save_dict({"city": "New York"})
        assert redis_cache.get("city") == "New York"

    def test_save_dict_respects_max_size(self, redis_cache):
        data = {f"key{i}": i for i in range(10)}  # exceeds max_size=5
        redis_cache.save_dict(data)
        assert redis_cache.size() <= 5

    def test_load_dict_excludes_lru_index(self, redis_cache):
        redis_cache.set("mykey", "myval")
        loaded = redis_cache.load_dict()
        assert "__lru__" not in loaded
        assert "mykey" in loaded

    def test_save_and_load_complex_values(self, redis_cache):
        data = {
            "addr1": {"street": "123 Main St", "city": "Anytown", "coords": [40.7128, -74.0060]},
            "addr2": {"street": "456 Oak Ave", "city": "Springfield"},
        }
        redis_cache.save_dict(data)
        loaded = redis_cache.load_dict()
        assert loaded["addr1"] == data["addr1"]
        assert loaded["addr2"] == data["addr2"]


# ══════════════════════════════════════════════════════════════════════════════
# 6. Fallback mode (in-memory OrderedDict)
# ══════════════════════════════════════════════════════════════════════════════

class TestFallbackMode:
    """Explicitly test the OrderedDict path (Redis disabled)."""

    def _make_cache(self, max_size: int = 3) -> LRUTTLCache:
        cache = LRUTTLCache.__new__(LRUTTLCache)
        cache._ns = "fallback:test"
        cache._ttl = 60
        cache._max_size = max_size
        cache._lru_key = "fallback:test:__lru__"
        cache._client = None
        cache._available = False
        cache._fallback = OrderedDict()
        return cache

    def test_set_and_get(self):
        c = self._make_cache()
        c.set("k1", {"x": 1})
        assert c.get("k1") == {"x": 1}

    def test_get_missing_returns_none(self):
        c = self._make_cache()
        assert c.get("missing") is None

    def test_delete(self):
        c = self._make_cache()
        c.set("k1", "v1")
        c.delete("k1")
        assert c.get("k1") is None

    def test_exists(self):
        c = self._make_cache()
        c.set("k1", "v1")
        assert c.exists("k1")
        assert not c.exists("k99")

    def test_lru_eviction(self):
        c = self._make_cache(max_size=3)
        c.set("k1", 1)
        c.set("k2", 2)
        c.set("k3", 3)
        c.get("k1")                               # k1 → MRU; k2 → LRU
        c.set("k4", 4)                            # evict k2
        assert c.get("k2") is None
        assert c.get("k1") is not None
        assert c.get("k4") is not None

    def test_size(self):
        c = self._make_cache()
        assert c.size() == 0
        c.set("a", 1)
        c.set("b", 2)
        assert c.size() == 2
        c.delete("a")
        assert c.size() == 1

    def test_lru_order(self):
        c = self._make_cache()
        c.set("k1", 1)
        c.set("k2", 2)
        c.get("k1")                               # promote k1 → MRU
        order = c.lru_order()
        assert order[-1] == "k1"

    def test_save_and_load_dict(self):
        c = self._make_cache(max_size=10)
        c.save_dict({"a": 1, "b": 2})
        loaded = c.load_dict()
        assert loaded["a"] == 1
        assert loaded["b"] == 2

    def test_available_property_is_false(self):
        c = self._make_cache()
        assert c.available is False

    def test_max_size_enforced_on_bulk_save(self):
        c = self._make_cache(max_size=3)
        c.save_dict({f"k{i}": i for i in range(6)})
        assert c.size() <= 3

    def test_overwrite_promotes_to_mru(self):
        c = self._make_cache(max_size=3)
        c.set("k1", 1)
        c.set("k2", 2)
        c.set("k3", 3)
        c.set("k1", 99)                           # re-set → MRU
        c.set("k4", 4)                            # evict k2 (new LRU)
        assert c.get("k1") == 99
        assert c.get("k2") is None


# ══════════════════════════════════════════════════════════════════════════════
# 7. Custom TTL per item
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not _FAKEREDIS_AVAILABLE, reason="fakeredis required for TTL tests")
class TestCustomTTL:
    def test_custom_ttl_stored(self, redis_cache):
        """set() with explicit ttl should honour that TTL (via fakeredis)."""
        redis_cache.set("short_lived", "data", ttl=1)
        assert redis_cache.get("short_lived") == "data"

    def test_default_ttl_applied(self, redis_cache):
        """Entries stored without explicit ttl use the cache default (60 s here)."""
        redis_cache.set("normal", "data")
        ttl = redis_cache._client.ttl(redis_cache._data_key("normal"))
        # fakeredis reports -1 for no-expire; should have a positive TTL
        assert ttl > 0


# ══════════════════════════════════════════════════════════════════════════════
# 8. Namespace isolation
# ══════════════════════════════════════════════════════════════════════════════

class TestNamespaceIsolation:
    def test_two_caches_same_key_no_collision(self, fake_redis_server):
        """Two caches with different namespaces must not share keys."""
        def _make(ns):
            c = LRUTTLCache.__new__(LRUTTLCache)
            c._ns = ns
            c._ttl = 60
            c._max_size = 10
            c._lru_key = f"{ns}:__lru__"
            c._fallback = OrderedDict()
            if _FAKEREDIS_AVAILABLE and fake_redis_server:
                c._client = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)
                c._available = True
            else:
                c._client = None
                c._available = False
            return c

        cache_a = _make("ns:a")
        cache_b = _make("ns:b")

        cache_a.set("shared_key", "value_A")
        cache_b.set("shared_key", "value_B")

        assert cache_a.get("shared_key") == "value_A"
        assert cache_b.get("shared_key") == "value_B"


# ══════════════════════════════════════════════════════════════════════════════
# 9. Edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_max_size_of_one(self, redis_cache):
        """A cache with max_size=1 should keep only the most recent item."""
        redis_cache._max_size = 1
        redis_cache.set("k1", 1)
        redis_cache.set("k2", 2)
        assert redis_cache.get("k2") is not None
        assert redis_cache.get("k1") is None

    def test_falsy_values_stored_correctly(self, redis_cache):
        """0, False, empty string, empty list — all falsy but valid cache values."""
        redis_cache.set("zero", 0)
        redis_cache.set("false_val", False)
        redis_cache.set("empty_list", [])
        assert redis_cache.get("zero") == 0
        assert redis_cache.get("false_val") is False
        assert redis_cache.get("empty_list") == []

    def test_none_returned_for_expired_lru_entry(self, redis_cache):
        """If a key expired out-of-band the LRU stale entry is cleaned up."""
        redis_cache.set("k1", "v1")
        # Remove the data key directly (simulate TTL expiry)
        if redis_cache._available:
            redis_cache._client.delete(redis_cache._data_key("k1"))
        else:
            redis_cache._fallback.pop("k1", None)
        assert redis_cache.get("k1") is None

    def test_large_value(self, redis_cache):
        """Cache should handle values with many fields without truncation."""
        big = {f"field_{i}": f"value_{i}" * 100 for i in range(50)}
        redis_cache.set("big", big)
        assert redis_cache.get("big") == big
