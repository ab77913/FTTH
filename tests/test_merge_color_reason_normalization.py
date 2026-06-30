from __future__ import annotations

from api_server import _normalized_merge_fields, _rule_export_fields, _rule_map_export_fields


def test_normalized_merge_fields_verified_defaults_reason_and_color() -> None:
    out = _normalized_merge_fields({"merge_status": "verified", "merge_color": "", "merge_reason": ""})
    assert out["merge_status"] == "verified"
    assert out["merge_color"] == "green"
    assert out["merge_reason"] == "Processed final address and coordinates verified against raw data"


def test_normalized_merge_fields_duplicate_color_maps_to_expected_reason() -> None:
    out = _normalized_merge_fields({"merge_status": "duplicate", "merge_color": "blue", "merge_reason": ""})
    assert out["merge_status"] == "duplicate"
    assert out["merge_color"] == "white"
    assert out["merge_reason"] == "Duplicate address in uploaded raw data"


def test_normalized_merge_fields_unknown_falls_back_to_invalid_red() -> None:
    out = _normalized_merge_fields({"merge_status": "something_else", "merge_color": "purple", "merge_reason": ""})
    assert out["merge_status"] == "invalid"
    assert out["merge_color"] == "red"
    assert out["merge_reason"] == "Address not found or failed final verification"


def test_original_kmz_input_stays_verified_green() -> None:
    out = _normalized_merge_fields({
        "file_role": "geospatial",
        "source_format": "kmz",
        "merge_status": "new",
        "merge_color": "yellow",
    })
    assert out["merge_status"] == "verified"
    assert out["merge_color"] == "green"


def test_agent7_neighborhood_record_stays_new_yellow() -> None:
    meta = {
        "file_role": "geospatial",
        "source_format": "agent7",
        "agent7_discovered": True,
        "merge_status": "new",
        "merge_color": "yellow",
        "rule_status": "new",
        "rule_color": "yellow",
    }
    merge = _normalized_merge_fields(meta)
    rule = _rule_export_fields(meta, {"rule_status": "new", "color": "yellow"})
    assert merge["merge_status"] == "new"
    assert merge["merge_color"] == "yellow"
    assert rule["rule_status"] == "new"
    assert rule["rule_color"] == "yellow"


def test_sticky_duplicate_wins_over_verified_merge_fields() -> None:
    out = _normalized_merge_fields({
        "record_status": "DUPLICATE",
        "merge_status": "verified",
        "merge_color": "green",
        "merge_reason": "Later verification should not replace duplicate",
    })
    assert out["merge_status"] == "duplicate"
    assert out["merge_color"] == "white"


def test_sticky_duplicate_wins_over_valid_rule_export_fields() -> None:
    rule = _rule_export_fields(
        {"record_status": "DUPLICATE", "merge_status": "verified", "merge_color": "green"},
        {"rule_status": "valid", "color": "green", "reason": "Agent marked valid"},
    )
    map_fields = _rule_map_export_fields(rule)
    assert rule["rule_status"] == "duplicate"
    assert rule["rule_color"] == "white"
    assert map_fields["map_color_hex"] == "#ffffff"
