from __future__ import annotations

from data_ingestion.utils.rule_classification import (
    address_found_from_raw_metadata,
    is_sticky_duplicate,
    rule_status_from_raw_metadata,
)


def test_address_found_match_status() -> None:
    meta = {"address_validation": {"match_status": "MATCH"}}
    assert address_found_from_raw_metadata(meta) is True


def test_address_found_auto_accept_from_final_resolution() -> None:
    meta = {
        "address_validation": {"match_status": "MISMATCH_WARN"},
        "final_resolution": {"status": "AUTO_ACCEPT"},
    }
    assert address_found_from_raw_metadata(meta) is True


def test_address_not_found_manual_review() -> None:
    meta = {
        "address_validation": {"match_status": "MISMATCH_WARN"},
        "final_resolution": {"status": None},
    }
    assert address_found_from_raw_metadata(meta) is False
    status, color, reason = rule_status_from_raw_metadata(meta)
    assert status == "invalid"
    assert color == "red"
    assert "not found" in reason.lower()


def test_duplicate_preserved() -> None:
    meta = {"address_validation": {"match_status": "MATCH"}}
    status, color, reason = rule_status_from_raw_metadata(meta, merge_status="duplicate")
    assert status == "duplicate"
    assert color == "white"


def test_match_found_reason() -> None:
    meta = {"address_validation": {"match_status": "MATCH"}}
    status, _, reason = rule_status_from_raw_metadata(meta)
    assert status == "verified"
    assert "match" in reason.lower()


def test_agent7_geospatial_discovery_is_new_not_kmz_input() -> None:
    meta = {
        "file_role": "geospatial",
        "source_format": "agent7",
        "agent7_discovered": True,
        "merge_status": "new",
    }
    assert rule_status_from_raw_metadata(meta) == (
        "new",
        "yellow",
        "New address identified from KML/KMZ but not present in CSV/Excel",
    )


def test_sticky_duplicate_detects_persisted_duplicate_statuses() -> None:
    assert is_sticky_duplicate({"record_status": "DUPLICATE"}) is True
    assert is_sticky_duplicate({"merge_classification": {"status": "duplicate"}}) is True
    assert is_sticky_duplicate({}, {"rule_status": "duplicate"}) is True


def test_sticky_duplicate_overrides_later_match_metadata() -> None:
    meta = {
        "record_status": "DUPLICATE",
        "merge_status": "verified",
        "address_validation": {"match_status": "MATCH"},
        "final_resolution": {"status": "AUTO_ACCEPT"},
    }
    status, color, reason = rule_status_from_raw_metadata(meta)
    assert status == "duplicate"
    assert color == "white"
    assert "duplicate" in reason.lower()
