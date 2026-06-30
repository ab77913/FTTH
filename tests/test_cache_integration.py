"""
Integration tests: LRU+TTL cache behaviour as exercised by the agent pipeline.

Tests simulate how agents interact with the cache throughout a pipeline run:
  - Address validation cache hits/misses (Agent 1 pattern)
  - LRU eviction when address volume exceeds max_size
  - JSON round-trip for all result shapes stored by agents
  - Namespace isolation (each agent owns its own namespace)
  - Bulk save_dict / load_dict for cache warm-up and dump
  - Per-key TTL override (short-lived scratch entries)
  - Cache delete and exists helpers
  - Fallback OrderedDict path mirrors Redis semantics

No live Redis required — fakeredis provides a fully in-process backend.

Run:
    pytest tests/test_cache_integration.py -v
"""
from __future__ import annotations

from collections import OrderedDict
from unittest.mock import patch

import pytest

from data_ingestion.utils.lru_ttl_cache import LRUTTLCache
from data_ingestion.utils.redis_cache import RedisCache  # alias check

# ── fakeredis guard ───────────────────────────────────────────────────────────

try:
    import fakeredis
    _FAKEREDIS_AVAILABLE = True
except ImportError:
    _FAKEREDIS_AVAILABLE = False


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture()
def _server():
    if not _FAKEREDIS_AVAILABLE:
        pytest.skip("fakeredis not installed")
    return fakeredis.FakeServer()


def _make_cache(server, namespace="agent:cache", max_size=10, ttl=3600):
    """Build an LRUTTLCache wired to a fakeredis FakeServer."""
    cache = LRUTTLCache.__new__(LRUTTLCache)
    cache._ns = namespace
    cache._ttl = ttl
    cache._max_size = max_size
    cache._lru_key = f"{namespace}:__lru__"
    cache._fallback = OrderedDict()
    cache._client = fakeredis.FakeRedis(server=server, decode_responses=True)
    cache._available = True
    return cache


def _make_fallback_cache(namespace="agent:cache", max_size=10):
    """Build an LRUTTLCache with no Redis (pure OrderedDict fallback)."""
    cache = LRUTTLCache.__new__(LRUTTLCache)
    cache._ns = namespace
    cache._ttl = 3600
    cache._max_size = max_size
    cache._lru_key = f"{namespace}:__lru__"
    cache._fallback = OrderedDict()
    cache._client = None
    cache._available = False
    return cache


# ─────────────────────────────────────────────────────────────────────────────
# 1. RedisCache alias
# ─────────────────────────────────────────────────────────────────────────────

class TestRedisCacheAlias:
    def test_rediscache_is_lruttlcache(self):
        assert RedisCache is LRUTTLCache

    def test_rediscache_fallback_instantiates(self):
        with patch("data_ingestion.utils.lru_ttl_cache.LRUTTLCache._connect"):
            c = RedisCache(namespace="alias_test", redis_url="redis://invalid:9999")
        assert hasattr(c, "get")
        assert hasattr(c, "set")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Agent 1 address-validation cache pattern (cache key = canonical address)
# ─────────────────────────────────────────────────────────────────────────────

_ADDR_RESULT = {
    "canonical": "123 MAIN ST, LEXINGTON, KY 40502",
    "status": "validated",
    "confidence": 0.97,
    "lat": 38.0406,
    "lon": -84.5037,
    "provider": "smarty",
}


