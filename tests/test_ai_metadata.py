"""Tests for compact AI metadata and strict address matching."""
from __future__ import annotations

from types import SimpleNamespace

from data_ingestion.utils.address_match import (
    address_match_percent,
    address_matches_exact,
    looks_like_street_address,
    resolve_upload_address_line,
)
from data_ingestion.utils.ai_metadata import persist_ai_metadata
from data_ingestion.utils.address_metadata import persist_agent1_address_validator_in_raw_metadata
from data_ingestion.agents.reverse_geocoder import is_kmz_placemark_label


def _addr(**kwargs):
    base = {"raw_metadata": {}}
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_persist_ai_metadata_writes_compact_block() -> None:
    addr = _addr(raw_metadata={
        "reverse_geocoding": {"formatted_address": "1 Main St", "ok": True},
        "google_geocoding": {"formatted_address": "1 Main St", "ok": True},
        "osm": {"formatted_address": "1 Main St", "ok": True},
    })
    persist_ai_metadata(
        addr,
        agent_name="agent2_geocoding",
        address="805 Adderton St, Americus, GA 31709",
        latitude=32.07,
        longitude=-84.23,
        city="Americus",
        country="US",
        zip="31709",
        zip_code="31709-1234",
        confidence=95,
        remarks="Google ROOFTOP",
        ai_type="GOOGLE",
    )
    ai = addr.raw_metadata["ai"]
    assert ai["ai_address"] == "805 Adderton St, Americus, GA 31709"
    assert ai["ai_latitude"] == 32.07
    assert ai["ai_agent_name"] == "agent2_geocoding"
    assert addr.raw_metadata["reverse_geocoding"]["ok"] is True
    assert addr.raw_metadata["google_geocoding"]["ok"] is True
    assert addr.raw_metadata["osm"]["ok"] is True


def test_persist_agent1_address_validator_writes_smarty_melissa_sub_json() -> None:
    addr = _addr(raw_metadata={"old": {"street_number_name": "1 Main St"}, "agent1_address_validator": {"legacy": True}})
    row = SimpleNamespace(
        raw_address="1 Main St",
        canonical_address="1 MAIN ST",
        smarty_standardized_address="1 Main Street",
        smarty_dpv="Y",
        smarty_zip_plus_4="1234",
        smarty_vacant=False,
        smarty_record_type="S",
        smarty_lat=32.07,
        smarty_lon=-84.23,
        melissa_standardized_address="1 Main St",
        melissa_dpv="Y",
        melissa_zip_plus_4="1234",
        melissa_vacant=False,
        melissa_record_type="S",
        chosen_standardized_address="1 Main Street",
        chosen_provider="smarty",
        confidence_score=99,
        validation_status="AUTO_ACCEPT",
        structure_hint="SFU",
        exception_reason=None,
        comparison_reason="NO_CONFLICT: providers agree",
    )
    persist_agent1_address_validator_in_raw_metadata(addr, row)
    block = addr.raw_metadata["Smarty_Street"]
    assert "agent1_address_validator" not in addr.raw_metadata
    assert block["chosen_provider"] == "smarty"
    assert block["confidence_score"] == 99
    assert block["smarty"]["standardized_address"] == "1 Main Street"
    assert block["smarty"]["dpv"] == "Y"
    assert block["smarty"]["latitude"] == 32.07
    assert block["melissa"]["standardized_address"] == "1 Main St"
    assert block["melissa"]["dpv"] == "Y"
    assert addr.raw_metadata["old"]["street_number_name"] == "1 Main St"


