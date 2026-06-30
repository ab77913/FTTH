"""Tests for provider API sub-JSON persistence in raw_metadata."""
from __future__ import annotations

from types import SimpleNamespace

from data_ingestion.utils.api_result_metadata import (
    geo_dict_to_provider_block,
    persist_provider_result,
    persist_provider_results,
)


def _addr(**kwargs):
    base = {"raw_metadata": {}}
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_geo_dict_to_provider_block_reverse() -> None:
    block = geo_dict_to_provider_block({
        "display_name": "15290 NW Gainesville Rd, Reddick, FL 32686, USA",
        "latitude": 29.371,
        "longitude": -82.197,
        "location_type": "ROOFTOP",
        "source": "google",
        "house_number": "15290",
        "road": "NW Gainesville Rd",
    })
    assert block["ok"] is True
    assert block["formatted_address"] == "15290 NW Gainesville Rd, Reddick, FL 32686, USA"
    assert block["location_type"] == "ROOFTOP"
    assert block["source"] == "GOOGLE"


def test_persist_provider_results_writes_all_forward_providers() -> None:
    addr = _addr()
    persist_provider_results(addr, {
        "geocoding": {
            "formatted_address": "15290 NW Gainesville Rd, Reddick, FL",
            "latitude": 29.37,
            "longitude": -82.19,
            "source": "GOOGLE",
            "ok": True,
            "confidence": 95.0,
        },
        "osm": {
            "formatted_address": "15290, Northwest Gainesville Road, Reddick, FL",
            "ok": True,
            "source": "OSM",
        },
        "street_interpolated": {"ok": False, "source": "INTERPOLATED"},
    })
    assert addr.raw_metadata["google_geocoding"]["source"] == "GOOGLE"
    assert addr.raw_metadata["geocoding"]["source"] == "GOOGLE"
    assert addr.raw_metadata["osm"]["source"] == "OSM"
    assert addr.raw_metadata["street_interpolated"]["ok"] is False


def test_persist_reverse_geocoding_block() -> None:
    addr = _addr()
    persist_provider_result(addr, "reverse_geocoding", {
        "display_name": "15290 NW Gainesville Rd, Reddick, FL 32686, USA",
        "latitude": 29.371214,
        "longitude": -82.197885,
        "location_type": "ROOFTOP",
        "source": "google",
        "address_match_percent": 100,
        "confidence": 92,
    })
    rev = addr.raw_metadata["reverse_geocoding"]
    assert rev["location_type"] == "ROOFTOP"
    assert rev["address_match_percent"] == 100