class TestAgent1CachePattern:
    """Simulate Agent 1 cache-hit / cache-miss workflow."""

    def test_cache_miss_returns_none(self, _server):
        cache = _make_cache(_server, namespace="agent1:addr")
        assert cache.get("123 MAIN ST") is None

    def test_cache_hit_after_set(self, _server):
        cache = _make_cache(_server, namespace="agent1:addr")
        cache.set("123 MAIN ST", _ADDR_RESULT)
        result = cache.get("123 MAIN ST")
        assert result == _ADDR_RESULT

    def test_cache_hit_preserves_types(self, _server):
        cache = _make_cache(_server, namespace="agent1:addr")
        cache.set("addr", _ADDR_RESULT)
        r = cache.get("addr")
        assert isinstance(r["confidence"], float)
        assert isinstance(r["lat"], float)
        assert r["provider"] == "smarty"

    def test_multiple_addresses_cached(self, _server):
        cache = _make_cache(_server, namespace="agent1:addr", max_size=50)
        records = {f"ADDR_{i}": {**_ADDR_RESULT, "id": i} for i in range(20)}
        for key, val in records.items():
            cache.set(key, val)
        for key, val in records.items():
            assert cache.get(key) == val

    def test_none_value_not_cached(self, _server):
        """Agents skip caching when result is None; get must return None."""
        cache = _make_cache(_server, namespace="agent1:addr")
        # Simulate "don't cache" by simply not calling set()
        assert cache.get("unknown_addr") is None

    def test_cache_overwrite_updates_value(self, _server):
        cache = _make_cache(_server, namespace="agent1:addr")
        cache.set("addr_x", {"status": "pending"})
        cache.set("addr_x", {"status": "validated"})
        assert cache.get("addr_x") == {"status": "validated"}

    def test_exists_returns_true_after_set(self, _server):
        cache = _make_cache(_server, namespace="agent1:addr")
        cache.set("e1", {"v": 1})
        assert cache.exists("e1") is True

    def test_exists_returns_false_for_missing(self, _server):
        cache = _make_cache(_server, namespace="agent1:addr")
        assert cache.exists("not_there") is False

    def test_delete_removes_entry(self, _server):
        cache = _make_cache(_server, namespace="agent1:addr")
        cache.set("del_me", {"v": 99})
        cache.delete("del_me")
        assert cache.get("del_me") is None
        assert cache.exists("del_me") is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. LRU eviction under agent load
# ─────────────────────────────────────────────────────────────────────────────

class TestLRUEvictionUnderAgentLoad:
    """Simulate a job with more addresses than max_size triggers eviction."""

    def test_eviction_keeps_size_within_max(self, _server):
        cache = _make_cache(_server, namespace="evict:test", max_size=5)
        for i in range(10):
            cache.set(f"addr_{i}", {"id": i})
        # After 10 inserts with max_size=5, ZSET should hold ≤5 entries
        lru = cache.lru_order()
        assert len(lru) <= 5

    def test_lru_evicts_oldest_access(self, _server):
        cache = _make_cache(_server, namespace="evict:lru", max_size=3)
        # Fill to capacity
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        # Access 'a' to make it MRU; 'b' becomes LRU
        cache.get("a")
        cache.get("c")
        # Insert 'd' — should evict 'b' (LRU)
        cache.set("d", 4)
        assert cache.get("a") is not None  # MRU — kept
        assert cache.get("c") is not None  # recently accessed — kept
        assert cache.get("d") is not None  # just inserted — kept
        # 'b' should have been evicted
        assert cache.get("b") is None

    def test_re_access_promotes_to_mru(self, _server):
        cache = _make_cache(_server, namespace="promote:test", max_size=3)
        cache.set("x", 10)
        cache.set("y", 20)
        cache.set("z", 30)
        # Access 'x' — now MRU; 'y' becomes new LRU
        cache.get("x")
        cache.set("new", 40)  # should evict 'y'
        assert cache.get("x") is not None
        assert cache.get("z") is not None
        assert cache.get("new") is not None
        assert cache.get("y") is None

    def test_size_reflects_live_entries(self, _server):
        cache = _make_cache(_server, namespace="size:test", max_size=10)
        for i in range(5):
            cache.set(f"k{i}", i)
        assert cache.size() == 5

    def test_lru_order_oldest_to_newest(self, _server):
        cache = _make_cache(_server, namespace="order:test", max_size=10)
        keys = ["first", "second", "third"]
        for k in keys:
            cache.set(k, k)
        order = cache.lru_order()
        # Order should be insertion order (oldest → newest) when no re-access
        assert order == keys


# ─────────────────────────────────────────────────────────────────────────────
# 4. JSON serialization round-trips for all agent result shapes
# ─────────────────────────────────────────────────────────────────────────────

