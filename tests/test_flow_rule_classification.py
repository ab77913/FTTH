from data_ingestion.agents.pipeline_runner import (
    _address_validation_exact_warning_found,
    _agent2_confirmed_not_found,
    _agent2_exact_address_found,
    _agent2_failed_validation,
    _flow_rule_status,
    _rule_status_from_merge_status,
)


def test_flow_rule_status_verifies_first_agent_match_above_threshold():
    status, color, reason = _flow_rule_status(
        {
            "routing_history": [
                {
                    "source_agent": "agent0_reverse_geocoder",
                    "status": "MATCH",
                    "confidence": 92,
                    "threshold": 90,
                }
            ],
            "final_resolution": {
                "source_agent": "agent0_reverse_geocoder",
                "status": "MATCH",
                "confidence": 92,
                "threshold": 90,
            },
        },
        92,
    )

    assert status == "verified"
    assert color == "green"
    assert "MATCH above threshold" in reason


def test_flow_rule_status_invalidates_final_mismatch_even_with_high_confidence():
    status, color, reason = _flow_rule_status(
        {
            "routing_history": [
                {
                    "source_agent": "agent0_reverse_geocoder",
                    "status": "MISMATCH",
                    "confidence": 95,
                    "threshold": 90,
                    "action": "continue",
                }
            ],
            "final_resolution": {
                "source_agent": "agent5_streetview",
                "status": "MISMATCH",
                "confidence": 94,
                "threshold": 90,
            },
        },
        94,
    )

    assert status == "invalid"
    assert color == "red"
    assert "MISMATCH" in reason


def test_flow_rule_status_does_not_verify_first_agent_below_threshold():
    status, color, reason = _flow_rule_status(
        {
            "routing_history": [
                {
                    "source_agent": "agent0_reverse_geocoder",
                    "status": "MATCH",
                    "confidence": 65,
                    "threshold": 90,
                }
            ],
        },
        65,
    )

    assert status == "invalid"
    assert color == "red"
    assert "No selected flow agent" in reason


def test_rule_status_contract_maps_merge_status_to_explicit_output_values():
    assert _rule_status_from_merge_status("verified") == "valid"
    assert _rule_status_from_merge_status("invalid") == "invalid"
    assert _rule_status_from_merge_status("duplicate") == "duplicate"
    assert _rule_status_from_merge_status("new") == "new"
    assert _rule_status_from_merge_status("excluded") == "excluded"
    assert _rule_status_from_merge_status("unexpected") == "invalid"


def test_flow_rule_status_keeps_sticky_duplicate_white():
    status, color, reason = _flow_rule_status(
        {
            "record_status": "DUPLICATE",
            "address_validation": {"match_status": "MATCH"},
            "final_resolution": {"status": "AUTO_ACCEPT", "confidence": 100},
        },
        100,
    )

    assert status == "duplicate"
    assert color == "white"
    assert "duplicate" in reason.lower()


def test_address_validation_exact_warning_is_found():
    found, reason, confidence, distance = _address_validation_exact_warning_found({
        "address_validation": {
            "match_status": "MISMATCH_WARN",
            "selected_provider": "GOOGLE_REVERSE",
            "address_match_percent": 100,
            "reverse_address_match_percent": 100,
            "forward_address_match_percent": 100,
            "confidence_score": 35,
            "distance_m": 191.9,
        }
    })

    assert found is True
    assert confidence == 35
    assert distance == 191.9
    assert "coordinate warning" in reason


def test_address_validation_warning_without_exact_match_is_not_found():
    found, _, _, _ = _address_validation_exact_warning_found({
        "address_validation": {
            "match_status": "MISMATCH_WARN",
            "address_match_percent": 40,
            "reverse_address_match_percent": 40,
            "forward_address_match_percent": 40,
            "confidence_score": 35,
            "distance_m": 191.9,
        }
    })

    assert found is False


def test_agent2_exact_rooftop_address_found_even_when_pin_is_wrong():
    found, reason, confidence, distance = _agent2_exact_address_found({
        "geocoding": {
            "source": "GOOGLE",
            "location_type": "ROOFTOP",
            "address_accepted": True,
            "address_match_percent": 100,
            "confidence": 83,
            "coord_distance_m": 818.8,
            "latitude": 31.279083,
            "longitude": -102.6944336,
        }
    })

    assert found is True
    assert confidence == 83
    assert distance == 818.8
    assert "supplied coordinates appear incorrect" in reason