def test_persist_agent1_address_validator_cache_hit_populates_metadata() -> None:
    addr = _addr(raw_metadata={})
    row = SimpleNamespace(
        raw_address="212 Wild Spring Ct",
        canonical_address="212 WILD SPRING CT LEXINGTON SC 29072",
        smarty_standardized_address="212 Wild Spring Ct Lexington SC 29072-7179",
        smarty_dpv="Y",
        smarty_zip_plus_4="29072-7179",
        smarty_vacant=False,
        smarty_record_type="S",
        smarty_lat=33.9951,
        smarty_lon=-81.29379,
        melissa_standardized_address=None,
        melissa_dpv=None,
        melissa_zip_plus_4=None,
        melissa_vacant=None,
        melissa_record_type=None,
        chosen_standardized_address="212 Wild Spring Ct Lexington SC 29072-7179",
        chosen_provider="smarty",
        confidence_score=99,
        validation_status="AUTO_ACCEPT",
        structure_hint="SFU",
        exception_reason=None,
        comparison_reason="Loaded from cache",
    )
    cached = {
        "normalized_full_address": "212 WILD SPRING CT LEXINGTON SC 29072",
        "cached_at": "2026-06-08T15:42:38.203445+00:00",
        "smarty_geocode_precision": "Zip9",
    }
    smarty_result = SimpleNamespace(
        provider="smarty",
        success=True,
        standardized_address=row.smarty_standardized_address,
        dpv_match=row.smarty_dpv,
        zip_plus_4=row.smarty_zip_plus_4,
        vacant=row.smarty_vacant,
        record_type=row.smarty_record_type,
        latitude=row.smarty_lat,
        longitude=row.smarty_lon,
        geocode_precision="Zip9",
        error=None,
    )
    melissa_result = SimpleNamespace(
        provider="melissa",
        success=False,
        standardized_address=None,
        dpv_match=None,
        zip_plus_4=None,
        vacant=None,
        record_type=None,
        latitude=None,
        longitude=None,
        geocode_precision=None,
        error="Skipped because FTTH_AGENT1_PROVIDER_MODE=smarty_only",
    )
    persist_agent1_address_validator_in_raw_metadata(
        addr,
        row,
        smarty_result=smarty_result,
        melissa_result=melissa_result,
        cached=cached,
    )
    block = addr.raw_metadata["Smarty_Street"]
    assert block["source"] == "cache"
    assert block["cached_at"] == cached["cached_at"]
    assert block["normalized_full_address"] == cached["normalized_full_address"]
    assert block["smarty"]["geocode_precision"] == "Zip9"
    assert block["smarty"]["success"] is True


def test_persist_agent1_address_validator_writes_smarty_melissa_on_provider_failure() -> None:
    addr = _addr(raw_metadata={"old": {"street_number_name": "4905 NW 152ND LN"}})
    row = SimpleNamespace(
        raw_address="4905 NW 152ND LN",
        canonical_address="4905 NORTHWEST 152ND LN REDDICK FL 32686",
        smarty_standardized_address=None,
        smarty_dpv=None,
        smarty_zip_plus_4=None,
        smarty_vacant=None,
        smarty_record_type=None,
        smarty_lat=None,
        smarty_lon=None,
        melissa_standardized_address=None,
        melissa_dpv=None,
        melissa_zip_plus_4=None,
        melissa_vacant=None,
        melissa_record_type=None,
        chosen_standardized_address=None,
        chosen_provider="smarty",
        confidence_score=35,
        validation_status="MANUAL_REVIEW",
        structure_hint="SFU_HINT",
        exception_reason="Both providers failed; provider validation failed; coordinates available",
        comparison_reason="Loaded from cache",
    )
    persist_agent1_address_validator_in_raw_metadata(addr, row)
    block = addr.raw_metadata["Smarty_Street"]
    assert block["smarty"]["success"] is False
    assert "Both providers failed" in block["smarty"]["error"]
    assert block["melissa"]["success"] is False


def test_address_match_exact() -> None:
    inp = "805 Adderton St"
    fmt = "805 Adderton Street, Americus, GA 31709, USA"
    score = address_match_percent(
        inp,
        fmt,
        city="Americus",
        state="GA",
        zip_code="31709",
    )
    assert score == 100
    assert address_matches_exact(inp, fmt, city="Americus", state="GA", zip_code="31709")


def test_kml_household_resolves_placemark_address() -> None:
    meta = {
        "source_format": "kml",
        "category": "Households",
        "placemark_name": "4910 NW 152ND LN",
        "Address": "4910 NW 152ND LN",
    }
    raw = "Households: 4910 NW 152ND LN"
    assert resolve_upload_address_line(raw, meta) == "4910 NW 152ND LN"
    assert looks_like_street_address("4910 NW 152ND LN")
    assert not is_kmz_placemark_label(raw, meta)


def test_kml_wrong_reverse_geocode_does_not_match_upload() -> None:
    upload = "4910 NW 152ND LN"
    reverse = "19964 US-441, Micanopy, FL 32667, USA"
    score = address_match_percent(upload, reverse, city="REDDICK", state="FL")
    assert score < 100
    assert not address_matches_exact(upload, reverse, city="REDDICK", state="FL")


def test_abbreviation_equivalent_addresses_score_100() -> None:
    upload = "15290 NW GAINESVILLE RD"
    google = "15290 NW Gainesville Rd, Reddick, FL 32686, USA"
    osm = "15290, Northwest Gainesville Road, Reddick, Marion County, Florida, 32686, United States"
    kwargs = {"city": "REDDICK", "state": "FL"}
    assert address_match_percent(upload, google, **kwargs) == 100
    assert address_match_percent(upload, osm, **kwargs) == 100
    assert address_matches_exact(upload, google, **kwargs)
    assert address_matches_exact(upload, osm, **kwargs)