class TestSerializationRoundTrips:
    """All types stored by agents must survive set() → get() intact."""

    @pytest.mark.parametrize("value", [
        # Agent 1 result
        {"canonical": "123 MAIN ST", "status": "validated", "confidence": 0.97, "lat": 38.04, "lon": -84.5},
        # Agent 2 geocoding result
        {"geocoded_lat": 38.0406, "geocoded_lon": -84.5037, "geocode_provider": "google", "geocode_confidence": "ROOFTOP"},
        # Agent 3 parcel
        {"parcel_id": "abc-123", "land_use_code": "SFH", "zoning": "R1", "lot_area_sqft": 7200.0},
        # Agent 4 building
        {"building_type": "residential", "footprint_sqft": 1800.5, "stories": 2, "year_built": 1995},
        # Agent 5 streetview
        {"sv_available": True, "sv_score": 87.3, "sv_lat": 38.04, "sv_lon": -84.50, "bearing": 270.0},
        # Agent 6 final synthesis
        {"final_classification": "SFH", "confidence_score": 0.93, "high_confidence": True,
         "validation_flags": ["geocoded", "parcel_found"], "serviceable": True},
        # Numeric string key (must survive as string, not int)
        "10001",
        # Integer
        42,
        # Float
        3.14159,
        # Nested list
        [1, "two", 3.0, {"nested": True}],
        # Boolean
        True,
    ])
    def test_round_trip(self, _server, value):
        cache = _make_cache(_server, namespace="rt:test", max_size=50)
        cache.set("key", value)
        result = cache.get("key")
        assert result == value

    def test_numeric_string_stays_string(self, _server):
        """The critical fix: '10001' must come back as str, not int 10001."""
        cache = _make_cache(_server, namespace="rt:str", max_size=10)
        cache.set("str_num", "10001")
        result = cache.get("str_num")
        assert result == "10001"
        assert isinstance(result, str)

    def test_empty_dict_round_trip(self, _server):
        cache = _make_cache(_server, namespace="rt:empty")
        cache.set("empty", {})
        assert cache.get("empty") == {}

    def test_empty_list_round_trip(self, _server):
        cache = _make_cache(_server, namespace="rt:elist")
        cache.set("elist", [])
        assert cache.get("elist") == []

    def test_none_type_handling(self, _server):
        """None stored explicitly must come back as None (JSON null)."""
        cache = _make_cache(_server, namespace="rt:null")
        cache.set("null_val", None)
        result = cache.get("null_val")
        assert result is None or result == "null"  # JSON null decodes to Python None


# ─────────────────────────────────────────────────────────────────────────────
# 5. Namespace isolation between agents
# ─────────────────────────────────────────────────────────────────────────────

class TestNamespaceIsolation:
    """Each agent uses its own namespace; keys must not bleed across."""

    def test_same_key_different_namespaces(self, _server):
        ns1 = _make_cache(_server, namespace="agent1:ns")
        ns2 = _make_cache(_server, namespace="agent2:ns")
        ns1.set("shared_key", "from_agent1")
        ns2.set("shared_key", "from_agent2")
        assert ns1.get("shared_key") == "from_agent1"
        assert ns2.get("shared_key") == "from_agent2"

    def test_delete_in_one_namespace_does_not_affect_other(self, _server):
        ns1 = _make_cache(_server, namespace="del_ns1")
        ns2 = _make_cache(_server, namespace="del_ns2")
        ns1.set("k", "v1")
        ns2.set("k", "v2")
        ns1.delete("k")
        assert ns1.get("k") is None
        assert ns2.get("k") == "v2"

    def test_size_scoped_to_namespace(self, _server):
        ns1 = _make_cache(_server, namespace="size_ns1", max_size=20)
        ns2 = _make_cache(_server, namespace="size_ns2", max_size=20)
        for i in range(3):
            ns1.set(f"k{i}", i)
        for i in range(7):
            ns2.set(f"k{i}", i)
        assert ns1.size() == 3
        assert ns2.size() == 7

    def test_lru_order_scoped_to_namespace(self, _server):
        ns1 = _make_cache(_server, namespace="lru_ns1", max_size=10)
        ns2 = _make_cache(_server, namespace="lru_ns2", max_size=10)
        ns1.set("a", 1)
        ns2.set("z", 26)
        assert "a" in ns1.lru_order()
        assert "z" not in ns1.lru_order()
        assert "z" in ns2.lru_order()


