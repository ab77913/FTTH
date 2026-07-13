from __future__ import annotations

from data_ingestion.utils.rule_classification import (
    address_found_from_raw_metadata,
    coord_address_mismatch_reason,
    is_coord_address_mismatch,
    is_interpolated_forward_reverse_conflict,
    is_missing_address_record,
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


def test_missing_address_comment_is_excluded_not_invalid() -> None:
    meta = {"COMMENTS": "ADDRESS DATA NOT GIVEN IN CSV"}
    assert is_missing_address_record(meta) is True

    status, color, reason = rule_status_from_raw_metadata(meta)
    assert status == "excluded"
    assert color == ""
    assert "not given" in reason.lower()


def test_missing_tabular_raw_address_is_excluded() -> None:
    meta = {"file_role": "tabular"}
    status, color, reason = rule_status_from_raw_metadata(meta)
    assert status == "invalid"
    assert color == "red"

    assert is_missing_address_record(meta, raw_address=None) is True
    assert is_missing_address_record(meta, raw_address="") is True


def test_interpolated_forward_reverse_conflict_is_invalid_not_found() -> None:
    meta = {
        "address_validation": {
            "match_status": "MATCH",
            "selected_direction": "forward",
            "location_type": "RANGE_INTERPOLATED",
            "reverse_address_match_percent": 40,
            "forward_address_match_percent": 100,
            "notes": "Reverse at pin returned house number 106, so the uploaded address was retained.",
        }
    }

    assert is_interpolated_forward_reverse_conflict(meta) is True
    assert address_found_from_raw_metadata(meta) is False
    status, color, reason = rule_status_from_raw_metadata(meta)
    assert status == "invalid"
    assert color == "red"
    assert "range-interpolated" in reason


def test_coord_address_mismatch_is_invalid_even_when_forward_geocode_matches() -> None:
    meta = {
        "merge_status": "verified",
        "merge_color": "green",
        "address_validation": {
            "match_status": "ADDRESS_MISMATCH",
            "notes": (
                "Uploaded address house number 65 does not match the address at the "
                "supplied coordinates (64). Reverse at pin: 64 Stevensville Rd, Underhill, VT 05489, USA"
            ),
        },
    }

    assert is_coord_address_mismatch(meta) is True
    assert address_found_from_raw_metadata(meta) is False
    status, color, reason = rule_status_from_raw_metadata(meta)
    assert status == "invalid"
    assert color == "red"
    assert "house number 65" in coord_address_mismatch_reason(meta)
    assert "house number 65" in reason
