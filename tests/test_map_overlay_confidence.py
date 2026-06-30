from types import SimpleNamespace

from api_server import (
    _build_confidence_overlay,
    _coerce_confidence,
    _final_confidence_for_display,
    _final_flow_fields,
    _flow_overlay_mode,
)


def _agent_row(agent_name, address_id, data):
    return SimpleNamespace(agent_name=agent_name, address_id=address_id, data=data)


def _agent1_row(address_id, confidence_score):
    return SimpleNamespace(
        address_id=address_id,
        data=None,
        validation_status="AUTO_ACCEPT",
        canonical_address="1 Main St",
        chosen_provider="smarty",
        confidence_score=confidence_score,
    )


def test_confidence_overlay_independent_flow_uses_best_agent_score():
    flow_config = {
        "agents": [
            {"agent_name": "agent1_address_validator", "enabled": True, "data_source": "database"},
            {"agent_name": "agent4_building", "enabled": True, "data_source": "database"},
            {"agent_name": "agent5_streetview", "enabled": True, "data_source": "database"},
        ]
    }

    result = _build_confidence_overlay(
        flow_config=flow_config,
        agent_tables={},
        agent1_rows=[_agent1_row(10, 72)],
        agent_rows=[
            _agent_row("agent4_building", 10, {"confidence": 81, "structure_type": "SFD"}),
            _agent_row("agent5_streetview", 10, {"confidence": 93, "structure_type": "MDU"}),
        ],
    )

    assert result["mode"] == "independent"
    assert result["overlay"][10]["agent_name"] == "agent5_streetview"
    assert result["overlay"][10]["confidence"] == 93
    assert result["overlay"][10]["color"] == "#16a34a"


def test_confidence_overlay_chained_flow_prefers_final_agent_when_present():
    flow_config = {
        "agents": [
            {"agent_name": "agent4_building", "enabled": True, "data_source": "database"},
            {"agent_name": "agent5_streetview", "enabled": True, "data_source": "agent4_output"},
            {"agent_name": "agent6_finalization", "enabled": True, "data_source": "all_agents"},
        ]
    }

    result = _build_confidence_overlay(
        flow_config=flow_config,
        agent_tables={},
        agent1_rows=[],
        agent_rows=[
            _agent_row("agent5_streetview", 20, {"confidence": 94}),
            _agent_row("agent6_final", 20, {"final_confidence": 78, "ftth_priority": "medium"}),
        ],
    )

    assert result["mode"] == "chained"
    assert result["overlay"][20]["agent_name"] == "agent6_final"
    assert result["overlay"][20]["confidence"] == 78
    assert result["overlay"][20]["color"] == "#f59e0b"


def test_confidence_helpers_normalize_fractional_scores_and_detect_flow_mode():
    assert _coerce_confidence(0.91) == 91
    assert _coerce_confidence("82.4") == 82
    assert _flow_overlay_mode({"agents": [{"data_source": "agent1_output"}]}) == "chained"
    assert _flow_overlay_mode({"agents": [{"data_source": "database"}]}) == "independent"


def test_agent5_final_confidence_uses_house_number_confidence_even_when_zero():
    final = {"source_agent": "agent6_final", "confidence": 91}
    meta = {"final_source_agent": "agent6_final", "final_confidence": 91, "agent5_confidence": 0}
    a5 = {"confidence": 91, "house_number_conf": 0}

    assert _final_confidence_for_display(final, meta, a5) == 0
    assert _final_flow_fields({"final_resolution": final, **meta})["final_confidence"] == 0


def test_final_flow_fields_derives_provider_from_winning_agent():
    assert _final_flow_fields(
        {"final_source_agent": "agent1_address_validator"},
        agent1_provider="smarty",
    )["final_provider"] == "Smarty"
    assert _final_flow_fields(
        {"final_source_agent": "agent2_geocoding"},
        agent2_provider="REVERSE_GEOCODER",
    )["final_provider"] == "Reverse Geocoder"
    assert _final_flow_fields(
        {"final_source_agent": "agent3_parcel"},
        agent3_provider="regrid",
    )["final_provider"] == "Regrid"


def test_final_flow_fields_prefers_persisted_provider():
    fields = _final_flow_fields({
        "final_resolution": {
            "source_agent": "agent3_parcel",
            "provider": "census",
        },
        "agent3_source": "regrid",
    })
    assert fields["final_provider"] == "Census TIGER"


def test_final_flow_fields_copies_agent4_structure_values():
    fields = _final_flow_fields(
        {},
        agent4_structure_type="MDU_SMALL",
        agent4_confidence=0,
    )
    assert fields["final_structure_type"] == "MDU_SMALL"
    assert fields["final_structure_confidence"] == 0


def test_final_flow_fields_uses_persisted_agent7_structure_status():
    fields = _final_flow_fields({
        "final_structure_type": "UNKNOWN",
        "final_structure_confidence": 0,
    })
    assert fields["final_structure_type"] == "UNKNOWN"
    assert fields["final_structure_confidence"] == 0


def test_agent5_overlay_prefers_house_number_confidence_over_image_confidence():
    flow_config = {
        "agents": [
            {"agent_name": "agent5_streetview", "enabled": True, "data_source": "database"},
        ]
    }

    result = _build_confidence_overlay(
        flow_config=flow_config,
        agent_tables={},
        agent1_rows=[],
        agent_rows=[
            _agent_row("agent5_streetview", 30, {"confidence": 91, "house_number_conf": 0}),
        ],
    )

    assert result["overlay"][30]["confidence"] == 0
