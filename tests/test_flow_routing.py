from __future__ import annotations

from data_ingestion.utils.flow_routing import (
    build_flow_routing_map,
    get_input_ids_for_agent,
    normalize_data_source,
)


def test_normalize_data_source_maps_legacy_raw_input_to_database() -> None:
    assert normalize_data_source("raw_input") == "database"
    assert normalize_data_source("database") == "database"
    assert normalize_data_source("agent2_output") == "agent2_output"


def test_database_source_uses_all_stored_address_ids() -> None:
    ids = get_input_ids_for_agent(
        "job-1",
        "agent2_geocoding",
        "raw_input",
        [1, 2, 3],
        {},
    )
    assert ids == [1, 2, 3]


def test_build_flow_routing_map_normalizes_data_source() -> None:
    routing = build_flow_routing_map({
        "agents": [
            {"agent_name": "agent2_geocoding", "data_source": "raw_input", "enabled": True},
        ]
    })
    assert routing["agent2_geocoding"]["data_source"] == "database"
