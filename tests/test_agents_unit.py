"""
Unit tests for all agents — pure logic and mocked HTTP.

Covers:
  Agent 1  — cache key generation, cache hit/miss logic
  Agent 2  — geocoding HTTP call + response parsing
  Agent 3  — land-use classification (pure), Census/Nominatim HTTP mocking
  Agent 4  — Street View metadata + keyword classification
  Agent 5  — structure type and unit-count parsing
  Agent 6  — _synthesize (covered separately) + _upsert / _ensure_table patterns

No live DB or network required.

Run:
    pytest tests/test_agents_unit.py -v
"""
from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from collections import OrderedDict

import pytest

# ── Agent 2 imports ───────────────────────────────────────────────────────────
from data_ingestion.agents.agent2_geocoding import (
    _fm_road_query_aliases,
    _geocode,
    _geocode_query_aliases,
    _geocode_with_fallback,
)

# ── Agent 3 imports ───────────────────────────────────────────────────────────
from data_ingestion.agents.agent3_parcel import _classify_land_use, _call_census
from data_ingestion.agents import agent4_building

# ── Agent 6 imports ───────────────────────────────────────────────────────────

# ── Cache imports ─────────────────────────────────────────────────────────────
from data_ingestion.utils.lru_ttl_cache import LRUTTLCache
from data_ingestion.utils.agent1_input import (
    build_melissa_payload,
    build_smarty_payload,
    resolve_agent1_input,
)
from data_ingestion.agents.agent1_address_validation import (
    _apply_coord_mismatch_gate,
    _apply_quality_gates,
    _distance_m,
    _first_house_number,
    _provider_mode,
    _status_from_score,
)


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 1 — Input resolution + cache (no live Smarty/Melissa)
# ══════════════════════════════════════════════════════════════════════════════


