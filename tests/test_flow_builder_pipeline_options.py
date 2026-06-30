from __future__ import annotations

from data_ingestion.utils.flow_routing import build_flow_routing_map, get_agents_in_execution_order
from data_ingestion.utils.pipeline_options import apply_flow_config_to_pipeline_options


def test_apply_flow_config_uses_nested_pipeline_sub_options() -> None:
    flow_config = {
        "name": "Custom Flow",
        "agents": [
            {
                "agent_name": "agent1_address_validator",
                "enabled": True,
                "pipeline_options": {
                    "smarty": False,
                    "melissa": True,
                    "use_cache": False,
                },
            },
            {
                "agent_name": "agent2_geocoding",
                "enabled": True,
                "pipeline_options": {
                    "reverse_geocoder": True,
                    "coord_validation": False,
                    "google_geocoding": False,
                    "osm_geocoding": True,
                    "street_interpolation": False,
                },
            },
        ],
    }

    opts = apply_flow_config_to_pipeline_options(None, flow_config)

    assert opts["agent1"]["enabled"] is True
    assert opts["agent1"]["smarty"] is False
    assert opts["agent1"]["melissa"] is True
    assert opts["agent1"]["use_cache"] is False

    assert opts["agent2"]["enabled"] is True
    assert opts["agent2"]["reverse_geocoder"] is True
    assert opts["agent2"]["coord_validation"] is False
    assert opts["agent2"]["google_geocoding"] is False
    assert opts["agent2"]["osm_geocoding"] is True
    assert opts["agent2"]["street_interpolation"] is False


def test_apply_flow_config_disables_agent_when_flow_disables_it() -> None:
    flow_config = {
        "name": "A2 only",
        "agents": [
            {
                "agent_name": "agent1_address_validator",
                "enabled": False,
                "pipeline_options": {
                    "smarty": True,
                    "melissa": True,
                },
            }
        ],
    }

    opts = apply_flow_config_to_pipeline_options(None, flow_config)

    assert opts["agent1"]["enabled"] is False


def test_apply_flow_config_keeps_building_provider_and_geocode_distance_thresholds() -> None:
    flow_config = {
        "name": "Provider and distance controls",
        "agents": [
            {
                "agent_name": "agent2_geocoding",
                "enabled": True,
                "pipeline_options": {
                    "coord_match_threshold_m": 80,
                    "coord_mismatch_warn_m": 350,
                },
            },
            {
                "agent_name": "agent4_building",
                "enabled": True,
                "pipeline_options": {
                    "building_footprint": True,
                    "building_provider": "attom",
                },
            },
        ],
    }

    opts = apply_flow_config_to_pipeline_options(None, flow_config)

    assert opts["agent2"]["coord_match_threshold_m"] == 80
    assert opts["agent2"]["coord_mismatch_warn_m"] == 350
    assert opts["agent4"]["building_provider"] == "attom"


def test_apply_flow_config_supports_agent50_offline_options() -> None:
    flow_config = {
        "name": "Offline OCR",
        "agents": [
            {
                "agent_name": "agent5_0_offline_ocr",
                "enabled": True,
                "pipeline_options": {
                    "street_view": True,
                    "paddle_ocr": True,
                    "ocr_engine": "vision_only",
                    "ollama_guidance": False,
                    "max_workers": 6,
                    "max_iterations": 4,
                    "confidence_gate": 88,
                },
            },
        ],
    }

    opts = apply_flow_config_to_pipeline_options(None, flow_config)

    assert opts["agent5_0"]["enabled"] is True
    assert opts["agent5_0"]["street_view"] is True
    assert opts["agent5_0"]["paddle_ocr"] is True
    assert opts["agent5_0"]["ocr_engine"] == "vision_only"
    assert opts["agent5_0"]["ollama_guidance"] is False
    assert opts["agent5_0"]["max_workers"] == 6
    assert opts["agent5_0"]["max_iterations"] == 4
    assert opts["agent5_0"]["confidence_gate"] == 88


def test_apply_flow_config_accepts_legacy_agent1_agent2_aliases() -> None:
    flow_config = {
        "agents": [
            {
                "agent_name": "agent1_geocoding",
                "enabled": True,
                "pipeline_options": {"google_geocoding": False, "osm_geocoding": True},
            },
            {
                "agent_name": "agent2_address_validation",
                "enabled": True,
                "pipeline_options": {"smarty": True, "melissa": False},
            },
        ],
    }

    opts = apply_flow_config_to_pipeline_options(None, flow_config)

    assert opts["agent2"]["enabled"] is True
    assert opts["agent2"]["google_geocoding"] is False
    assert opts["agent2"]["osm_geocoding"] is True
    assert opts["agent1"]["enabled"] is True
    assert opts["agent1"]["smarty"] is True
    assert opts["agent1"]["melissa"] is False


def test_enabled_validation_agents_keep_at_least_one_provider_or_mode() -> None:
    flow_config = {
        "agents": [
            {
                "agent_name": "agent1_address_validator",
                "enabled": True,
                "pipeline_options": {"smarty": False, "melissa": False, "use_cache": True},
            },
            {
                "agent_name": "agent2_geocoding",
                "enabled": True,
                "pipeline_options": {
                    "reverse_geocoder": False,
                    "coord_validation": False,
                    "google_geocoding": False,
                    "osm_geocoding": False,
                    "street_interpolation": False,
                },
            },
        ],
    }

    opts = apply_flow_config_to_pipeline_options(None, flow_config)

    assert opts["agent1"]["smarty"] is True
    assert opts["agent2"]["google_geocoding"] is True


def test_flow_routing_canonicalizes_agent_aliases_for_thresholds() -> None:
    flow_config = {
        "agents": [
            {
                "agent_name": "agent1_geocoding",
                "enabled": True,
                "order": 2,
                "confidence_thresholds": {"threshold_1": 91},
            },
            {
                "agent_name": "agent2_address_validation",
                "enabled": True,
                "order": 3,
                "confidence_thresholds": {"threshold_1": 92},
            },
        ],
    }

    routing = build_flow_routing_map(flow_config)

    assert routing["agent2_geocoding"]["threshold_1"] == 91
    assert routing["agent1_address_validator"]["threshold_1"] == 92
    assert get_agents_in_execution_order(flow_config) == ["agent2_geocoding", "agent1_address_validator"]