def test_agent2_far_range_interpolated_address_fails_validation():
    found, reason, confidence, distance = _agent2_exact_address_found({
        "geocoding": {
            "source": "GOOGLE",
            "location_type": "RANGE_INTERPOLATED",
            "address_accepted": True,
            "address_match_percent": 100,
            "confidence": 35,
            "coord_distance_m": 354.0,
            "latitude": 31.2693193,
            "longitude": -102.6872589,
        }
    })

    assert found is False
    failed, reason, confidence, distance = _agent2_failed_validation({
        "geocoding": {
            "source": "GOOGLE",
            "location_type": "RANGE_INTERPOLATED",
            "ok": True,
            "address_accepted": True,
            "address_match_percent": 100,
            "confidence": 35,
            "coord_distance_m": 354.0,
            "latitude": 31.2693193,
            "longitude": -102.6872589,
        }
    })
    assert failed is True
    assert confidence == 35
    assert distance == 354.0
    assert "RANGE_INTERPOLATED" in reason


def test_agent2_far_geometric_center_address_fails_validation():
    found, reason, confidence, distance = _agent2_exact_address_found({
        "geocoding": {
            "source": "GOOGLE",
            "location_type": "GEOMETRIC_CENTER",
            "address_accepted": True,
            "address_match_percent": 100,
            "confidence": 35,
            "coord_distance_m": 672.7,
            "latitude": 31.2755942,
            "longitude": -102.6944989,
        }
    })

    assert found is False
    failed, reason, confidence, distance = _agent2_failed_validation({
        "geocoding": {
            "source": "GOOGLE",
            "location_type": "GEOMETRIC_CENTER",
            "ok": True,
            "address_accepted": True,
            "address_match_percent": 100,
            "confidence": 35,
            "coord_distance_m": 672.7,
            "latitude": 31.2755942,
            "longitude": -102.6944989,
        }
    })
    assert failed is True
    assert confidence == 35
    assert distance == 672.7
    assert "GEOMETRIC_CENTER" in reason


def test_agent2_near_exact_range_interpolated_address_fails_validation():
    found, reason, confidence, distance = _agent2_exact_address_found({
        "geocoding": {
            "source": "GOOGLE",
            "location_type": "RANGE_INTERPOLATED",
            "address_accepted": True,
            "address_match_percent": 100,
            "confidence": 35,
            "coord_distance_m": 12.0,
            "latitude": 31.2743807,
            "longitude": -102.6919593,
        }
    })

    assert found is False
    failed, reason, confidence, distance = _agent2_failed_validation({
        "geocoding": {
            "source": "GOOGLE",
            "location_type": "RANGE_INTERPOLATED",
            "ok": True,
            "address_accepted": True,
            "address_match_percent": 100,
            "confidence": 35,
            "coord_distance_m": 12.0,
            "latitude": 31.2743807,
            "longitude": -102.6919593,
        }
    })
    assert failed is True
    assert confidence == 35
    assert distance == 12.0
    assert "RANGE_INTERPOLATED" in reason


def test_agent2_accepted_query_alias_range_interpolated_fails_validation():
    found, reason, confidence, distance = _agent2_exact_address_found({
        "geocoding": {
            "source": "GOOGLE",
            "location_type": "RANGE_INTERPOLATED",
            "address_accepted": True,
            "address_match_percent": 100,
            "confidence": 35,
            "coord_distance_m": 7.0,
            "latitude": 31.2757196,
            "longitude": -102.6988862,
            "query_alias": "209 S COOLEDGE ST, IMPERIAL, TX, 79743",
        }
    })

    assert found is False
    failed, reason, confidence, distance = _agent2_failed_validation({
        "geocoding": {
            "source": "GOOGLE",
            "location_type": "RANGE_INTERPOLATED",
            "ok": True,
            "address_accepted": True,
            "address_match_percent": 100,
            "confidence": 35,
            "coord_distance_m": 7.0,
            "latitude": 31.2757196,
            "longitude": -102.6988862,
            "query_alias": "209 S COOLEDGE ST, IMPERIAL, TX, 79743",
        }
    })
    assert failed is True
    assert confidence == 35
    assert distance == 7.0
    assert "RANGE_INTERPOLATED" in reason


def test_agent2_zero_results_is_confirmed_not_found():
    not_found, reason, confidence, distance = _agent2_confirmed_not_found({
        "geocoding": {
            "source": "GOOGLE",
            "ok": False,
            "google_status": "ZERO_RESULTS",
            "zero_results": True,
            "confidence": 0,
        }
    })

    assert not_found is True
    assert confidence == 0
    assert distance is None
    assert "not found" in reason.lower()