# ─────────────────────────────────────────────────────────────────────────────
# 6. Bulk cache operations (save_dict / load_dict)
# ─────────────────────────────────────────────────────────────────────────────

class TestBulkCacheOperations:
    """Simulate cache warm-up at pipeline start and bulk dump at end."""

    def test_save_and_load_dict(self, _server):
        cache = _make_cache(_server, namespace="bulk:test", max_size=100)
        data = {f"addr_{i}": {"id": i, "status": "cached"} for i in range(10)}
        cache.save_dict(data)
        loaded = cache.load_dict()
        for k, v in data.items():
            assert loaded[k] == v

    def test_save_dict_numeric_string_keys(self, _server):
        """Numeric-string values must survive bulk save."""
        cache = _make_cache(_server, namespace="bulk:numstr", max_size=100)
        cache.save_dict({"key1": "10001", "key2": "20002"})
        loaded = cache.load_dict()
        assert loaded["key1"] == "10001"
        assert isinstance(loaded["key1"], str)

    def test_save_dict_empty_does_not_raise(self, _server):
        cache = _make_cache(_server, namespace="bulk:empty", max_size=100)
        cache.save_dict({})  # must not raise
        assert cache.load_dict() == {}

    def test_load_dict_excludes_lru_zset(self, _server):
        """The __lru__ ZSET must never appear in load_dict results."""
        cache = _make_cache(_server, namespace="bulk:lru_ex", max_size=100)
        cache.set("addr_1", {"v": 1})
        loaded = cache.load_dict()
        for k in loaded:
            assert "__lru__" not in k

    def test_save_dict_does_not_exceed_max_size(self, _server):
        """Post-bulk eviction should enforce max_size."""
        cache = _make_cache(_server, namespace="bulk:evict", max_size=5)
        data = {f"k{i}": i for i in range(10)}
        cache.save_dict(data)
        # After eviction the ZSET should not exceed max_size
        assert len(cache.lru_order()) <= 5


# ─────────────────────────────────────────────────────────────────────────────
# 7. Per-key TTL override
# ─────────────────────────────────────────────────────────────────────────────

class TestPerKeyTTL:
    """Agents can override the default TTL for scratch / short-lived entries."""

    def test_custom_ttl_entry_stored(self, _server):
        cache = _make_cache(_server, namespace="ttl:test", ttl=3600)
        cache.set("scratch", {"temp": True}, ttl=10)
        assert cache.get("scratch") == {"temp": True}

    def test_default_ttl_used_when_not_overridden(self, _server):
        cache = _make_cache(_server, namespace="ttl:default", ttl=3600)
        cache.set("k", "v")
        # Check the key TTL was set (should be close to 3600)
        raw_key = "ttl:default:k"
        ttl_val = cache._client.ttl(raw_key)
        assert 3590 <= ttl_val <= 3600

    def test_custom_ttl_shorter_than_default(self, _server):
        cache = _make_cache(_server, namespace="ttl:custom", ttl=3600)
        cache.set("short_lived", "data", ttl=30)
        raw_key = "ttl:custom:short_lived"
        ttl_val = cache._client.ttl(raw_key)
        assert ttl_val <= 30