class TestAgent1InputResolver:
    """Address DB columns and validated metadata should beat upload file snapshots."""

    def _addr(self, **kwargs):
        defaults = dict(
            raw_address="805 ADDERTON ST",
            city=None,
            state=None,
            zip_code=None,
            latitude=32.085297,
            longitude=-84.244013,
            validated_street_line=None,
            validated_raw_address=None,
            validated_postcode=None,
            validated_city_state=None,
            validated_country_code=None,
            validated_latitude=None,
            validated_longitude=None,
            source_latitude=None,
            source_longitude=None,
            raw_metadata=None,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_prefers_new_bundle_over_raw_address_column(self):
        meta = {
            "ADDRESS": "214 Walnut Dr, Americus, GA 31719, USA",
            "new": {
                "street_number_name": "214 Walnut Drive",
                "zip_postal_code": "31719",
                "latitude": 32.0944844,
                "longitude": -84.2492642,
                "city_state": "Americus, Georgia",
                "country_code": "US",
            },
            "_routing": {"resolved_address": "514 WALNUT DR"},
        }
        fields = resolve_agent1_input(self._addr(raw_metadata=meta))
        assert fields.raw_address == "214 Walnut Drive"
        assert fields.city == "Americus"
        assert fields.state == "GA"
        assert fields.zip_code == "31719"
        assert fields.country == "US"
        assert fields.latitude == pytest.approx(32.085297)
        assert "raw_metadata.new" in fields.sources

    def test_provider_payloads_from_resolved_fields(self):
        meta = {
            "ADDRESS": "214 Walnut Dr, Americus, GA 31719, USA",
            "new": {
                "street_number_name": "214 Walnut Drive",
                "zip_postal_code": "31719",
                "city_state": "Americus, Georgia",
                "country_code": "US",
            },
        }
        fields = resolve_agent1_input(self._addr(raw_metadata=meta))
        smarty = build_smarty_payload(fields)
        melissa = build_melissa_payload(fields)
        assert smarty == {
            "street": "214 Walnut Drive",
            "city": "Americus",
            "state": "GA",
            "zipcode": "31719",
            "country": "US",
            "candidates": 1,
        }
        assert melissa == {
            "a1": "214 Walnut Drive",
            "loc": "Americus",
            "admarea": "GA",
            "postal": "31719",
            "ctry": "US",
            "format": "JSON",
        }

    def test_parses_address_when_new_bundle_missing(self):
        meta = {"ADDRESS": "214 Walnut Dr, Americus, GA 31719, USA"}
        fields = resolve_agent1_input(self._addr(raw_address="805 ADDERTON ST", raw_metadata=meta))
        assert fields.raw_address == "805 ADDERTON ST"
        assert fields.city == "Americus"
        assert fields.state == "GA"
        assert fields.zip_code == "31719"
        assert "addresses.raw_address" in fields.sources


class TestAgent1CacheLogic:
    """Test the LRU cache as used by Agent 1 (key / get / set / hit / miss)."""

    def _make_cache(self) -> LRUTTLCache:
        c = LRUTTLCache.__new__(LRUTTLCache)
        c._ns = "agent1:test"
        c._ttl = 60
        c._max_size = 100
        c._lru_key = "agent1:test:__lru__"
        c._client = None
        c._available = False
        c._fallback = OrderedDict()
        return c

    def test_cache_miss_returns_none(self):
        cache = self._make_cache()
        assert cache.get("nonexistent_address_key") is None

    def test_cache_set_and_get(self):
        cache = self._make_cache()
        result = {
            "normalized_full_address": "123 Main St, Anytown, NY 10001",
            "validation_status": "AUTO_ACCEPT",
            "confidence_score": 90,
            "smarty_standardized_address": "123 Main St Ste 1",
            "smarty_lat": 40.7128,
            "smarty_lon": -74.0060,
        }
        cache.set("cache_key_1", result)
        retrieved = cache.get("cache_key_1")
        assert retrieved["validation_status"] == "AUTO_ACCEPT"
        assert retrieved["confidence_score"] == 90

    def test_cache_hit_does_not_need_api_call(self):
        """Simulate Agent 1 logic: cache hit skips API calls."""
        cache = self._make_cache()
        stored = {
            "confidence_score": 85,
            "validation_status": "AUTO_ACCEPT",
            "smarty_standardized_address": "456 Oak Ave",
            "smarty_lat": 40.0,
            "smarty_lon": -74.0,
        }
        cache.set("key_existing", stored)

        # Simulate what Agent 1 does: check cache, skip API if hit
        api_called = False
        cached = cache.get("key_existing")
        if cached:
            result = cached
        else:
            api_called = True
            result = {"validation_status": "MANUAL_REVIEW"}

        assert api_called is False
        assert result["validation_status"] == "AUTO_ACCEPT"

    def test_cache_miss_triggers_api_path(self):
        """On cache miss, Agent 1 should call APIs."""
        cache = self._make_cache()
        api_called = False
        cached = cache.get("totally_new_address")
        if cached:
            result = cached
        else:
            api_called = True
            result = {"validation_status": "MANUAL_REVIEW", "confidence_score": 60}
            cache.set("totally_new_address", result)

        assert api_called is True
        # Now the same key should hit
        assert cache.get("totally_new_address") is not None

    def test_cache_status_mapping(self):
        """Test the cache-hit confidence → validation_status mapping."""
        cache = self._make_cache()

        for score, expected_status in [
            (90, "AUTO_ACCEPT"),
            (80, "MANUAL_REVIEW"),
            (70, "MANUAL_REVIEW"),
            (55, "REJECT"),
            (40, "REJECT"),
            (20, "REJECT"),
            (0,  "REJECT"),
        ]:
            cache.set(f"key_{score}", {"confidence_score": score})
            cached = cache.get(f"key_{score}")
            derived_status = _status_from_score(cached["confidence_score"])
            assert derived_status == expected_status, \
                f"score={score}: expected {expected_status}, got {derived_status}"

    def test_cache_eviction_respects_lru(self):
        """Cache evicts LRU entry when max_size is exceeded."""
        c = LRUTTLCache.__new__(LRUTTLCache)
        c._ns = "a1:evict"
        c._ttl = 60
        c._max_size = 3
        c._lru_key = "a1:evict:__lru__"
        c._client = None
        c._available = False
        c._fallback = OrderedDict()

        c.set("addr1", {"status": "AUTO_ACCEPT"})
        c.set("addr2", {"status": "AUTO_ACCEPT"})
        c.set("addr3", {"status": "AUTO_ACCEPT"})

        c.get("addr1")  # addr1 → MRU; addr2 → LRU

        c.set("addr4", {"status": "AUTO_ACCEPT"})  # evicts addr2

        assert c.get("addr2") is None
        assert c.get("addr1") is not None
        assert c.get("addr4") is not None


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 2 — Google Geocoding
# ══════════════════════════════════════════════════════════════════════════════

class TestAgent1QualityGates:
    def test_provider_mode_defaults_to_smarty_only(self, monkeypatch):
        monkeypatch.delenv("FTTH_AGENT1_PROVIDER_MODE", raising=False)
        assert _provider_mode() == "smarty_only"

    def test_provider_mode_allows_dual_provider(self, monkeypatch):
        monkeypatch.setenv("FTTH_AGENT1_PROVIDER_MODE", "dual_provider")
        assert _provider_mode() == "dual_provider"

    def test_house_number_parser(self):
        assert _first_house_number("123 Main St") == "123"
        assert _first_house_number("Apt 2B") == "2B"
        assert _first_house_number(None) is None

    def test_distance_m_sanity(self):
        assert _distance_m(40.0, -74.0, 40.0, -74.0) == pytest.approx(0)
        assert _distance_m(None, -74.0, 40.0, -74.0) is None

    def test_auto_accept_requires_matching_house_number(self):
        chosen = SimpleNamespace(
            success=True,
            dpv_match="Y",
            standardized_address="456 Main St",
            latitude=None,
            longitude=None,
            raw_response={},
        )
        canonical = SimpleNamespace(raw_address="123 Main St")
        addr = SimpleNamespace(latitude=None, longitude=None)

        status, score, reason = _apply_quality_gates(
            status="AUTO_ACCEPT",
            score=95,
            chosen=chosen,
            comparison=None,
            addr=addr,
            canonical=canonical,
            exc_reason=None,
        )

        assert status == "MANUAL_REVIEW"
        assert score == 79
        assert "house number mismatch" in reason

    def test_auto_accept_downgrades_far_provider_geocode(self):
        chosen = SimpleNamespace(
            success=True,
            dpv_match="Y",
            standardized_address="123 Main St",
            latitude=41.0,
            longitude=-74.0,
            raw_response={},
        )
        canonical = SimpleNamespace(raw_address="123 Main St")
        addr = SimpleNamespace(latitude=40.0, longitude=-74.0)

        status, score, reason = _apply_quality_gates(
            status="AUTO_ACCEPT",
            score=96,
            chosen=chosen,
            comparison=None,
            addr=addr,
            canonical=canonical,
            exc_reason=None,
        )

        assert status == "MANUAL_REVIEW"
        assert score == 79
        assert "supplied coordinates" in reason

    def test_provider_failure_with_coords_goes_manual_not_auto(self):
        chosen = SimpleNamespace(success=False)
        canonical = SimpleNamespace(raw_address="123 Main St")
        addr = SimpleNamespace(latitude=40.0, longitude=-74.0)

        status, score, reason = _apply_quality_gates(
            status="REJECT",
            score=0,
            chosen=chosen,
            comparison=None,
            addr=addr,
            canonical=canonical,
            exc_reason="Both providers failed",
        )

        assert status == "MANUAL_REVIEW"
        assert score == 35
        assert "coordinates available" in reason

    def test_coord_address_mismatch_blocks_agent1_auto_accept(self):
        addr = SimpleNamespace(
            coord_address_match_status="ADDRESS_MISMATCH",
            raw_metadata={},
        )

        status, score, reason = _apply_coord_mismatch_gate(
            status="AUTO_ACCEPT",
            score=100,
            exc_reason=None,
            addr=addr,
        )

        assert status == "REJECT"
        assert score == 30
        assert "ADDRESS_MISMATCH" in reason


def _make_geocode_response(status="OK", lat=40.7128, lon=-74.0060,
                           location_type="ROOFTOP", formatted="123 Main St, NY"):
    return json.dumps({
        "status": status,
        "results": [{
            "formatted_address": formatted,
            "geometry": {
                "location": {"lat": lat, "lng": lon},
                "location_type": location_type,
            },
            "place_id": "ChIJtest123",
        }] if status == "OK" else [],
    }).encode()


class TestAgent2Geocoding:
    def test_fm_road_query_aliases_normalizes_rural_road_names(self):
        assert _fm_road_query_aliases("404 E FM ROAD 11, IMPERIAL, TX, 79743") == [
            "404 E FM 11, IMPERIAL, TX, 79743"
        ]
        assert _fm_road_query_aliases("404 E FARM TO MARKET ROAD 11, IMPERIAL, TX, 79743") == [
            "404 E FM 11, IMPERIAL, TX, 79743"
        ]

    def test_geocode_query_aliases_adds_imperial_tx_provider_spellings(self):
        assert "215 S COOLEDGE ST, IMPERIAL, TX, 79743" in _geocode_query_aliases(
            "215 S COOLIDGE ST, IMPERIAL, TX, 79743"
        )
        assert "318 E FM 11, IMPERIAL, TX, 79743" in _geocode_query_aliases(
            "318 E STATE HIGHWAY 11, IMPERIAL, TX, 79743"
        )
        assert _geocode_query_aliases("215 S COOLIDGE ST, AUSTIN, TX, 78701") == []

    def test_geocode_with_fallback_retries_accepted_query_alias(self):
        rejected = {
            "ok": True,
            "source": "GOOGLE",
            "address_accepted": False,
            "address_match_percent": 40,
            "confidence": 0,
        }
        accepted = {
            "ok": True,
            "source": "GOOGLE",
            "address_accepted": True,
            "address_match_percent": 100,
            "confidence": 95,
            "latitude": 31.26,
            "longitude": -102.69,
        }

        with patch("data_ingestion.agents.agent2_geocoding._get_api_key", return_value="key"):
            with patch("data_ingestion.agents.agent2_geocoding._forward_geocode_google") as mock_forward:
                with patch(
                    "data_ingestion.agents.agent2_geocoding._finalize_geocode_result",
                    side_effect=[rejected, accepted],
                ):
                    result, steps, executed = _geocode_with_fallback(
                        "404 E FM ROAD 11, IMPERIAL, TX, 79743",
                        geocode_options={
                            "google_geocoding": True,
                            "osm_geocoding": False,
                            "street_interpolation": False,
                        },
                    )

        assert result is accepted
        assert result["query_alias"] == "404 E FM 11, IMPERIAL, TX, 79743"
        assert executed["geocoding"] is accepted
        assert any("accepted query alias" in step for step in steps)
        assert mock_forward.call_args_list[1].args[0] == "404 E FM 11, IMPERIAL, TX, 79743"

    def test_geocode_success_returns_dict(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = _make_geocode_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _geocode("123 Main St, New York, NY 10001")

        assert result is not None
        assert result["latitude"] == 40.7128
        assert result["longitude"] == -74.0060
        assert result["location_type"] == "ROOFTOP"
        assert result["confidence"] == 95

    def test_geocode_range_interpolated_confidence(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = _make_geocode_response(location_type="RANGE_INTERPOLATED")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _geocode("456 Oak Ave")

        assert result["confidence"] == 75

    def test_geocode_approximate_confidence(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = _make_geocode_response(location_type="APPROXIMATE")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _geocode("Some City, NY")

        assert result["confidence"] == 40

    def test_geocode_api_zero_results_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = _make_geocode_response(status="ZERO_RESULTS")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _geocode("bad address")

        assert result is None

    def test_geocode_network_error_returns_none(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = _geocode("123 Main St")
        assert result is None

    def test_geocode_timeout_returns_none(self):
        import socket
        with patch("urllib.request.urlopen", side_effect=socket.timeout("timeout")):
            result = _geocode("123 Main St")
        assert result is None

    def test_geocode_formats_formatted_address(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = _make_geocode_response(
            formatted="123 Main St, New York, NY 10001, USA"
        )
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _geocode("123 Main St")

        assert "New York" in result["formatted_address"]


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 3 — Land-use classification (pure)
# ══════════════════════════════════════════════════════════════════════════════

class TestAgent3LandUseClassification:
    def test_apt_keyword_multi_family(self):
        assert _classify_land_use("123 Main St Apt 2B", None) == "Multi-Family Residential"

    def test_apartment_keyword(self):
        assert _classify_land_use("456 Oak Apartment Complex", None) == "Multi-Family Residential"

    def test_suite_keyword_multi_family(self):
        assert _classify_land_use("789 Business Blvd Ste 200", None) == "Multi-Family Residential"

    def test_unit_hash_keyword(self):
        assert _classify_land_use("100 Park Ave Unit #5", None) == "Multi-Family Residential"

    def test_drive_keyword_single_family(self):
        assert _classify_land_use("42 Maple Drive", None) == "Single-Family Residential"

    def test_lane_keyword_single_family(self):
        assert _classify_land_use("7 Shady Lane", None) == "Single-Family Residential"

    def test_court_keyword_single_family(self):
        assert _classify_land_use("15 Oak Court", None) == "Single-Family Residential"

    def test_highway_keyword_commercial(self):
        assert _classify_land_use("1000 State Hwy 35", None) == "Commercial/Industrial"

    def test_industrial_keyword_commercial(self):
        assert _classify_land_use("555 Industrial Pkwy", None) == "Commercial/Industrial"

    def test_street_suffix_classifies_single_family(self):
        assert _classify_land_use("123 Main St", None) == "Single-Family Residential"

    def test_unknown_address_without_suffix(self):
        assert _classify_land_use("123 Main", None) == "Unknown"

    def test_parcel_data_overrides_keyword(self):
        """When parcel data has land_use, it takes precedence over keywords."""
        parcel = {"land_use": "Commercial Office"}
        result = _classify_land_use("42 Maple Drive", parcel)
        assert result == "Commercial Office"

    def test_none_address_returns_unknown(self):
        assert _classify_land_use(None, None) == "Unknown"

    def test_empty_address_returns_unknown(self):
        assert _classify_land_use("", None) == "Unknown"

    def test_case_insensitive_matching(self):
        assert _classify_land_use("123 MAIN ST APT 5", None) == "Multi-Family Residential"


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 3 — Census HTTP call
# ══════════════════════════════════════════════════════════════════════════════

class TestAgent3CensusCall:
    def _make_census_response(self):
        return json.dumps({
            "result": {
                "geographies": {
                    "Census Tracts": [{
                        "STATE": "36",
                        "COUNTY": "061",
                        "TRACT": "002400",
                        "GEOID": "36061002400",
                        "AREALAND": "1234567",
                        "NAMELSAD": "Census Tract 24",
                    }],
                    "Counties": [{"NAME": "New York County"}],
                }
            }
        }).encode()

    def test_census_success(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_census_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _call_census(40.7128, -74.0060)

        assert result is not None
        assert result["state_fips"] == "36"
        assert result["county_fips"] == "061"
        assert result["county_name"] == "New York County"
        assert result["source"] == "census"

    def test_census_network_error_returns_none(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Timeout")):
            result = _call_census(40.7128, -74.0060)
        assert result is None

    def test_census_empty_response_returns_partial(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"result": {}}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _call_census(40.0, -74.0)

        # Should still return a dict (possibly with empty strings)
        assert isinstance(result, dict)
        assert result.get("source") == "census"


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 4 — Street View metadata + keyword classification
# ══════════════════════════════════════════════════════════════════════════════

from data_ingestion.agents.agent4_building import _sv_metadata


class TestAgent4StreetView:
    def test_sv_metadata_ok_returns_true(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "OK"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _sv_metadata(40.7128, -74.0060)

        assert result is True

    def test_sv_metadata_zero_results_returns_false(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ZERO_RESULTS"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _sv_metadata(40.0, -74.0)

        assert result is False

    def test_sv_metadata_network_error_returns_false(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = _sv_metadata(40.0, -74.0)
        assert result is False

    def test_sv_metadata_not_found_returns_false(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "NOT_FOUND"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _sv_metadata(0.0, 0.0)

        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 5 — Bearing calculation + imagery
# ══════════════════════════════════════════════════════════════════════════════

from data_ingestion.agents.agent5_streetview import _bearing, _sv_meta


class TestAgent5Bearing:
    def test_bearing_north(self):
        """Points due north: bearing should be ~0°."""
        b = _bearing(0.0, 0.0, 1.0, 0.0)
        assert abs(b - 0.0) < 1.0 or abs(b - 360.0) < 1.0

    def test_bearing_east(self):
        """Points due east: bearing should be ~90°."""
        b = _bearing(0.0, 0.0, 0.0, 1.0)
        assert 85 < b < 95

    def test_bearing_south(self):
        """Points due south: bearing should be ~180°."""
        b = _bearing(1.0, 0.0, 0.0, 0.0)
        assert 175 < b < 185

    def test_bearing_west(self):
        """Points due west: bearing should be ~270°."""
        b = _bearing(0.0, 1.0, 0.0, 0.0)
        assert 265 < b < 275

    def test_bearing_always_0_to_360(self):
        """Bearing must always be in [0, 360)."""
        import random
        random.seed(42)
        for _ in range(20):
            lat1 = random.uniform(-89, 89)
            lon1 = random.uniform(-179, 179)
            lat2 = random.uniform(-89, 89)
            lon2 = random.uniform(-179, 179)
            b = _bearing(lat1, lon1, lat2, lon2)
            assert 0 <= b < 360, f"bearing={b} out of range"


class TestAgent5OcrZoomRefinement:
    def test_tighter_fov_steps_prioritizes_gpt_recommendation(self):
        from data_ingestion.utils.agent5_iterative_search import _tighter_fov_steps

        assert _tighter_fov_steps(28, {"recommended_fov": 15}) == [15, 25, 22, 20, 18]
        assert _tighter_fov_steps(30, None) == [25, 22, 20, 18, 15]
        assert _tighter_fov_steps(10, None) == []

    def test_should_refine_when_number_visible_or_partial_ocr(self):
        from data_ingestion.utils.agent5_iterative_search import _should_refine_zoom_for_ocr

        assert _should_refine_zoom_for_ocr({"house_number_visible": True})
        assert _should_refine_zoom_for_ocr({"house_number_text": "15230"})
        assert _should_refine_zoom_for_ocr(None, paddle_partial=True)
        assert not _should_refine_zoom_for_ocr(None)

    def test_fast_mode_stops_after_repeated_empty_ocr_probes(self):
        from io import BytesIO

        from PIL import Image

        from data_ingestion.utils.agent5_iterative_search import iterative_house_number_search

        buf = BytesIO()
        Image.new("RGB", (32, 32), "white").save(buf, format="JPEG")
        image_bytes = buf.getvalue()
        fetch_calls = []

        def fake_fetch(lat, lon, heading, fov):
            fetch_calls.append((lat, lon, heading, fov))
            return image_bytes, "streetview-url"

        with patch("data_ingestion.utils.agent5_iterative_search.tesseract_available", return_value=False):
            with patch(
                "data_ingestion.utils.agent5_iterative_search.detect_house_number_with_paddle",
                return_value=(False, 0.0, ""),
            ):
                state = iterative_house_number_search(
                    lat=1.0,
                    lon=2.0,
                    base_heading=90.0,
                    expected_house_number="15290",
                    address="15290 Test Rd",
                    fetch_streetview_image=fake_fetch,
                    azure_analyze=lambda _img: {"readResult": {"blocks": []}},
                    detect_house_number=lambda *_args, **_kwargs: (False, 0.0),
                    extract_ocr_text=lambda _vision: "",
                    images=[],
                    variant_visions=[],
                    global_ocr_results=[],
                    max_iterations=5,
                    gpt_enabled=False,
                    fast_mode=True,
                    paddle_scan=None,
                )

        assert state["found"] is False
        assert state["iterations_used"] == 3
        assert len(fetch_calls) == 3


class TestAgent5PaddleOcrScan:
    def test_paddle_ocr_prefers_angle_aware_initializer(self):
        from types import SimpleNamespace

        from data_ingestion.utils import agent5_paddle_ocr

        calls = []

        class FakePaddleOCR:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        agent5_paddle_ocr._PADDLE_RUNTIME_DISABLED = False
        agent5_paddle_ocr._PADDLE_UNAVAILABLE_REASON = ""
        agent5_paddle_ocr._ocr_engine.cache_clear()
        try:
            with patch.dict("sys.modules", {"paddleocr": SimpleNamespace(PaddleOCR=FakePaddleOCR)}):
                assert agent5_paddle_ocr._ocr_engine() is not None
        finally:
            agent5_paddle_ocr._PADDLE_RUNTIME_DISABLED = False
            agent5_paddle_ocr._PADDLE_UNAVAILABLE_REASON = ""
            agent5_paddle_ocr._ocr_engine.cache_clear()

        assert calls
        assert calls[0]["use_textline_orientation"] is True

    def test_choose_best_prefers_consensus(self):
        from data_ingestion.utils.agent5_paddleocr_scan import choose_best_house_number

        number, conf = choose_best_house_number([
            ("15230", 0.4),
            ("15230", 0.5),
            ("1520", 0.9),
        ])
        assert number == "15230"
        assert conf > 0

    def test_valid_house_number_rejects_years(self):
        from data_ingestion.utils.agent5_paddleocr_scan import valid_house_number

        assert not valid_house_number("2024")
        assert valid_house_number("15230")

    def test_valid_house_number_rejects_noise(self):
        from data_ingestion.utils.agent5_paddleocr_scan import valid_house_number

        assert not valid_house_number("001")
        assert not valid_house_number("002")
        assert valid_house_number("15020")

    def test_scan_needs_zoom_when_wrong_or_missing(self):
        from data_ingestion.utils.agent5_paddleocr_scan import scan_needs_zoom_refinement

        assert scan_needs_zoom_refinement(None, 0.0, "15275")
        assert scan_needs_zoom_refinement("001", 2.0, "15275")
        assert not scan_needs_zoom_refinement("15275", 1.2, "15275")

    def test_azure_focus_boxes_from_word_polygon(self):
        from data_ingestion.utils.agent5_paddleocr_scan import _collect_focus_boxes_from_azure

        vision = {
            "readResult": {
                "blocks": [
                    {
                        "lines": [
                            {
                                "text": "15275 NW RD",
                                "words": [
                                    {
                                        "text": "15275",
                                        "boundingPolygon": [
                                            {"x": 100, "y": 200},
                                            {"x": 180, "y": 200},
                                            {"x": 180, "y": 240},
                                            {"x": 100, "y": 240},
                                        ],
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        }
        boxes = _collect_focus_boxes_from_azure(vision, "15275", 640, 640)
        assert len(boxes) == 1
        assert boxes[0] == (100, 200, 180, 240)

    def test_scan_matches_expected(self):
        from data_ingestion.utils.agent5_paddleocr_scan import scan_matches_expected

        assert scan_matches_expected("15230", 1.2, "15230")
        assert not scan_matches_expected("1520", 1.2, "15230")

    def test_accumulator_summary(self):
        from data_ingestion.utils.agent5_paddleocr_scan import PaddleScanAccumulator

        acc = PaddleScanAccumulator(expected="15230")
        acc.best_number = "15230"
        acc.best_confidence = 2.1
        acc.best_view = "streetview_primary"
        summary = acc.summary()
        assert summary["paddleocr_scan_recognized"] == "15230"
        assert summary["paddleocr_scan_matched"] is True

    def test_azure_fallback_extracts_house_number(self):
        from data_ingestion.utils.agent5_paddleocr_scan import scan_house_number_from_azure_vision

        vision = {
            "readResult": {
                "blocks": [
                    {
                        "lines": [
                            {
                                "text": "15275 NW GAINESVILLE RD",
                                "words": [{"text": "15275", "confidence": 0.92}],
                            }
                        ]
                    }
                ]
            }
        }
        number, conf, count = scan_house_number_from_azure_vision(vision)
        assert number == "15275"
        assert conf > 0
        assert count >= 1

    def test_fast_focus_scan_stops_after_consecutive_empty_probes(self):
        from io import BytesIO

        from PIL import Image

        from data_ingestion.utils import agent5_paddleocr_scan

        calls = {"count": 0}

        def fake_run(_engine, _crop, *, fast=False):
            calls["count"] += 1
            return []

        buf = BytesIO()
        Image.new("RGB", (640, 480), color=(200, 200, 200)).save(buf, format="PNG")
        image_bytes = buf.getvalue()

        with patch("data_ingestion.utils.agent5_paddle_ocr._ocr_engine", return_value=object()):
            with patch.object(agent5_paddleocr_scan, "_run_paddle_on_bgr", side_effect=fake_run):
                number, conf, det_count = agent5_paddleocr_scan.scan_focused_local_zoom(
                    image_bytes,
                    expected="15290",
                    fast=True,
                )

        assert number is None
        assert conf == 0.0
        assert det_count == 0
        assert calls["count"] <= agent5_paddleocr_scan._FAST_LOCAL_ZOOM_STEPS

    def test_scan_streetview_image_uses_azure_when_paddle_missing(self):
        from data_ingestion.utils.agent5_paddleocr_scan import PaddleScanAccumulator

        vision = {
            "readResult": {
                "blocks": [
                    {
                        "lines": [
                            {
                                "text": "15290",
                                "words": [{"text": "15290", "confidence": 0.88}],
                            }
                        ]
                    }
                ]
            }
        }
        acc = PaddleScanAccumulator(expected="15290")
        with patch(
            "data_ingestion.utils.agent5_paddleocr_scan.scan_focused_local_zoom",
            return_value=(None, 0.0, 0),
        ):
            result = acc.scan_streetview_image("streetview_primary", b"fake", azure_vision=vision)
        assert result.house_number == "15290"
        assert acc.summary()["paddleocr_scan_recognized"] == "15290"

    def test_fatal_paddle_runtime_error_disables_paddle_fallback(self):
        from data_ingestion.utils import agent5_paddle_ocr, agent5_paddleocr_scan

        agent5_paddle_ocr._PADDLE_RUNTIME_DISABLED = False
        agent5_paddle_ocr._PADDLE_UNAVAILABLE_REASON = ""
        agent5_paddle_ocr._ocr_engine.cache_clear()
        try:
            with patch(
                "data_ingestion.utils.agent5_paddleocr_scan._enhance_variants",
                return_value=[object()],
            ):
                with patch(
                    "data_ingestion.utils.agent5_paddleocr_scan._run_ocr",
                    side_effect=RuntimeError(
                        "ConvertPirAttribute2RuntimeAttribute not support "
                        "pir::ArrayAttribute in onednn_instruction"
                    ),
                ):
                    detections = agent5_paddleocr_scan._run_paddle_on_bgr(
                        object(), object(), fast=True
                    )

            assert detections == []
            status = agent5_paddle_ocr.paddleocr_runtime_status()
            assert status["available"] is False
            assert "runtime failed" in status["reason"]
        finally:
            agent5_paddle_ocr._PADDLE_RUNTIME_DISABLED = False
            agent5_paddle_ocr._PADDLE_UNAVAILABLE_REASON = ""
            agent5_paddle_ocr._ocr_engine.cache_clear()

    def test_scan_streetview_image_keeps_paddle_view_name(self):
        from data_ingestion.utils.agent5_paddleocr_scan import PaddleScanAccumulator

        acc = PaddleScanAccumulator(expected="15290")
        with patch(
            "data_ingestion.utils.agent5_paddleocr_scan.scan_focused_local_zoom",
            return_value=("15290", 1.4, 2),
        ):
            result = acc.scan_streetview_image("streetview_primary", b"fake")

        assert result.view == "streetview_primary"
        assert acc.summary()["paddleocr_scan_view"] == "streetview_primary"

    def test_record_gpt_confirmed_read_fills_scan_column(self):
        from data_ingestion.utils.agent5_paddleocr_scan import PaddleScanAccumulator

        acc = PaddleScanAccumulator(expected="15290")
        acc.record_gpt_confirmed_read(
            {
                "house_number_visible": True,
                "house_number_text": "15290",
                "confidence": 0.9,
            }
        )
        summary = acc.summary()
        assert summary["paddleocr_scan_recognized"] == "15290"
        assert summary["paddleocr_scan_matched"] is True
        assert summary["paddleocr_scan_view"] == "gpt_confirmed"

    def test_record_gpt_confirmed_read_ignores_low_confidence(self):
        from data_ingestion.utils.agent5_paddleocr_scan import PaddleScanAccumulator

        acc = PaddleScanAccumulator(expected="15290")
        acc.record_gpt_confirmed_read(
            {
                "house_number_visible": True,
                "house_number_text": "15290",
                "confidence": 0.5,
            }
        )
        assert acc.summary()["paddleocr_scan_recognized"] == ""

    def test_record_confirmed_read_azure_match(self):
        from data_ingestion.utils.agent5_paddleocr_scan import PaddleScanAccumulator

        acc = PaddleScanAccumulator(expected="15020")
        acc.record_confirmed_read("15020", 0.90, view="streetview_primary_var1", scale=5.0)
        summary = acc.summary()
        assert summary["paddleocr_scan_recognized"] == "15020"
        assert summary["paddleocr_scan_matched"] is True
        assert summary["paddleocr_scan_view"] == "streetview_primary_var1"


class TestAgent5HouseNumbersOutput:
    def test_build_house_number_entry_from_scan(self):
        from data_ingestion.utils.agent5_house_numbers_output import build_house_number_entry

        entry = build_house_number_entry(
            {
                "status": "analyzed",
                "latitude": 29.367107,
                "longitude": -82.194973,
                "paddleocr_scan_recognized": "15020",
                "paddleocr_scan_confidence": 4.5,
                "paddleocr_scan_view": "streetview_primary_var1",
                "search_trace": [
                    {
                        "view": "streetview_primary_var1",
                        "heading": 350.0,
                        "fov": 30,
                    }
                ],
            },
            {"house_number": "15020"},
        )
        assert entry is not None
        assert entry["house_number"] == "15020"
        assert entry["image"] == "29.367107_-82.194973_sv_350.0_fov30_s2.jpg"

    def test_write_house_numbers_json(self, tmp_path):
        from data_ingestion.utils.agent5_house_numbers_output import write_house_numbers_json

        out = tmp_path / "house_numbers.json"
        write_house_numbers_json(
            [{"image": "a.jpg", "house_number": "15020", "confidence": 4.5}],
            out,
        )
        assert out.read_text(encoding="utf-8").strip().startswith("[")

    def test_image_filename_strips_azure_suffix(self):
        from data_ingestion.utils.agent5_house_numbers_output import build_house_number_entry

        entry = build_house_number_entry(
            {
                "status": "analyzed",
                "latitude": 29.370743,
                "longitude": -82.197097,
                "paddleocr_scan_recognized": "15275",
                "paddleocr_scan_confidence": 2.88,
                "paddleocr_scan_view": "streetview_primary_var1_azure",
                "search_trace": [
                    {
                        "view": "streetview_primary_var1",
                        "heading": 90.8,
                        "fov": 20,
                    }
                ],
            },
            {"house_number": "15275"},
        )
        assert entry is not None
        assert entry["image"] == "29.370743_-82.197097_sv_90.8_fov20_s2.jpg"


class TestAgent5GptDeferredMatch:
    def test_gpt_match_deferred_when_paddle_scan_still_needs_zoom(self):
        from data_ingestion.utils.agent5_iterative_search import iterative_house_number_search

        images: list = []
        variant_visions: list = []
        global_ocr_results: list = []
        paddle_scan = __import__(
            "data_ingestion.utils.agent5_paddleocr_scan", fromlist=["PaddleScanAccumulator"]
        ).PaddleScanAccumulator(expected="15290")

        calls: list[str] = []

        def fetch(_lat, _lon, _heading, fov):
            calls.append(f"fov={fov}")
            return b"jpeg" + b"x" * 20000, "url"

        def azure(_img):
            return {"readResult": {"blocks": []}}

        gpt_calls = {"n": 0}

        def gpt(_img, _expected, _address):
            gpt_calls["n"] += 1
            if gpt_calls["n"] == 1:
                return {
                    "house_number_visible": True,
                    "house_number_text": "15290",
                    "confidence": 0.9,
                    "house_direction": "center",
                    "recommended_fov": 25,
                    "recommended_heading_delta_deg": 0,
                    "recommended_move_meters": {"forward": 0, "lateral": 0},
                }
            return None

        with patch(
            "data_ingestion.utils.agent5_iterative_search.gpt_vision_enabled",
            return_value=True,
        ), patch(
            "data_ingestion.utils.agent5_iterative_search.analyze_house_number_view",
            side_effect=gpt,
        ), patch(
            "data_ingestion.utils.agent5_iterative_search.clarify_image",
            side_effect=lambda b: b,
        ), patch(
            "data_ingestion.utils.agent5_iterative_search.detect_house_number_with_paddle",
            return_value=(False, 0.0, ""),
        ), patch(
            "data_ingestion.utils.agent5_iterative_search.detect_house_number_with_tesseract",
            return_value=(False, 0.0, ""),
        ), patch(
            "data_ingestion.utils.agent5_paddleocr_scan.scan_focused_local_zoom",
            return_value=(None, 0.0, 0),
        ):
            state = iterative_house_number_search(
                lat=29.371214,
                lon=-82.197885,
                base_heading=269.9,
                expected_house_number="15290",
                address="15290 NW GAINESVILLE RD",
                images=images,
                variant_visions=variant_visions,
                global_ocr_results=global_ocr_results,
                fetch_streetview_image=fetch,
                azure_analyze=azure,
                extract_ocr_text=lambda _v: "",
                detect_house_number=lambda _v, _e, _t: (False, 0.0),
                paddle_scan=paddle_scan,
                max_iterations=1,
            )

        assert state["found"] is True
        assert len(calls) > 1, "expected tighter FOV probes after deferred GPT match"
        assert paddle_scan.summary()["paddleocr_scan_recognized"] == "15290"


class TestAgent5PrimaryImageryFallback:
    def test_pick_primary_prefers_streetview_over_satellite(self):
        from data_ingestion.utils.agent5_vision import _pick_primary_streetview_image

        images = [
            ("satellite", b"sat", "sat-url"),
            ("streetview_oblique_left", b"sv" + b"x" * 20000, "sv-url"),
        ]
        img, img_type = _pick_primary_streetview_image(images)
        assert img.startswith(b"sv")
        assert img_type == "streetview_oblique_left"

    def test_pick_primary_skips_placeholder(self):
        from data_ingestion.utils.agent5_vision import _is_streetview_placeholder, _pick_primary_streetview_image

        placeholder = b"x" * 9000
        assert _is_streetview_placeholder(placeholder)
        images = [
            ("streetview_primary", placeholder, "url"),
            ("streetview_oblique_left", b"real-image-bytes-here" + b"x" * 20000, "url2"),
        ]
        img, img_type = _pick_primary_streetview_image(images)
        assert img_type == "streetview_oblique_left"


class TestAgent5AnalysisMode:
    def test_offline_mode_forces_qwen_and_disables_azure(self):
        from data_ingestion.utils import agent5_vision

        captured = {}

        def fake_search(**kwargs):
            captured.update(kwargs)
            captured["azure_probe"] = kwargs["azure_analyze"](b"img")
            return {"found": False, "iterations_used": 0, "search_trace": []}

        record = {
            "latitude": 1.0,
            "longitude": 2.0,
            "house_number": "123",
            "raw_address": "123 MAIN ST",
        }

        with patch("data_ingestion.utils.agent5_vision.streetview_metadata", return_value=(True, {"location": {"lat": 1.0, "lng": 2.0}})), \
            patch("data_ingestion.utils.agent5_vision.iterative_house_number_search", side_effect=fake_search), \
            patch("data_ingestion.utils.agent5_vision.fetch_esri_satellite_image", return_value=(b"esri", "esri")), \
            patch("data_ingestion.utils.agent5_vision.fetch_satellite_image", return_value=(None, "")), \
            patch("data_ingestion.utils.agent5_vision.parse_vision_output", return_value={"structure_type": "SFH", "confidence": 0}), \
            patch("data_ingestion.utils.agent5_vision._analyze_for_structure", return_value=None):
            agent5_vision.analyze_address(record, agent_options={"analysis_mode": "offline"})

        assert captured["azure_probe"] is None
        assert captured["llm_provider"] == "offline"
        assert captured["ollama_vision_model"] == "qwen2.5vl:latest"
        assert captured["gpt_enabled"] is True

    def test_online_mode_forces_azure_and_online_llm(self):
        from data_ingestion.utils import agent5_vision

        captured = {}

        def fake_search(**kwargs):
            captured.update(kwargs)
            captured["azure_probe"] = kwargs["azure_analyze"](b"img")
            return {"found": False, "iterations_used": 0, "search_trace": []}

        record = {
            "latitude": 1.0,
            "longitude": 2.0,
            "house_number": "123",
            "raw_address": "123 MAIN ST",
        }

        with patch("data_ingestion.utils.agent5_vision.streetview_metadata", return_value=(True, {"location": {"lat": 1.0, "lng": 2.0}})), \
            patch("data_ingestion.utils.agent5_vision.iterative_house_number_search", side_effect=fake_search), \
            patch("data_ingestion.utils.agent5_vision.azure_vision_analyze", return_value={"readResult": {"blocks": []}}), \
            patch("data_ingestion.utils.agent5_vision.fetch_esri_satellite_image", return_value=(b"esri", "esri")), \
            patch("data_ingestion.utils.agent5_vision.fetch_satellite_image", return_value=(None, "")), \
            patch("data_ingestion.utils.agent5_vision.parse_vision_output", return_value={"structure_type": "SFH", "confidence": 0}), \
            patch("data_ingestion.utils.agent5_vision._analyze_for_structure", return_value=None):
            agent5_vision.analyze_address(
                record,
                agent_options={
                    "analysis_mode": "online",
                    "azure_vision": False,
                    "llm_provider": "offline",
                },
            )

        assert captured["azure_probe"] == {"readResult": {"blocks": []}}
        assert captured["llm_provider"] == "online"
        assert captured["gpt_enabled"] is True


class TestAgent5SVMeta:
    def test_sv_meta_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "OK", "pano_id": "abc123"}

        with patch("data_ingestion.utils.agent5_vision._google_key", return_value="test-key"):
            with patch("data_ingestion.utils.agent5_vision.requests.get", return_value=mock_resp):
                with patch("data_ingestion.utils.agent5_vision.Path.write_text"):
                    with patch("data_ingestion.utils.agent5_vision.Path.is_file", return_value=False):
                        ok, data = _sv_meta(40.7128, -74.0060)

        assert ok is True
        assert data.get("pano_id") == "abc123"

    def test_sv_meta_not_available(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ZERO_RESULTS"}

        with patch("data_ingestion.utils.agent5_vision._google_key", return_value="test-key"):
            with patch("data_ingestion.utils.agent5_vision.requests.get", return_value=mock_resp):
                with patch("data_ingestion.utils.agent5_vision.Path.write_text"):
                    with patch("data_ingestion.utils.agent5_vision.Path.is_file", return_value=False):
                        ok, data = _sv_meta(0.0, 0.0)

        assert ok is False

    def test_sv_meta_network_error(self):
        with patch("data_ingestion.utils.agent5_vision._google_key", return_value="test-key"):
            with patch("data_ingestion.utils.agent5_vision.requests.get", side_effect=Exception("network error")):
                with patch("data_ingestion.utils.agent5_vision.Path.is_file", return_value=False):
                    ok, data = _sv_meta(40.0, -74.0)
        assert ok is False
        assert data == {}


class TestAgent5ConfidenceScoring:
    def test_house_number_confidence_drives_agent5_verdict_and_main_score(self):
        from data_ingestion.utils.agent5_vision import _apply_house_number_verdict

        result = _apply_house_number_verdict({
            "status": "analyzed",
            "confidence": 88,
            "house_number_conf": 95,
        })

        assert result["status"] == "ACCEPT"
        assert result["confidence"] == 95
        assert result["image_confidence"] == 88
        assert result["analysis_status"] == "analyzed"

    def test_low_house_number_confidence_is_review_even_with_high_image_score(self):
        from data_ingestion.utils.agent5_vision import _apply_house_number_verdict

        result = _apply_house_number_verdict({
            "status": "analyzed",
            "confidence": 95,
            "house_number_conf": 70,
        })

        assert result["status"] == "REVIEW"
        assert result["confidence"] == 70
        assert result["image_confidence"] == 95

    def test_high_conf_house_number_floors_confidence_at_90(self):
        from data_ingestion.utils.agent5_vision import parse_vision_output

        vision = {
            "tagsResult": {"values": [{"name": "house", "confidence": 0.4}]},
            "objectsResult": {"values": []},
            "readResult": {"blocks": []},
        }
        result = parse_vision_output(
            vision,
            {"address_type": "Residential", "house_number": "1020"},
            ocr_match_found=True,
            house_number_conf=0.95,
        )
        assert result["confidence"] >= 90
        assert result["high_conf_house_number"] is True

    def test_parse_vision_streetview_sfh_floor_90(self):
        from data_ingestion.utils.agent5_vision import parse_vision_output

        vision = {
            "tagsResult": {"values": [{"name": "house", "confidence": 0.92}]},
            "objectsResult": {"values": []},
            "readResult": {"blocks": []},
        }
        result = parse_vision_output(
            vision,
            {"address_type": "Residential", "house_number": "415"},
            imagery_source="streetview",
        )
        assert result["confidence"] >= 90

    def test_parse_vision_residential_streetview_unclear_floor_90(self):
        from data_ingestion.utils.agent5_vision import parse_vision_output

        vision = {
            "tagsResult": {"values": [{"name": "road", "confidence": 0.99}]},
            "objectsResult": {"values": []},
            "readResult": {"blocks": []},
        }
        result = parse_vision_output(
            vision,
            {"address_type": "Residential", "house_number": "805"},
            imagery_source="streetview",
        )
        assert result["confidence"] >= 90
        assert result["structure_type_detail"] == "detached_house"

    def test_parse_vision_no_signal_satellite_zero(self):
        from data_ingestion.utils.agent5_vision import parse_vision_output

        vision = {
            "tagsResult": {"values": []},
            "objectsResult": {"values": []},
            "readResult": {"blocks": []},
        }
        result = parse_vision_output(
            vision,
            {"address_type": "Residential"},
            imagery_source="satellite",
        )
        assert result["confidence"] == 0
        assert result["image_quality"] == "no_structure"

    def test_paddle_ocr_house_number_can_floor_confidence(self):
        from data_ingestion.utils import agent5_vision

        record = {
            "latitude": 1.0,
            "longitude": 2.0,
            "house_number": "123",
            "address_type": "Residential",
        }
        vision = {
            "tagsResult": {"values": [{"name": "house", "confidence": 0.4}]},
            "objectsResult": {"values": []},
            "readResult": {"blocks": []},
        }
        from io import BytesIO
        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", (32, 32), "white").save(buf, format="JPEG")
        image_bytes = buf.getvalue()

        with patch("data_ingestion.utils.agent5_vision.streetview_metadata", return_value=(True, {"location": {"lat": 1.0, "lng": 2.0}})):
            with patch("data_ingestion.utils.agent5_vision.fetch_streetview_image", return_value=(image_bytes, "url")):
                with patch("data_ingestion.utils.agent5_vision.fetch_esri_satellite_image", return_value=(None, "esri")):
                    with patch("data_ingestion.utils.agent5_vision.fetch_satellite_image", return_value=(None, "sat")):
                        with patch("data_ingestion.utils.agent5_vision.azure_vision_analyze", return_value=vision):
                            with patch("data_ingestion.utils.agent5_vision.detect_house_number_with_paddle", return_value=(True, 0.96, "123")):
                                result = agent5_vision.analyze_address(
                                    record,
                                    sleep_seconds=0,
                                    agent_options={"fast_mode": False},
                                )

        assert result["confidence"] >= 90
        assert result["paddle_ocr_match_found"] is True
        assert result["high_conf_house_number"] is True


class TestAgent5RawMetadata:
    def test_sync_writes_google_street_view_block_when_confidence_at_least_90(self):
        from data_ingestion.utils.agent5_metadata import (
            GOOGLE_STREET_VIEW_METADATA_KEY,
            sync_agent5_streetview_in_raw_metadata,
        )

        addr = MagicMock()
        addr.raw_address = "416 MAPLE ST"
        addr.source_raw_address = "416 MAPLE ST"
        addr.source_latitude = 32.0937221
        addr.source_longitude = -85.2488023
        addr.latitude = 32.0937221
        addr.longitude = -85.2488023
        addr.city = "Glennville"
        addr.state = "AL"
        addr.zip_code = "36744"
        addr.validated_raw_address = None
        addr.raw_metadata = {"country_code": "US"}

        record = {
            "latitude": 32.0937221,
            "longitude": -85.2488023,
            "address": "416 MAPLE ST",
            "city": "Glennville",
            "state": "AL",
            "zip_code": "36744",
            "house_number": "416",
            "address_type": "Residential",
        }
        result = {
            "status": "analyzed",
            "confidence": 92,
            "latitude": 32.0937221,
            "longitude": -85.2488023,
            "structure_type": "SFH",
            "structure_type_detail": "detached_house",
            "imagery_source": "streetview",
            "ocr_match_found": True,
            "high_conf_house_number": True,
            "image_quality": "good",
        }

        updated = sync_agent5_streetview_in_raw_metadata(addr, record, result)

        assert updated is True
        assert "old" in addr.raw_metadata
        assert GOOGLE_STREET_VIEW_METADATA_KEY in addr.raw_metadata
        sv = addr.raw_metadata[GOOGLE_STREET_VIEW_METADATA_KEY]
        assert sv["confidence"] == "92"
        assert sv["status"] == "MATCH"
        assert sv["street_number_name"] == "416 MAPLE ST"
        assert sv["latitude"] == 32.0937221
        assert sv["LONGITUDE"] == str(-85.2488023)
        assert "remarks" in sv

    def test_sync_skips_when_confidence_below_90(self):
        from data_ingestion.utils.agent5_metadata import sync_agent5_streetview_in_raw_metadata

        addr = MagicMock()
        addr.raw_address = "416 MAPLE ST"
        addr.raw_metadata = {}

        updated = sync_agent5_streetview_in_raw_metadata(
            addr,
            {"latitude": 1.0, "longitude": 2.0, "address": "416 MAPLE ST", "house_number": "416"},
            {"confidence": 70, "status": "analyzed", "latitude": 1.0, "longitude": 2.0},
        )

        assert updated is False
        assert "google street view addresses" not in (addr.raw_metadata or {})

    def test_sync_preserves_existing_old_block(self):
        from data_ingestion.utils.agent5_metadata import sync_agent5_streetview_in_raw_metadata

        existing_old = {
            "ADDRESS": "3QV2+FF Glennville, AL, USA",
            "LATITUDE": "32.0937221",
            "LONGITUDE": "-85.2488022999999",
            "street_number_name": "416 MAPLE ST",
            "zip_postal_code": "",
            "latitude": 32.0937221,
            "longitude": -85.2488022999999,
            "city_state": "",
            "country_code": "",
        }
        addr = MagicMock()
        addr.raw_address = "different upload"
        addr.raw_metadata = {"old": existing_old}

        sync_agent5_streetview_in_raw_metadata(
            addr,
            {"latitude": 32.1, "longitude": -85.2, "address": "416 MAPLE ST", "house_number": "416"},
            {
                "confidence": 90,
                "status": "analyzed",
                "latitude": 32.1,
                "longitude": -85.2,
                "ocr_match_found": False,
                "image_quality": "partial",
            },
        )

        assert addr.raw_metadata["old"] == existing_old
        assert addr.raw_metadata["google street view addresses"]["status"] == "MISMATCH"

    def test_sync_sets_mismatch_when_agent0_coord_validation_fails(self):
        from data_ingestion.utils.agent5_metadata import sync_agent5_streetview_in_raw_metadata

        addr = MagicMock()
        addr.raw_address = "805 ADDERTON ST"
        addr.source_raw_address = "805 ADDERTON ST"
        addr.source_latitude = 32.085297
        addr.source_longitude = -90.244013
        addr.latitude = 32.085297
        addr.longitude = -90.244013
        addr.city = "Glennville"
        addr.state = "AL"
        addr.zip_code = "36744"
        addr.validated_raw_address = None
        addr.validated_latitude = 32.085297
        addr.validated_longitude = -84.2492642
        addr.coord_address_match_status = "MISMATCH"
        addr.coord_address_distance_m = 520000.0
        addr.coord_address_validation_notes = "Address vs stored coordinates differ by 520000.0m"
        addr.raw_metadata = {
            "country_code": "US",
            "address_validation": {
                "match_status": "MISMATCH",
                "distance_m": 520000.0,
                "notes": "Address vs stored coordinates differ by 520000.0m",
            },
        }

        sync_agent5_streetview_in_raw_metadata(
            addr,
            {
                "latitude": 32.085297,
                "longitude": -84.2492642,
                "address": "805 ADDERTON ST",
                "house_number": "805",
            },
            {
                "confidence": 90,
                "status": "analyzed",
                "latitude": 32.085297,
                "longitude": -84.2492642,
                "ocr_match_found": True,
                "high_conf_house_number": True,
            },
        )

        sv = addr.raw_metadata["google street view addresses"]
        assert sv["status"] == "MISMATCH"
        assert sv["longitude"] == -84.2492642
        assert "uploaded coordinates do not match validated address" in sv["remarks"]
        assert "520000.0m apart" in sv["remarks"]

    def test_sync_sets_mismatch_when_old_and_analyzed_coords_differ(self):
        from data_ingestion.utils.agent5_metadata import sync_agent5_streetview_in_raw_metadata

        existing_old = {
            "street_number_name": "416 MAPLE ST",
            "latitude": 32.0937221,
            "longitude": -85.2488022999999,
            "LATITUDE": "32.0937221",
            "LONGITUDE": "-85.2488022999999",
        }
        addr = MagicMock()
        addr.raw_address = "416 MAPLE ST"
        addr.coord_address_match_status = None
        addr.coord_address_distance_m = None
        addr.coord_address_validation_notes = None
        addr.raw_metadata = {"old": existing_old}

        sync_agent5_streetview_in_raw_metadata(
            addr,
            {"latitude": 32.1, "longitude": -85.2, "address": "416 MAPLE ST", "house_number": "416"},
            {
                "confidence": 92,
                "status": "analyzed",
                "latitude": 32.1,
                "longitude": -85.2,
                "ocr_match_found": True,
                "high_conf_house_number": True,
            },
        )

        sv = addr.raw_metadata["google street view addresses"]
        assert sv["status"] == "MISMATCH"
        assert "coordinates corrected from upload to validated location" in sv["remarks"]

    def test_sync_uses_validated_coords_in_streetview_block(self):
        from data_ingestion.utils.agent5_metadata import sync_agent5_streetview_in_raw_metadata

        addr = MagicMock()
        addr.raw_address = "805 ADDERTON ST"
        addr.source_raw_address = "805 ADDERTON ST"
        addr.source_latitude = 32.085297
        addr.source_longitude = -90.244013
        addr.latitude = 32.085297
        addr.longitude = -90.244013
        addr.validated_latitude = 32.085297
        addr.validated_longitude = -84.2492642
        addr.validated_raw_address = "805 ADDERTON ST, Glennville, AL"
        addr.city = "Glennville"
        addr.state = "AL"
        addr.zip_code = "36744"
        addr.coord_address_match_status = "MISMATCH"
        addr.coord_address_distance_m = 520000.0
        addr.coord_address_validation_notes = None
        addr.raw_metadata = {
            "old": {
                "street_number_name": "805 ADDERTON ST",
                "latitude": 32.085297,
                "longitude": -90.244013,
            },
            "new": {
                "street_number_name": "805 ADDERTON ST, Glennville, AL",
                "latitude": 32.085297,
                "longitude": -84.2492642,
            },
        }

        sync_agent5_streetview_in_raw_metadata(
            addr,
            {
                "latitude": 32.085297,
                "longitude": -84.2492642,
                "address": "805 ADDERTON ST, Glennville, AL",
                "house_number": "805",
            },
            {
                "confidence": 92,
                "status": "analyzed",
                "latitude": 32.085297,
                "longitude": -84.2492642,
                "ocr_match_found": True,
                "high_conf_house_number": True,
            },
        )

        sv = addr.raw_metadata["google street view addresses"]
        assert sv["latitude"] == 32.085297
        assert sv["longitude"] == -84.2492642
        assert sv["LONGITUDE"] == "-84.2492642"
        assert sv["status"] == "MISMATCH"
        assert "uploaded coordinates do not match validated address" in sv["remarks"]

    def test_fast_mode_runs_full_iterative_house_number_search_without_early_exit(self):
        """Fast mode must still run the complete iterative search ladder."""
        from io import BytesIO

        from PIL import Image

        from data_ingestion.utils import agent5_vision

        buf = BytesIO()
        Image.new("RGB", (32, 32), "white").save(buf, format="JPEG")
        image_bytes = buf.getvalue()

        record = {
            "latitude": 32.09,
            "longitude": -84.24,
            "house_number": "514",
            "address_type": "Residential",
        }
        vision = {
            "tagsResult": {"values": [{"name": "house", "confidence": 0.5}]},
            "objectsResult": {"values": []},
            "readResult": {"blocks": []},
        }
        variant_calls: list[str] = []

        def _fetch_variant(lat, lon, heading, image_type, angle_offset, fov, fetch_fn):
            variant_calls.append(image_type)
            return image_type, image_bytes, "url"

        with patch("data_ingestion.utils.agent5_vision.streetview_metadata", return_value=(True, {"location": {"lat": 32.09, "lng": -84.24}})):
            with patch("data_ingestion.utils.agent5_vision.fetch_streetview_image", return_value=(image_bytes, "url")) as fetch_mock:
                with patch("data_ingestion.utils.agent5_vision.fetch_variant", side_effect=_fetch_variant):
                    with patch("data_ingestion.utils.agent5_vision.fetch_esri_satellite_image", return_value=(None, "")):
                        with patch("data_ingestion.utils.agent5_vision.fetch_satellite_image", return_value=(None, "")):
                            with patch("data_ingestion.utils.agent5_vision.azure_vision_analyze", return_value=vision):
                                with patch("data_ingestion.utils.agent5_vision.detect_house_number", return_value=(False, 0.0)):
                                    with patch("data_ingestion.utils.agent5_vision.detect_house_number_with_tesseract", return_value=(False, 0.0, "")):
                                        agent5_vision.analyze_address(
                                            record,
                                            agent_options={"fast_mode": True},
                                        )

        searched_fovs = [call.args[3] for call in fetch_mock.call_args_list]
        assert searched_fovs == [60, 48, 38]
        assert variant_calls == ["streetview_oblique_left", "streetview_oblique_right"]


class TestAgent5SatelliteImagery:
    def test_hybrid_map_label_picks_number_nearest_center(self):
        from io import BytesIO

        from PIL import Image

        from data_ingestion.utils.agent5_image_utils import analyze_hybrid_map_labels

        buf = BytesIO()
        Image.new("RGB", (1280, 1280), "white").save(buf, format="JPEG")
        image_bytes = buf.getvalue()

        vision = {
            "readResult": {
                "blocks": [{
                    "lines": [
                        {
                            "text": "14810",
                            "words": [{
                                "text": "14810",
                                "confidence": 0.9,
                                "boundingBox": [900, 200, 980, 200, 980, 240, 900, 240],
                            }],
                        },
                        {
                            "text": "14700",
                            "words": [{
                                "text": "14700",
                                "confidence": 0.9,
                                "boundingBox": [620, 620, 700, 620, 700, 660, 620, 660],
                            }],
                        },
                    ],
                }],
            },
        }

        info = analyze_hybrid_map_labels(vision, image_bytes, "14700")
        assert info["center_label"] == "14700"
        assert info["matches_expected"] is True
        assert info["status"] == "match"
        assert "14810" in info["visible_numbers"]

    def test_analyze_address_fetches_satellite_even_when_street_view_available(self):
        from PIL import Image

        from data_ingestion.utils import agent5_vision

        buf = BytesIO()
        Image.new("RGB", (32, 32), "white").save(buf, format="JPEG")
        image_bytes = buf.getvalue()

        record = {
            "latitude": 29.37,
            "longitude": -82.20,
            "house_number": "15251",
            "address_type": "Residential",
        }
        vision = {
            "tagsResult": {"values": [{"name": "house", "confidence": 0.5}]},
            "objectsResult": {"values": []},
            "readResult": {"blocks": []},
        }

        with patch("data_ingestion.utils.agent5_vision.streetview_metadata", return_value=(True, {"location": {"lat": 29.37, "lng": -82.20}})):
            with patch("data_ingestion.utils.agent5_vision.fetch_streetview_image", return_value=(image_bytes, "sv-url")):
                with patch("data_ingestion.utils.agent5_vision.fetch_esri_satellite_image", return_value=(image_bytes, "esri-url")) as esri_mock:
                    with patch("data_ingestion.utils.agent5_vision.fetch_satellite_image", return_value=(image_bytes, "sat-url")) as sat_mock:
                        with patch("data_ingestion.utils.agent5_vision.azure_vision_analyze", return_value=vision):
                            with patch("data_ingestion.utils.agent5_vision.detect_house_number", return_value=(True, 0.95)):
                                result = agent5_vision.analyze_address(
                                    record,
                                    sleep_seconds=0,
                                    agent_options={"fast_mode": True},
                                )

        sat_mock.assert_called_once()
        esri_mock.assert_called_once()
        assert result["imagery_source"] == "streetview"
        assert result["images_fetched"] >= 2

    def test_analyze_address_detects_house_number_from_satellite_ocr(self):
        from PIL import Image

        from data_ingestion.utils import agent5_vision

        buf = BytesIO()
        Image.new("RGB", (32, 32), "white").save(buf, format="JPEG")
        image_bytes = buf.getvalue()

        record = {
            "latitude": 29.37,
            "longitude": -82.20,
            "house_number": "15251",
            "address_type": "Residential",
        }
        street_vision = {
            "tagsResult": {"values": [{"name": "house", "confidence": 0.5}]},
            "objectsResult": {"values": []},
            "readResult": {"blocks": []},
        }
        satellite_vision = {
            "tagsResult": {"values": [{"name": "house", "confidence": 0.4}]},
            "objectsResult": {"values": []},
            "readResult": {
                "blocks": [{
                    "lines": [{
                        "text": "15251",
                        "words": [{"text": "15251", "confidence": 0.96}],
                    }],
                }],
            },
        }

        def _analyze_side_effect(image_bytes):
            return satellite_vision if image_bytes == image_bytes else street_vision

        with patch("data_ingestion.utils.agent5_vision.streetview_metadata", return_value=(False, {})):
            with patch("data_ingestion.utils.agent5_vision.fetch_satellite_image", return_value=(image_bytes, "sat-url")):
                with patch("data_ingestion.utils.agent5_vision.fetch_esri_satellite_image", return_value=(None, "")):
                    with patch("data_ingestion.utils.agent5_vision.azure_vision_analyze", side_effect=lambda img: satellite_vision):
                        result = agent5_vision.analyze_address(
                            record,
                            sleep_seconds=0,
                            agent_options={"street_view": False, "fast_mode": True},
                        )

        assert result["ocr_match_found"] is True
        assert result["winning_step"] == "satellite"
        assert result["imagery_source"] == "satellite"


class TestAgent5CoordinateResolution:
    def test_resolve_prefers_final_resolution_over_upload(self):
        from data_ingestion.utils.agent5_input import resolve_agent5_coordinates

        addr = MagicMock()
        addr.latitude = 32.085297
        addr.longitude = -90.244013
        addr.validated_latitude = None
        addr.validated_longitude = None
        addr.raw_metadata = {
            "final_resolution": {
                "latitude": 32.085297,
                "longitude": -84.2492642,
                "address": "805 ADDERTON ST",
            }
        }

        lat, lon, source = resolve_agent5_coordinates(addr, None)
        assert lat == 32.085297
        assert lon == -84.2492642
        assert source == "final_resolution"

    def test_resolve_uses_agent1_smarty_when_no_final_resolution(self):
        from data_ingestion.utils.agent5_input import resolve_agent5_coordinates

        addr = MagicMock()
        addr.latitude = 32.085297
        addr.longitude = -90.244013
        addr.validated_latitude = 32.085297
        addr.validated_longitude = -84.24
        addr.raw_metadata = {}
        a1 = MagicMock()
        a1.smarty_lat = 32.0853
        a1.smarty_lon = -84.249

        lat, lon, source = resolve_agent5_coordinates(addr, a1)
        assert source == "agent1_smarty"
        assert lon == -84.249


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 6 — run_agent6_for_job with mocked DB
# ══════════════════════════════════════════════════════════════════════════════

from data_ingestion.agents.agent6_finalization import (
    run_agent6_for_job,
)


class TestAgent6RunWithMockDB:
    """Test run_agent6_for_job using fully mocked SQLAlchemy session."""

    def _make_mock_session(self, addresses, a1_results=None, agent_results=None):
        session = MagicMock()

        # AgentTable.ensure: return None (not found → will add)
        session.execute.return_value.scalar_one_or_none.return_value = None
        session.add = MagicMock()
        session.commit = MagicMock()
        session.rollback = MagicMock()
        session.close = MagicMock()
        session.flush = MagicMock()

        # scalars().all(): addresses, then (when non-empty) a1_results + 4 agent tables
        scalars_calls = [
            MagicMock(all=MagicMock(return_value=addresses)),
        ]
        if addresses:
            scalars_calls.append(MagicMock(all=MagicMock(return_value=a1_results or [])))
            for _ in range(4):
                scalars_calls.append(MagicMock(all=MagicMock(return_value=[])))

        session.scalars.side_effect = scalars_calls
        return session

    def _make_address(self, id=1, job_id="job1", raw_address="123 Main St"):
        addr = MagicMock()
        addr.id = id
        addr.job_id = job_id
        addr.raw_address = raw_address
        return addr

    def _make_a1(self, address_id=1, status="AUTO_ACCEPT", score=85):
        a1 = MagicMock()
        a1.address_id = address_id
        a1.validation_status = status
        a1.confidence_score = score
        a1.exception_reason = None
        return a1

    def test_run_returns_summary_dict(self):
        addr = self._make_address(id=1)
        a1 = self._make_a1(address_id=1)
        session = self._make_mock_session([addr], [a1])

        with patch("data_ingestion.agents.agent6_finalization.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent6_finalization._upsert"):
                with patch("data_ingestion.agents.agent6_finalization._ensure_table"):
                    summary = run_agent6_for_job("job1")

        assert "total" in summary
        assert "high" in summary
        assert "medium" in summary
        assert "low" in summary
        assert "skip" in summary

    def test_rejected_address_counted_as_skip(self):
        addr = self._make_address(id=1)
        a1 = self._make_a1(address_id=1, status="REJECT", score=5)
        session = self._make_mock_session([addr], [a1])

        with patch("data_ingestion.agents.agent6_finalization.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent6_finalization._upsert"):
                with patch("data_ingestion.agents.agent6_finalization._ensure_table"):
                    summary = run_agent6_for_job("job1")

        assert summary["skip"] == 1
        assert summary["total"] == 1

    def test_high_priority_address_counted_correctly(self):
        addr = self._make_address(id=1)
        a1 = self._make_a1(address_id=1, status="AUTO_ACCEPT", score=90)
        session = self._make_mock_session([addr], [a1])

        # Patch _synthesize to return HIGH priority
        def _fake_synthesize(**kwargs):
            return {
                "status": "classified",
                "final_structure_type": "MDU_LARGE",
                "final_is_mdu": True,
                "final_unit_count": 10,
                "final_confidence": 85,
                "ftth_priority": "HIGH",
                "validation_summary": "test",
                "a1_status": "AUTO_ACCEPT",
                "a1_score": 90,
                "vote_pool": ["MDU"],
                "a2_geocoded": False,
                "a2_location_type": "",
                "a3_land_use": "",
                "a4_structure": "MDU",
                "a4_confidence": 80,
                "a5_structure": "MDU",
                "a5_confidence": 80,
                "imagery_source": "street_view",
            }

        with patch("data_ingestion.agents.agent6_finalization.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent6_finalization._upsert"):
                with patch("data_ingestion.agents.agent6_finalization._ensure_table"):
                    with patch("data_ingestion.agents.agent6_finalization._synthesize",
                               side_effect=_fake_synthesize):
                        summary = run_agent6_for_job("job1")

        assert summary["high"] == 1

    def test_progress_callback_called(self):
        addr = self._make_address(id=1)
        session = self._make_mock_session([addr])

        calls = []
        def cb(done, total):
            calls.append((done, total))

        with patch("data_ingestion.agents.agent6_finalization.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent6_finalization._upsert"):
                with patch("data_ingestion.agents.agent6_finalization._ensure_table"):
                    run_agent6_for_job("job1", progress_callback=cb)

        assert len(calls) == 1
        assert calls[0] == (1, 1)

    def test_empty_job_returns_zero_counts(self):
        session = self._make_mock_session([])

        with patch("data_ingestion.agents.agent6_finalization.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent6_finalization._upsert"):
                with patch("data_ingestion.agents.agent6_finalization._ensure_table"):
                    summary = run_agent6_for_job("job_empty")

        assert summary["total"] == 0
        assert summary["high"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 2 — run_agent2_for_job with mocked DB + HTTP
# ══════════════════════════════════════════════════════════════════════════════

from data_ingestion.agents.agent2_geocoding import run_agent2_for_job, run_geocoding_for_job


class TestAgent2RunWithMockDB:
    def _make_session(self, addresses, a1_results=None):
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        session.add = MagicMock()
        session.commit = MagicMock()
        session.rollback = MagicMock()
        session.close = MagicMock()
        session.flush = MagicMock()

        session.scalars.side_effect = [
            MagicMock(all=MagicMock(return_value=addresses)),
            MagicMock(all=MagicMock(return_value=a1_results or [])),
        ]
        return session

    def _make_address(self, id=1, raw_address="123 Main St", lat=None, lon=None):
        addr = MagicMock()
        addr.id = id
        addr.raw_address = raw_address
        addr.latitude = lat
        addr.longitude = lon
        addr.city = None
        addr.state = None
        addr.zip_code = None
        addr.raw_metadata = {}
        addr.reverse_geocode_confidence_score = None
        addr.validated_raw_address = None
        addr.validated_latitude = None
        addr.validated_longitude = None
        addr.source_latitude = None
        addr.source_longitude = None
        return addr

    def test_run_skips_empty_address(self):
        addr = self._make_address(raw_address="")
        session = self._make_session([addr])

        with patch("data_ingestion.agents.agent2_geocoding.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent2_geocoding._upsert"):
                with patch("data_ingestion.agents.agent2_geocoding._ensure_table"):
                    summary = run_agent2_for_job("job1")

        assert summary["skipped"] == 1
        assert summary["geocoded"] == 0

    def test_run_geocodes_valid_address(self):
        addr = self._make_address(raw_address="123 Main St, NY")
        session = self._make_session([addr])

        geo_result = {
            "formatted_address": "123 Main St, New York, NY",
            "latitude": 40.7, "longitude": -74.0,
            "location_type": "ROOFTOP", "place_id": "xyz", "confidence": 95,
        }

        with patch("data_ingestion.agents.agent2_geocoding.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent2_geocoding._geocode_with_fallback", return_value=(geo_result, [], {})):
                with patch("data_ingestion.agents.agent2_geocoding._upsert"):
                    with patch("data_ingestion.agents.agent2_geocoding._ensure_table"):
                        summary = run_agent2_for_job("job1")

        assert summary["geocoded"] == 1
        assert summary["failed"] == 0

    @pytest.mark.parametrize(
        ("selected_direction", "selected_provider", "reverse_pct", "forward_pct"),
        [
            ("forward", "GOOGLE_FORWARD", 40, 100),
            ("reverse", "GOOGLE_REVERSE", 100, 40),
        ],
    )
    def test_coordinate_match_is_accepted_at_100_without_forward_geocoding(
        self,
        selected_direction,
        selected_provider,
        reverse_pct,
        forward_pct,
    ):
        addr = self._make_address(
            raw_address="4910 NW 152ND LN",
            lat=29.370049,
            lon=-82.2061220000017,
        )
        addr.coord_address_match_status = "MATCH"
        addr.validated_raw_address = "4910 NW 152nd Ln, Reddick, FL 32686, USA"
        addr.validated_latitude = 29.370039
        addr.validated_longitude = -82.2061066
        addr.reverse_geocode_confidence_score = 35
        addr.raw_metadata = {
            "ADDRESS": "4910 NW 152ND LN",
            "address_validation": {
                "match_status": "MATCH",
                "confidence_score": 100,
                "forward_address_match_percent": forward_pct,
                "reverse_address_match_percent": reverse_pct,
                "selected_direction": selected_direction,
                "selected_provider": selected_provider,
                "selection_reason": f"{selected_direction} test winner",
            },
        }
        a1 = SimpleNamespace(
            address_id=addr.id,
            raw_address=addr.raw_address,
            canonical_address="19964 U.S. 441, Micanopy, FL 32667",
            chosen_standardized_address="19964 U.S. 441, Micanopy, FL 32667",
            chosen_provider="google",
            confidence_score=35,
            validation_status="REVERSE_GEOCODED",
            exception_reason=None,
            comparison_reason=None,
            data={},
            smarty_lat=None,
            smarty_lon=None,
            updated_at=None,
        )
        session = self._make_session([addr], [a1])

        with patch("data_ingestion.agents.agent2_geocoding.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent2_geocoding._geocode_with_fallback") as mock_geocode:
                with patch("data_ingestion.agents.agent2_geocoding._upsert") as mock_upsert:
                    with patch("data_ingestion.agents.agent2_geocoding._ensure_table"):
                        summary = run_agent2_for_job(
                            "job1",
                            geocode_options={
                                "google_geocoding": False,
                                "osm_geocoding": False,
                                "street_interpolation": False,
                            },
                        )

        assert summary["geocoded"] == 1
        assert summary["failed"] == 0
        mock_geocode.assert_not_called()
        payload = mock_upsert.call_args.args[3]
        assert payload["status"] == "validated_match"
        assert payload["confidence"] == 100
        assert payload["source"] == selected_provider
        assert payload["formatted_address"] == addr.validated_raw_address
        assert payload["latitude"] == addr.validated_latitude
        assert payload["longitude"] == addr.validated_longitude
        assert a1.validation_status == "MATCH"
        assert a1.confidence_score == 100
        assert a1.chosen_provider == selected_provider.lower()
        assert a1.chosen_standardized_address == addr.validated_raw_address

    def test_run_handles_geocode_failure(self):
        addr = self._make_address(raw_address="Nowhere, XX 99999")
        session = self._make_session([addr])

        with patch("data_ingestion.agents.agent2_geocoding.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent2_geocoding._geocode_with_fallback", return_value=(None, [], {})):
                with patch("data_ingestion.agents.agent2_geocoding._upsert"):
                    with patch("data_ingestion.agents.agent2_geocoding._ensure_table"):
                        summary = run_agent2_for_job("job1")

        assert summary["failed"] == 1

    def test_progress_callback_invoked(self):
        addr = self._make_address(raw_address="123 Main St")
        session = self._make_session([addr])
        calls = []

        with patch("data_ingestion.agents.agent2_geocoding.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent2_geocoding._geocode_with_fallback", return_value=(None, [], {})):
                with patch("data_ingestion.agents.agent2_geocoding._upsert"):
                    with patch("data_ingestion.agents.agent2_geocoding._ensure_table"):
                        run_agent2_for_job("job1", progress_callback=lambda d, t: calls.append((d, t)))

        assert len(calls) == 1


class TestAgent2UnifiedGeocodingProgress:
    def test_progress_total_uses_address_count_not_sum_of_phases(self):
        """1233 addresses × 3 phases must not display as 3699 in the UI."""
        address_ids = list(range(1, 1234))
        progress_calls: list[tuple[int, int]] = []

        def _phase_cb(done: int, total: int) -> None:
            progress_calls.append((done, total))

        def _rev_side_effect(job_id, ids, progress_callback=None):
            if progress_callback:
                progress_callback(len(ids), len(ids))
            return {"total": len(ids)}

        def _val_side_effect(job_id, ids, progress_callback=None, **kw):
            if progress_callback:
                progress_callback(len(ids), len(ids))
            return {"total": len(ids)}

        def _fwd_side_effect(job_id, address_ids=None, progress_callback=None, **kw):
            count = len(address_ids or [])
            if progress_callback:
                progress_callback(count, count)
            return {"total": count}

        with patch("data_ingestion.agents.reverse_geocoder.run_reverse_geocoder_for_job", side_effect=_rev_side_effect):
            with patch("data_ingestion.agents.reverse_geocoder.run_address_coord_validation_for_job", side_effect=_val_side_effect):
                with patch("data_ingestion.agents.agent2_geocoding.run_agent2_for_job", side_effect=_fwd_side_effect):
                    summary = run_geocoding_for_job(
                        "job1",
                        coord_only_ids=address_ids,
                        all_ids=address_ids,
                        address_ids=address_ids,
                        forward_ids=address_ids,
                        progress_callback=lambda done, total: progress_calls.append((done, total)),
                    )

        assert summary["total"] == 1233
        assert progress_calls, "expected progress callbacks"
        assert all(total == 1233 for _, total in progress_calls)
        assert progress_calls[-1] == (1233, 1233)


class TestReverseGeocoderCacheCoordinates:
    def test_accepts_cached_result_near_query_coordinates(self):
        from data_ingestion.agents.reverse_geocoder import _cached_geo_matches_query

        geo = {"latitude": 29.370039, "longitude": -82.2061066}
        assert _cached_geo_matches_query(geo, 29.370049, -82.2061220000017)

    def test_rejects_cached_result_far_from_query_coordinates(self):
        from data_ingestion.agents.reverse_geocoder import _cached_geo_matches_query

        poisoned_geo = {"latitude": 29.5047, "longitude": -82.2798}
        assert not _cached_geo_matches_query(
            poisoned_geo,
            29.370049,
            -82.2061220000017,
        )


class TestReverseForwardMatchSelection:
    @staticmethod
    def _geo(address, house_number, *, address_types, location_type="ROOFTOP", source="google"):
        return {
            "display_name": address,
            "house_number": house_number,
            "road": "NW 152nd Ln",
            "address_types": address_types,
            "location_type": location_type,
            "source": source,
            "latitude": 29.370039,
            "longitude": -82.2061066,
        }

    def test_forward_wins_when_it_matches_uploaded_house_and_reverse_does_not(self):
        from data_ingestion.agents.reverse_geocoder import _select_match_geo

        reverse = self._geo("19964 US-441", "19964", address_types=["street_address"])
        forward = self._geo("4910 NW 152nd Ln", "4910", address_types=["street_address"])
        winner, direction, _ = _select_match_geo(
            "4910 NW 152ND LN",
            reverse_geo=reverse,
            forward_geo=forward,
            reverse_match_pct=40,
            forward_match_pct=100,
        )
        assert winner is forward
        assert direction == "forward"

    def test_reverse_wins_when_it_matches_uploaded_house_and_forward_does_not(self):
        from data_ingestion.agents.reverse_geocoder import _select_match_geo

        reverse = self._geo("4910 NW 152nd Ln", "4910", address_types=["street_address"])
        forward = self._geo("19964 US-441", "19964", address_types=["street_address"])
        winner, direction, _ = _select_match_geo(
            "4910 NW 152ND LN",
            reverse_geo=reverse,
            forward_geo=forward,
            reverse_match_pct=100,
            forward_match_pct=40,
        )
        assert winner is reverse
        assert direction == "reverse"

    def test_address_type_breaks_an_equal_match_tie(self):
        from data_ingestion.agents.reverse_geocoder import _select_match_geo

        reverse = self._geo("4910 NW 152nd Ln", "4910", address_types=["street_address"])
        forward = self._geo("4910 NW 152nd Ln", "4910", address_types=["route"])
        winner, direction, _ = _select_match_geo(
            "4910 NW 152ND LN",
            reverse_geo=reverse,
            forward_geo=forward,
            reverse_match_pct=100,
            forward_match_pct=100,
        )
        assert winner is reverse
        assert direction == "reverse"

    def test_coordinate_driven_input_prefers_reverse(self):
        from data_ingestion.agents.reverse_geocoder import _select_match_geo

        reverse = self._geo("Reverse address", "4910", address_types=["route"])
        forward = self._geo("Forward address", "4910", address_types=["street_address"])
        winner, direction, _ = _select_match_geo(
            "4910 NW 152ND LN",
            reverse_geo=reverse,
            forward_geo=forward,
            reverse_match_pct=100,
            forward_match_pct=100,
            coordinate_driven=True,
        )
        assert winner is reverse
        assert direction == "reverse"


class TestAddressCoordinateValidation:
    def test_forward_exact_near_pin_wins_over_reverse_pin_house_number_conflict(self):
        from data_ingestion.agents.reverse_geocoder import _validate_address_coords

        addr = SimpleNamespace(
            id=102,
            raw_address="102 IMPERIAL ST",
            source_raw_address=None,
            city="IMPERIAL",
            state="TX",
            zip_code="79743",
            latitude=31.274467,
            longitude=-102.692332,
            source_latitude=None,
            source_longitude=None,
            validated_raw_address=None,
            validated_street_line=None,
            validated_postcode=None,
            validated_city_state=None,
            validated_country_code=None,
            validated_latitude=None,
            validated_longitude=None,
            coord_address_match_status=None,
            coord_address_distance_m=None,
            coord_address_validation_notes=None,
            reverse_geocode_confidence_score=None,
            raw_metadata={},
        )
        reverse_at_pin = {
            "source": "google",
            "display_name": "180, Imperial Street, Imperial, Pecos County, Texas, 79743, United States",
            "house_number": "180",
            "road": "Imperial Street",
            "city": "Imperial",
            "state": "TX",
            "postcode": "79743",
            "country_code": "us",
            "location_type": "ROOFTOP",
            "latitude": 31.2744954,
            "longitude": -102.6923115,
            "address_types": ["street_address"],
        }
        forward_for_input = {
            "source": "GOOGLE",
            "display_name": "102 Imperial Street, Imperial, TX 79743, USA",
            "formatted_address": "102 Imperial Street, Imperial, TX 79743, USA",
            "house_number": "102",
            "road": "Imperial Street",
            "city": "Imperial",
            "state": "TX",
            "postcode": "79743",
            "country_code": "us",
            "location_type": "ROOFTOP",
            "latitude": 31.2741488,
            "longitude": -102.6918275,
            "address_types": ["street_address"],
        }

        with patch("data_ingestion.agents.reverse_geocoder._google_api_key", return_value="key"):
            with patch("data_ingestion.agents.reverse_geocoder._call_google_forward", return_value=forward_for_input):
                with patch("data_ingestion.agents.reverse_geocoder.flag_modified"):
                    result = _validate_address_coords(
                        addr,
                        reverse_geo=reverse_at_pin,
                        allow_reverse_fallback=False,
                    )

        assert result["status"] == "MATCH"
        assert addr.coord_address_match_status == "MATCH"
        assert addr.validated_raw_address == "102 Imperial Street, Imperial, TX 79743, USA"
        assert addr.raw_metadata["address_validation"]["forward_address_match_percent"] == 100
        assert addr.raw_metadata["address_validation"]["address_match_percent"] == 100
        assert addr.raw_metadata["address_validation"]["selected_direction"] == "forward"
        assert "Reverse at pin returned house number 180" in addr.coord_address_validation_notes

    def test_range_interpolated_forward_does_not_win_reverse_house_number_conflict(self):
        from data_ingestion.agents.reverse_geocoder import _validate_address_coords

        addr = SimpleNamespace(
            id=504,
            raw_address="504 TEACHERS RD",
            source_raw_address=None,
            city="IMPERIAL",
            state="TX",
            zip_code="79743",
            latitude=31.277279,
            longitude=-102.697174,
            source_latitude=None,
            source_longitude=None,
            validated_raw_address=None,
            validated_street_line=None,
            validated_postcode=None,
            validated_city_state=None,
            validated_country_code=None,
            validated_latitude=None,
            validated_longitude=None,
            coord_address_match_status=None,
            coord_address_distance_m=None,
            coord_address_validation_notes=None,
            reverse_geocode_confidence_score=None,
            raw_metadata={},
        )
        reverse_at_pin = {
            "source": "google",
            "display_name": "664 Teachers Rd, Imperial, TX 79743, USA",
            "house_number": "664",
            "road": "Teachers Road",
            "city": "Imperial",
            "state": "TX",
            "postcode": "79743",
            "country_code": "us",
            "location_type": "ROOFTOP",
            "latitude": 31.277279,
            "longitude": -102.697174,
            "address_types": ["street_address"],
        }
        forward_for_input = {
            "source": "GOOGLE",
            "display_name": "504 Teachers Rd, Imperial, TX 79743, USA",
            "formatted_address": "504 Teachers Rd, Imperial, TX 79743, USA",
            "house_number": "504",
            "road": "Teachers Road",
            "city": "Imperial",
            "state": "TX",
            "postcode": "79743",
            "country_code": "us",
            "location_type": "RANGE_INTERPOLATED",
            "latitude": 31.2772521,
            "longitude": -102.6962634,
            "address_types": ["street_address"],
        }

        with patch("data_ingestion.agents.reverse_geocoder._google_api_key", return_value="key"):
            with patch("data_ingestion.agents.reverse_geocoder._call_google_forward", return_value=forward_for_input):
                with patch("data_ingestion.agents.reverse_geocoder.flag_modified"):
                    result = _validate_address_coords(
                        addr,
                        reverse_geo=reverse_at_pin,
                        allow_reverse_fallback=False,
                    )

        assert result["status"] == "ADDRESS_MISMATCH"
        assert addr.coord_address_match_status == "ADDRESS_MISMATCH"
        assert addr.validated_raw_address == "504 TEACHERS RD"
        assert addr.raw_metadata["address_validation"]["location_type"] == "ROOFTOP"
        assert addr.raw_metadata["address_validation"]["selected_direction"] == "source"
        assert "664" in addr.coord_address_validation_notes

    def test_forward_exact_far_from_pin_remains_address_mismatch(self):
        from data_ingestion.agents.reverse_geocoder import _validate_address_coords

        addr = SimpleNamespace(
            id=201,
            raw_address="201 N 1ST ST",
            source_raw_address=None,
            city="IMPERIAL",
            state="TX",
            zip_code="79743",
            latitude=31.27386700000214,
            longitude=-102.6905210000026,
            source_latitude=None,
            source_longitude=None,
            validated_raw_address=None,
            validated_street_line=None,
            validated_postcode=None,
            validated_city_state=None,
            validated_country_code=None,
            validated_latitude=None,
            validated_longitude=None,
            coord_address_match_status=None,
            coord_address_distance_m=None,
            coord_address_validation_notes=None,
            reverse_geocode_confidence_score=None,
            raw_metadata={},
        )
        reverse_at_pin = {
            "source": "google",
            "display_name": "208 1st St, Imperial, TX 79743, USA",
            "house_number": "208",
            "road": "1st Street",
            "city": "Imperial",
            "state": "TX",
            "postcode": "79743",
            "country_code": "us",
            "location_type": "ROOFTOP",
            "latitude": 31.27386700000214,
            "longitude": -102.6905210000026,
            "address_types": ["street_address"],
        }
        forward_for_input = {
            "source": "GOOGLE",
            "display_name": "201 N 1st St, Imperial, TX 79743, USA",
            "formatted_address": "201 N 1st St, Imperial, TX 79743, USA",
            "house_number": "201",
            "road": "1st Street",
            "city": "Imperial",
            "state": "TX",
            "postcode": "79743",
            "country_code": "us",
            "location_type": "ROOFTOP",
            "latitude": 31.270818,
            "longitude": -102.690521,
            "address_types": ["street_address"],
        }

        with patch("data_ingestion.agents.reverse_geocoder._google_api_key", return_value="key"):
            with patch("data_ingestion.agents.reverse_geocoder._call_google_forward", return_value=forward_for_input):
                with patch("data_ingestion.agents.reverse_geocoder.flag_modified"):
                    result = _validate_address_coords(
                        addr,
                        reverse_geo=reverse_at_pin,
                        allow_reverse_fallback=False,
                    )

        assert result["status"] == "ADDRESS_MISMATCH"
        assert addr.coord_address_match_status == "ADDRESS_MISMATCH"
        assert addr.validated_raw_address == "201 N 1ST ST"
        assert addr.raw_metadata["address_validation"]["forward_address_match_percent"] == 100
        assert addr.raw_metadata["address_validation"]["selected_direction"] == "source"
        assert "208" in addr.coord_address_validation_notes


class TestReverseGeocodeConfidenceScoring:
    def test_apply_reverse_confidence_caps_when_text_match_below_100(self):
        from data_ingestion.agents.reverse_geocoder import _apply_reverse_geocode_confidence

        addr = MagicMock()
        geo = {
            "source": "GOOGLE",
            "location_type": "ROOFTOP",
            "house_number": "4248",
            "road": "Northwest 153rd Street",
            "postcode": "32686",
        }
        confidence = _apply_reverse_geocode_confidence(
            addr,
            geo,
            match_status="MISMATCH_WARN",
            distance_m=33.0,
            address_match_percent=40,
        )
        assert confidence <= 35
        assert addr.reverse_geocode_confidence_score <= 35

    def test_sync_reverse_score_does_not_overwrite_validation_confidence(self):
        from data_ingestion.utils.address_metadata import sync_reverse_geocode_confidence_in_raw_metadata

        addr = MagicMock()
        addr.raw_metadata = {
            "address_validation": {
                "confidence_score": 35,
                "match_status": "MISMATCH_WARN",
            }
        }
        sync_reverse_geocode_confidence_in_raw_metadata(addr, 99)
        assert addr.raw_metadata["reverse_geocode_confidence_score"] == 99
        assert addr.raw_metadata["address_validation"]["confidence_score"] == 35


class TestAgent2ResolutionGate:
    def test_failed_agent2_does_not_use_reverse_confidence_fallback(self):
        from uuid import uuid4

        from data_ingestion.agents.pipeline_runner import _apply_resolution_gate

        job_id = str(uuid4())
        addr = MagicMock()
        addr.id = 101
        addr.job_id = job_id
        addr.raw_address = "15345 NW 42ND TER"
        addr.latitude = 29.371544
        addr.longitude = -82.196114
        addr.reverse_geocode_confidence_score = 99
        addr.raw_metadata = {}

        agent_result = MagicMock()
        agent_result.address_id = 101
        agent_result.data = {
            "status": "failed",
            "confidence": 0,
            "formatted_address": "15345 NW 42ND TER",
            "latitude": 29.371544,
            "longitude": -82.196114,
        }

        session = MagicMock()
        session.scalars.side_effect = [
            MagicMock(all=MagicMock(return_value=[addr])),
            MagicMock(all=MagicMock(return_value=[agent_result])),
        ]
        session.commit = MagicMock()
        session.close = MagicMock()

        with patch("data_ingestion.agents.pipeline_runner.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            continued, summary = _apply_resolution_gate(
                job_id,
                [101],
                "agent2_geocoding",
                90,
            )

        assert continued == [101]
        assert summary["accepted"] == 0
        assert "final_resolution" not in (addr.raw_metadata or {})


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 3 — run_agent3_for_job with mocked DB + HTTP
# ══════════════════════════════════════════════════════════════════════════════

from data_ingestion.agents.agent3_parcel import run_agent3_for_job


class TestAgent3RunWithMockDB:
    def _make_session(self, addresses, a1_results=None):
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        session.add = MagicMock()
        session.commit = MagicMock()
        session.close = MagicMock()
        session.scalars.side_effect = [
            MagicMock(all=MagicMock(return_value=addresses)),
            MagicMock(all=MagicMock(return_value=a1_results or [])),
            MagicMock(all=MagicMock(return_value=[])),
        ]
        return session

    def _make_address(self, id=1, lat=40.7, lon=-74.0, raw_address="123 Drive"):
        addr = MagicMock()
        addr.id = id
        addr.latitude = lat
        addr.longitude = lon
        addr.raw_address = raw_address
        return addr

    def test_skips_address_without_coords(self):
        addr = self._make_address(lat=None, lon=None)
        session = self._make_session([addr])

        with patch("data_ingestion.agents.agent3_parcel.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent3_parcel._upsert"):
                with patch("data_ingestion.agents.agent3_parcel._ensure_table"):
                    summary = run_agent3_for_job("job1")

        assert summary["skipped"] == 1

    def test_finds_parcel_via_census(self):
        addr = self._make_address()
        session = self._make_session([addr])
        census_data = {
            "state_fips": "36", "county_fips": "061",
            "county_name": "New York County", "source": "census",
        }

        with patch("data_ingestion.agents.agent3_parcel.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = session
            with patch("data_ingestion.agents.agent3_parcel._call_regrid", return_value=None):
                with patch("data_ingestion.agents.agent3_parcel._call_tigerline", return_value=census_data):
                    with patch("data_ingestion.agents.agent3_parcel._upsert"):
                        with patch("data_ingestion.agents.agent3_parcel._ensure_table"):
                            summary = run_agent3_for_job("job1")

        assert summary["found"] == 1


# Agent 4 - cached footprint index


class TestAgent4ReferenceCache:
    def setup_method(self):
        agent4_building._REFERENCE_AGENT_CACHE = None
        agent4_building._REFERENCE_AGENT_STATES = set()

    def teardown_method(self):
        agent4_building._REFERENCE_AGENT_CACHE = None
        agent4_building._REFERENCE_AGENT_STATES = set()

    def test_reuses_cached_reference_agent_for_same_state(self):
        class FakeBuildingDataAgent:
            created = 0

            def __init__(self, config_path):
                FakeBuildingDataAgent.created += 1
                self.total = 99
                self.matched = 88
                self.distance_sum = 77
                self.class_counts = {"SFH": 1}

        records = [{"lat": 32.09, "lon": -84.24}]

        with patch("data_ingestion.agents.agent4_building._states_for_records", return_value={"Georgia"}):
            with patch("data_ingestion.agents.agent4_building._import_reference_agent", return_value=FakeBuildingDataAgent):
                first = agent4_building._get_reference_agent(records)
                second = agent4_building._get_reference_agent(records)

        assert first is second
        assert FakeBuildingDataAgent.created == 1
        assert second.total == 0
        assert second.matched == 0
        assert second.distance_sum == 0
        assert second.class_counts == {}


class TestAgent4MatchedFootprintFallback:
    def _addr(self):
        return SimpleNamespace(
            id=1,
            raw_address="514 Walnut Dr",
            validated_raw_address=None,
            raw_metadata={},
        )

    def test_matched_house_scale_unresolved_footprint_far_match_becomes_sfh(self):
        enriched = {
            "address_id": 1,
            "address": "514 Walnut Dr",
            "lat": 32.0948509,
            "lon": -84.2493417,
            "building_matched": True,
            "structure_hint": "UNRESOLVED",
            "hint_confidence": 0,
            "class_source": "HUMAN",
            "footprint_area_m2": 220,
            "footprint_match_distance_m": 18,
            "hint_signals": [],
        }

        payload = agent4_building._to_agent4_payload(enriched, self._addr(), None)

        assert payload["structure_type"] == "SFH"
        assert payload["structure_hint"] == "SFU"
        assert payload["confidence"] >= 80
        assert payload["class_source"] == "FOOTPRINT_FALLBACK"
        assert "matched_residential_footprint_fallback" in payload["hint_signals"]

    def test_matched_house_scale_unresolved_footprint_close_match_becomes_sfh(self):
        enriched = {
            "address_id": 1,
            "address": "514 Walnut Dr",
            "lat": 32.0948509,
            "lon": -84.2493417,
            "building_matched": True,
            "structure_hint": "UNRESOLVED",
            "hint_confidence": 0,
            "class_source": "HUMAN",
            "footprint_area_m2": 220,
            "footprint_match_distance_m": 8,
            "hint_signals": [],
        }

        payload = agent4_building._to_agent4_payload(enriched, self._addr(), None)

        assert payload["structure_type"] == "SFH"
        assert payload["structure_hint"] == "SFU"
        assert payload["confidence"] >= 80
        assert payload["class_source"] == "FOOTPRINT_FALLBACK"
        assert "matched_residential_footprint_fallback" in payload["hint_signals"]

    def test_large_unresolved_footprint_becomes_mdu_large(self):
        enriched = {
            "building_matched": True,
            "structure_hint": "UNRESOLVED",
            "hint_confidence": 0,
            "class_source": "HUMAN",
            "footprint_area_m2": 2500,
            "footprint_match_distance_m": 10,
        }

        payload = agent4_building._to_agent4_payload(enriched, self._addr(), None)

        assert payload["structure_type"] == "MDU"
        assert payload["structure_hint"] == "MDU_LARGE"
        assert payload["class_source"] == "FOOTPRINT_FALLBACK"


class TestAgent4BuildingsAddressMetadata:
    def _addr(self, raw_metadata=None, **kwargs):
        defaults = {
            "id": 42,
            "raw_address": "514 Walnut Dr",
            "validated_raw_address": None,
            "city": "Americus",
            "state": "GA",
            "zip_code": "31719",
            "latitude": 32.0948509,
            "longitude": -84.2493417,
            "source_latitude": None,
            "source_longitude": None,
            "validated_latitude": None,
            "validated_longitude": None,
            "raw_metadata": raw_metadata or {},
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_record_from_db_metadata_uses_address_columns(self):
        addr = self._addr()
        rec = agent4_building._record_from_db_metadata(addr)
        assert rec is not None
        assert rec["address_id"] == 42
        assert rec["lat"] == pytest.approx(32.0948509)
        assert rec["lon"] == pytest.approx(-84.2493417)
        assert rec["_input_source"] == "database"
        assert "514 Walnut Dr" in rec["address"]

    def test_record_for_reference_uses_database_over_old_snapshot(self):
        addr = self._addr(
            raw_metadata={
                "old": {
                    "street_number_name": "514 Walnut Dr",
                    "latitude": 32.0948509,
                    "longitude": -84.2493417,
                }
            },
            latitude=32.0,
            longitude=-84.0,
        )
        rec = agent4_building._record_for_reference(addr, None, None)
        assert rec["lat"] == pytest.approx(32.0)
        assert rec["_input_source"] == "database"

    def test_persist_buildings_address_writes_raw_metadata(self):
        addr = self._addr(raw_metadata={
            "ADDRESS": "514 Walnut Dr, Americus, GA",
        })
        enriched = {
            "address_id": 42,
            "lat": 32.0948509,
            "lon": -84.2493417,
            "building_matched": True,
            "structure_hint": "SFU",
            "hint_confidence": 80,
            "class_source": "RULE",
            "footprint_area_m2": 220.0,
            "footprint_match_distance_m": 10.0,
            "matched_radius_m": 15,
        }
        payload = agent4_building._to_agent4_payload(enriched, addr, None)

        agent4_building._persist_buildings_address(
            addr,
            enriched,
            payload,
            input_source="database",
        )

        assert "buildings_address" in addr.raw_metadata
        ba = addr.raw_metadata["buildings_address"]
        assert ba["source"] == "database"
        assert ba["input"]["street_number_name"] == "514 Walnut Dr"
        assert ba["classification"]["structure_type"] == "SFH"
        assert ba["structure_hint"] == "SFU"
        assert ba["building_matched"] is True
        assert "updated_at" in ba
        assert addr.raw_metadata["ADDRESS"] == "514 Walnut Dr, Americus, GA"


class TestAgent4MicrosoftRegionTiles:
    """Border quadkeys exist under both Canada and UnitedStates with different tiles."""

    def test_niagara_prefers_canada_region(self):
        assert agent4_building._preferred_ms_region_for_records(
            [{"lat": 43.182603, "lon": -79.259024}]
        ) == "Canada"

    def test_buffalo_prefers_united_states_region(self):
        assert agent4_building._preferred_ms_region_for_records(
            [{"lat": 42.8864, "lon": -78.8784}]
        ) == "UnitedStates"

    def test_tile_candidates_prefer_canada_then_us_sibling(self):
        import pandas as pd

        links = pd.DataFrame(
            [
                {
                    "Location": "UnitedStates",
                    "QuadKey": "30223133",
                    "Url": "https://example.test/us.csv.gz",
                },
                {
                    "Location": "Canada",
                    "QuadKey": "30223133",
                    "Url": "https://example.test/ca.csv.gz",
                },
            ]
        )
        candidates = agent4_building._tile_candidates_for_quadkey(
            links, "30223133", preferred_region="Canada"
        )
        assert [c["region"] for c in candidates] == ["Canada", "UnitedStates"]
        assert candidates[0]["url"].endswith("/ca.csv.gz")

    def test_region_cache_token_normalizes_labels(self):
        assert agent4_building._region_cache_token("United States") == "unitedstates"
        assert agent4_building._region_cache_token("Canada") == "canada"