def test_nw_lane_abbreviations_score_100() -> None:
    upload = "4910 NW 152ND LN"
    osm = "4910, Northwest 152nd Lane, Reddick, Marion County, Florida, 32686, United States"
    assert address_match_percent(upload, osm, city="REDDICK", state="FL") == 100


def test_international_abbreviations_score_100() -> None:
    assert address_match_percent(
        "10 Downing Street",
        "10 Downing St, London SW1A 2AA, UK",
        city="London",
    ) == 100
    assert address_match_percent(
        "123 Rue de Rivoli",
        "123 Rue de Rivoli, 75001 Paris, France",
        city="Paris",
    ) == 100


def test_different_house_number_stays_low() -> None:
    upload = "4910 NW 152ND LN"
    reverse = "19964 US-441, Micanopy, FL 32667, USA"
    assert address_match_percent(upload, reverse, city="REDDICK", state="FL") <= 40


def test_hyphenated_house_number_mismatch() -> None:
    upload = "4905 NW 152ND LN"
    reverse_geo = {
        "display_name": "49-5 NW 152nd Ln, Reddick, FL 32686, USA",
        "house_number": "49-5",
    }
    from data_ingestion.utils.address_match import address_match_percent_for_geo

    pct = address_match_percent_for_geo(upload, reverse_geo, city="REDDICK", state="FL")
    assert pct <= 40

    forward_geo = {
        "display_name": "4905 NW 152nd Ln, Reddick, FL 32686, USA",
        "house_number": "4905",
    }
    assert address_match_percent_for_geo(upload, forward_geo, city="REDDICK", state="FL") == 100


def test_rooftop_confidence_requires_full_reverse_match() -> None:
    from data_ingestion.utils.address_match import finalize_geocoder_confidence

    high = finalize_geocoder_confidence(
        99,
        location_type="ROOFTOP",
        address_match_percent=100,
    )
    assert high == 99

    capped = finalize_geocoder_confidence(
        99,
        location_type="ROOFTOP",
        address_match_percent=40,
    )
    assert capped <= 35

    capped_interp = finalize_geocoder_confidence(
        95,
        location_type="RANGE_INTERPOLATED",
        address_match_percent=100,
    )
    assert capped_interp <= 35

    capped_osm = finalize_geocoder_confidence(
        88,
        location_type="APPROXIMATE",
        address_match_percent=100,
    )
    assert capped_osm <= 35


def test_refine_coord_match_status_downgrades_distance_only_match() -> None:
    from data_ingestion.agents.reverse_geocoder import _refine_coord_match_status

    forward = {
        "location_type": "RANGE_INTERPOLATED",
        "display_name": "15060 NW 41st Terrace, Reddick, FL",
    }
    reverse_geo = {
        "location_type": "ROOFTOP",
        "display_name": "4192 NW 151st St, Reddick, FL",
    }
    match_scores = {"reverse": 40, "forward": 100, "best": 100}
    status, notes = _refine_coord_match_status(
        "MATCH",
        forward=forward,
        reverse_geo=reverse_geo,
        match_scores=match_scores,
        confidence_score=35,
        notes="Address and coordinates agree within 99.6m (threshold 100.0m).",
    )
    assert status == "MISMATCH_WARN"
    assert "reverse at pin 40%" in notes
    assert "RANGE_INTERPOLATED" in notes
    assert "validation confidence 35" in notes


def test_refine_coord_match_status_keeps_rooftop_match() -> None:
    from data_ingestion.agents.reverse_geocoder import _refine_coord_match_status

    forward = {
        "location_type": "ROOFTOP",
        "display_name": "4905 NW 152nd Ln, Reddick, FL",
    }
    reverse_geo = {
        "location_type": "ROOFTOP",
        "display_name": "4905 NW 152nd Ln, Reddick, FL",
    }
    match_scores = {"reverse": 100, "forward": 100, "best": 100}
    status, notes = _refine_coord_match_status(
        "MATCH",
        forward=forward,
        reverse_geo=reverse_geo,
        match_scores=match_scores,
        confidence_score=99,
        notes="Address and coordinates agree within 12.0m (threshold 100.0m).",
    )
    assert status == "MATCH"
    assert notes.startswith("Address and coordinates agree")