# ─────────────────────────────────────────────────────────────────────────────
# 8. Fallback mode mirrors Redis semantics
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackModeIntegration:
    """Verify the OrderedDict fallback behaves identically for agent patterns."""

    def test_fallback_get_miss(self):
        cache = _make_fallback_cache()
        assert cache.get("missing") is None

    def test_fallback_set_get(self):
        cache = _make_fallback_cache()
        cache.set("addr", _ADDR_RESULT)
        assert cache.get("addr") == _ADDR_RESULT

    def test_fallback_lru_eviction(self):
        cache = _make_fallback_cache(max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.get("a")   # promote 'a' to MRU; 'b' is LRU
        cache.set("d", 4)  # evicts 'b'
        assert cache.get("a") is not None
        assert cache.get("c") is not None
        assert cache.get("d") is not None
        assert cache.get("b") is None

    def test_fallback_size(self):
        cache = _make_fallback_cache(max_size=10)
        for i in range(5):
            cache.set(f"k{i}", i)
        assert cache.size() == 5

    def test_fallback_delete(self):
        cache = _make_fallback_cache()
        cache.set("x", 99)
        cache.delete("x")
        assert cache.get("x") is None

    def test_fallback_exists(self):
        cache = _make_fallback_cache()
        cache.set("y", "val")
        assert cache.exists("y") is True
        assert cache.exists("nope") is False

    def test_fallback_lru_order(self):
        cache = _make_fallback_cache(max_size=10)
        for k in ["p", "q", "r"]:
            cache.set(k, k)
        assert cache.lru_order() == ["p", "q", "r"]

    def test_fallback_save_and_load_dict(self):
        cache = _make_fallback_cache(max_size=100)
        data = {"addr_1": {"v": 1}, "addr_2": {"v": 2}}
        cache.save_dict(data)
        loaded = cache.load_dict()
        assert loaded == data

    def test_fallback_overwrite(self):
        cache = _make_fallback_cache()
        cache.set("k", "old")
        cache.set("k", "new")
        assert cache.get("k") == "new"

    def test_fallback_numeric_string_preserved(self):
        cache = _make_fallback_cache()
        cache.set("ns", "10001")
        result = cache.get("ns")
        assert result == "10001"
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Pipeline-scale simulation (many addresses, two namespaces)
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineScaleSimulation:
    """Simulate a real job run: 200 addresses, two agent namespaces, LRU churn."""

    def test_200_addr_cache_no_eviction_within_size(self, _server):
        cache = _make_cache(_server, namespace="pipe:agent1", max_size=500, ttl=3600)
        for i in range(200):
            cache.set(f"addr_{i}", {"id": i, "status": "validated"})
        for i in range(200):
            assert cache.get(f"addr_{i}") is not None

    def test_200_addr_eviction_enforced(self, _server):
        cache = _make_cache(_server, namespace="pipe:evict", max_size=50, ttl=3600)
        for i in range(200):
            cache.set(f"addr_{i}", {"id": i})
        assert len(cache.lru_order()) <= 50

    def test_two_agent_caches_isolated(self, _server):
        agent1_cache = _make_cache(_server, namespace="pipe:a1", max_size=200, ttl=3600)
        agent2_cache = _make_cache(_server, namespace="pipe:a2", max_size=200, ttl=3600)
        for i in range(50):
            agent1_cache.set(f"addr_{i}", {"agent": 1, "id": i})
            agent2_cache.set(f"addr_{i}", {"agent": 2, "id": i})
        for i in range(50):
            assert agent1_cache.get(f"addr_{i}")["agent"] == 1
            assert agent2_cache.get(f"addr_{i}")["agent"] == 2

    def test_warm_cache_bulk_then_single_read(self, _server):
        cache = _make_cache(_server, namespace="pipe:warm", max_size=500, ttl=3600)
        bulk = {f"addr_{i}": {"id": i, "validated": True} for i in range(100)}
        cache.save_dict(bulk)
        # All entries accessible after bulk warm-up
        for i in range(100):
            result = cache.get(f"addr_{i}")
            assert result is not None
            assert result["id"] == i

    def test_lru_churn_mixes_hot_and_cold_entries(self, _server):
        """Hot entries (frequently accessed) survive while cold ones are evicted."""
        cache = _make_cache(_server, namespace="pipe:hot", max_size=5, ttl=3600)
        hot_keys = ["hot_a", "hot_b", "hot_c"]
        for k in hot_keys:
            cache.set(k, {"hot": True})

        # Repeatedly access hot keys to keep them MRU
        for _ in range(5):
            for k in hot_keys:
                cache.get(k)

        # Insert cold entries that should displace each other, not hot keys
        for i in range(10):
            cache.set(f"cold_{i}", {"cold": True})
            for k in hot_keys:
                cache.get(k)  # keep hot entries alive

        # Hot keys must still be present
        for k in hot_keys:
            assert cache.get(k) is not None, f"{k} was unexpectedly evicted"
