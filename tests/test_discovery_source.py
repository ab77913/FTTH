from __future__ import annotations

from data_ingestion.utils.discovery_source import (
    build_new_address_discovery_metadata,
    format_new_address_discovery_summary,
    new_address_discovery_ui_fields,
    resolve_new_address_discovery,
)


def test_format_new_address_discovery_summary_rooftop() -> None:
    assert format_new_address_discovery_summary(
        agent="agent7",
        provider="google_reverse_geocode",
        location_type="ROOFTOP",
    ) == "Agent 7 via Google Reverse (ROOFTOP)"


def test_build_new_address_discovery_metadata_appends_summary_to_reason() -> None:
    meta = build_new_address_discovery_metadata(
        agent="agent0",
        provider="google_reverse_geocode",
        location_type="RANGE_INTERPOLATED",
        base_reason="New address discovered by Agent 0 inside polygon",
    )
    assert meta["new_address_discovery_location_type"] == "RANGE_INTERPOLATED"
    assert "Agent 0 via Google Reverse (RANGE_INTERPOLATED)" in meta["merge_reason"]


def test_resolve_new_address_discovery_from_agent7_detail() -> None:
    agent, provider, location_type = resolve_new_address_discovery(
        {
            "agent7_discovered": True,
            "final_provider": "google_reverse_geocode",
            "final_location_type": "ROOFTOP",
        },
        agent7_data={
            "new_address_details": [
                {
                    "provider": "google_reverse_geocode",
                    "location_type": "ROOFTOP",
                }
            ]
        },
    )
    assert agent == "agent7"
    assert provider == "google_reverse_geocode"
    assert location_type == "ROOFTOP"


def test_new_address_discovery_ui_fields_empty_for_verified_row() -> None:
    fields = new_address_discovery_ui_fields({"merge_status": "verified"})
    assert fields["new_address_discovery_source"] == ""


def test_new_address_discovery_ui_fields_for_agent0_row() -> None:
    fields = new_address_discovery_ui_fields(
        {
            "agent0_discovered": True,
            "merge_status": "new",
            "new_address_discovery_provider": "attom",
            "new_address_discovery_location_type": "ATTOM_PROPERTY",
        },
        agent0_data={"source": "attom", "location_type": "ATTOM_PROPERTY"},
    )
    assert fields["new_address_discovery_provider"] == "ATTOM"
    assert fields["new_address_discovery_location_type"] == "ATTOM_PROPERTY"
    assert "Agent 0 via ATTOM (ATTOM_PROPERTY)" in fields["new_address_discovery_source"]
